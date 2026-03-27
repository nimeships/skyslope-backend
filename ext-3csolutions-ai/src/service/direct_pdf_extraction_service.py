# Direct Document Extraction Service - Sends documents/images directly to Claude (No Textract)

import os
import json
import re
import hashlib
import traceback
from datetime import datetime, timezone
from src.adapter.s3_adapter import S3Adapter
from src.adapter.bedrock_adapter import BedrockAdapter, extract_json_from_codeblock, remove_blank_fields
from src.adapter.dynamodb_adapter import DynamoDBAdapter
from src.utils.config import PipelineConfig
from src.utils.logger import get_logger, set_request_context
from src.utils.constants import (
    STAGE_EXTRACTION, STATUS_IN_PROGRESS, STATUS_SUCCESS
)

logger = get_logger(__name__)

AGGREGATION_CACHE_VERSION = os.getenv("AGGREGATION_CACHE_VERSION", "v2")

PARTY_FIELD_GROUP_FALLBACK_POLICIES = (
    {
        "name": "outside_customer_listing_amendment_fallback",
        "left_prefix": "Customer",
        "right_prefix": "OutsideCustomer",
        "fallback_doc_types": {"listing_amendment"},
        "required_batch_context": {
            "has_buyer_agreement": True,
            "has_listing_agreement": False,
        },
        "right_expected_doc_types": {
            "residential_contract_of_purchase",
            "residential_sales_contract",
        },
        "trigger": "missing_or_identity_match",
    },
)

PARTY_IDENTITY_GUARD_POLICIES = (
    {
        "name": "customer_vs_outside_customer",
        "left_prefix": "Customer",
        "right_prefix": "OutsideCustomer",
    },
)

# Maps each extractable field to its expected source document type(s).
# A list means the field is accepted from multiple document types (Bright MLS takes priority for address fields).
FIELD_TO_DOC_TYPE = {
    # Exclusive Right to Sell Listing Agreement / Buyer Agreement
    "Property_Address1":          ["listing_agreement", "bright_mls"],
    "Property_City":              ["listing_agreement", "bright_mls"],
    "Property_State":             ["listing_agreement", "bright_mls"],
    "Property_Zip":               ["listing_agreement", "bright_mls"],
    "Customer_FirstName":         ["listing_agreement", "buyer_agreement"],
    "Customer_LastName":          ["listing_agreement", "buyer_agreement"],
    "Customer_Email":             ["listing_agreement", "buyer_agreement"],
    "ListPrice":                  "listing_agreement",
    "CommissionPercent":          ["listing_agreement", "listing_amendment"],
    "AdminFeeAmt":                "listing_agreement",
    "GrossOfficeCommission":      "listing_agreement",
    "GrossCommission":            "listing_agreement",
    "GrossCommissionAmount":      "listing_agreement",
    "CommissionAmount":           "listing_agreement",
    # Residential Contract of Purchase / Residential Sales Contract
    "OutsideCustomer_FirstName":  ["residential_contract_of_purchase", "residential_sales_contract"],
    "OutsideCustomer_LastName":   ["residential_contract_of_purchase", "residential_sales_contract"],
    "OutsideCustomer_Email":      ["residential_contract_of_purchase", "residential_sales_contract"],
    "ContractRatificationDate":   ["residential_contract_of_purchase", "residential_sales_contract"],
    "BasePrice":                  ["residential_contract_of_purchase", "residential_sales_contract"],
    # Residential Sales Contract
    "SettlementDate":             ["residential_sales_contract", "listing_amendment"],
    "SalePrice":                  "residential_sales_contract",
    "FirstTrustAmt":              "residential_sales_contract",
    "EarnestAmount":              "residential_sales_contract",
    # Agent Full / MRIS Full Listing
    "MLSno":                      "agent_full",
    "DateEnteredMLS":             "agent_full",
    "MLSExpirationDate":          ["agent_full", "listing_agreement", "listing_amendment"],
    "OriginalListPrice":          ["agent_full", "listing_agreement", "bright_mls"],
    # Listing Amendment / Change of Status / Extension
    "XREF1Percent":               "listing_agreement",
    "XREF1Amount":                "listing_agreement",
    # Bright MLS
    "Property_County":            "bright_mls",
}

# Keyword patterns used to identify document type from filename
DOC_TYPE_FILENAME_PATTERNS = {
    "listing_agreement":                ["listing", "exclusive", "erts", "nvar"],
    "buyer_agreement":                  [
        "buyer agreement", "buyer_agreement", "buyeragreement", "buyer-agreement",
        "buyer agency agreement", "buyer_agency_agreement", "buyer-agency-agreement"
    ],
    "residential_contract_of_purchase": ["contract of purchase", "rcp", "contract_of_purchase"],
    "residential_sales_contract":       ["sales contract", "k1117", "sales_contract"],
    "agent_full":                       ["agent full", "agent_full", "agentfull", "mris", "full listing", "full_listing", "copy of mris"],
    "listing_amendment":                ["cos_extension", "extension", "change of status", "change_of_status", "amendment", "addendum"],
    "bright_mls":                       ["bright mls", "bright_mls", "brightmls", "bright-mls"],
}


class DirectPDFExtractionService:
    """
    Direct document extraction service that sends files directly to Claude.

    Accepted file types:
    - PDF documents (native Claude support)
    - Images (PNG, JPEG, GIF, WebP, TIFF, BMP) - via Claude's vision API

    Skips Textract OCR - faster and supports images natively.
    """

    _DATE_PATTERNS = (
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{1,2}/\d{1,2}/\d{4}\b",
        r"\b\d{1,2}-\d{1,2}-\d{4}\b",
    )
    _VALUE_POSITIVE_HINTS = (
        "replace", "replaced", "replacement", "updated", "update", "new",
        "corrected", "correction", "final", "amendment", "addendum", "annotation",
        "stamp", "above", "beside", "inserted", "overwritten", "written above"
    )
    _VALUE_INVALID_HINTS = (
        "crossed out", "crossed-out", "struck", "strikethrough", "strike through",
        "void", "invalid", "deleted", "removed", "lined through", "line-through"
    )

    def __init__(self, config: PipelineConfig):
        self.s3 = S3Adapter(config)
        self.bedrock = BedrockAdapter(config)
        self.dynamodb = DynamoDBAdapter(config)

    def detect_batch_context(self, file_keys: list) -> dict:
        """Pre-scan all filenames in the batch to detect which document types are present."""
        detected_types = set()
        for key in file_keys:
            filename = os.path.basename(key)
            doc_type = self.detect_doc_type(filename)
            if doc_type != "unknown":
                detected_types.add(doc_type)
        return {
            "has_buyer_agreement": "buyer_agreement" in detected_types,
            "has_listing_agreement": "listing_agreement" in detected_types,
            "detected_types": sorted(list(detected_types)),
        }

    def _build_cache_salt(self, schema_json, batch_context):
        schema_hash = hashlib.sha256(
            json.dumps(schema_json, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        context_hash = hashlib.sha256(
            json.dumps(batch_context or {}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        prompt_hash = hashlib.sha256(
            self.build_system_instructions(schema_json, batch_context=batch_context).encode("utf-8")
        ).hexdigest()
        return "|".join([
            AGGREGATION_CACHE_VERSION,
            self.bedrock.model_id,
            schema_hash,
            context_hash,
            prompt_hash,
        ])

    @staticmethod
    def _normalize_party_value(value):
        if value is None:
            return ""
        normalized = re.sub(r"\s+", " ", str(value).strip().lower())
        return normalized

    @staticmethod
    def _normalize_email(value):
        return DirectPDFExtractionService._normalize_party_value(value)

    @staticmethod
    def _normalize_name(first_name, last_name):
        full_name = " ".join(part for part in [first_name, last_name] if part)
        return DirectPDFExtractionService._normalize_party_value(full_name)

    @staticmethod
    def _field_prefix(prefix: str) -> str:
        return f"{prefix}_"

    @staticmethod
    def _get_prefixed_fields(row: dict, prefix: str):
        token = DirectPDFExtractionService._field_prefix(prefix)
        return [field for field in row.keys() if field.startswith(token)]

    @staticmethod
    def _batch_context_matches(batch_context: dict, required_context: dict) -> bool:
        if not required_context:
            return True
        if not batch_context:
            return False
        return all(batch_context.get(key) == expected for key, expected in required_context.items())

    @staticmethod
    def _is_null_like(value) -> bool:
        return value in ("", None, [], {})

    @staticmethod
    def _has_party_identity_match(
        row: dict,
        left_prefix: str = "Customer",
        right_prefix: str = "OutsideCustomer",
    ) -> bool:
        left_name = DirectPDFExtractionService._normalize_name(
            row.get(f"{left_prefix}_FirstName"),
            row.get(f"{left_prefix}_LastName"),
        )
        right_name = DirectPDFExtractionService._normalize_name(
            row.get(f"{right_prefix}_FirstName"),
            row.get(f"{right_prefix}_LastName"),
        )
        left_email = DirectPDFExtractionService._normalize_email(row.get(f"{left_prefix}_Email"))
        right_email = DirectPDFExtractionService._normalize_email(row.get(f"{right_prefix}_Email"))

        name_match = bool(left_name and right_name and left_name == right_name)
        email_match = bool(left_email and right_email and left_email == right_email)
        return name_match or email_match

    def _should_allow_party_group_fallback(self, target_row: dict, batch_context: dict, policy: dict) -> bool:
        if not self._batch_context_matches(batch_context, policy.get("required_batch_context", {})):
            return False

        right_prefix = policy["right_prefix"]
        left_prefix = policy["left_prefix"]
        right_fields = self._get_prefixed_fields(target_row, right_prefix)
        if not right_fields:
            return False

        trigger = policy.get("trigger", "missing_or_identity_match")
        has_any_right_value = any(
            not self._is_null_like(target_row.get(field))
            for field in right_fields
        )

        if trigger == "missing_or_identity_match":
            if not has_any_right_value:
                return True
            return self._has_party_identity_match(
                target_row,
                left_prefix=left_prefix,
                right_prefix=right_prefix,
            )

        return False

    def _select_party_group_fallback_policy(
        self,
        field: str,
        expected_doc_type,
        detected_doc_type: str,
        target_row: dict,
        batch_context: dict,
    ):
        expected_types = set(expected_doc_type if isinstance(expected_doc_type, list) else [expected_doc_type])

        for policy in PARTY_FIELD_GROUP_FALLBACK_POLICIES:
            right_prefix = policy["right_prefix"]
            if not field.startswith(self._field_prefix(right_prefix)):
                continue
            if detected_doc_type not in policy.get("fallback_doc_types", set()):
                continue

            required_expected = policy.get("right_expected_doc_types")
            if required_expected and expected_types and not expected_types.intersection(required_expected):
                continue

            if self._should_allow_party_group_fallback(target_row, batch_context, policy):
                return policy

        return None

    @staticmethod
    def _normalize_percentage(value):
        if value in ("", None, [], {}):
            return value

        text = str(value).strip().replace("%", "")
        try:
            numeric = float(text)
        except (TypeError, ValueError):
            return value

        # Convert decimal representation to percentage points (e.g. 0.025 -> 2.5)
        if 0 < numeric < 1:
            numeric = numeric * 100

        if numeric < 0 or numeric > 100:
            return value

        normalized = round(numeric, 3)
        if normalized.is_integer():
            return int(normalized)
        return normalized

    @staticmethod
    def _normalize_date_value(value):
        if value in ("", None, [], {}):
            return value

        raw = str(value).strip()

        candidates = []
        for pattern in DirectPDFExtractionService._DATE_PATTERNS:
            candidates.extend(re.findall(pattern, raw))

        if not candidates:
            return value

        # Prefer the last detected date token when multiple appear in a single value.
        selected = candidates[-1]
        formats = ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y")
        for fmt in formats:
            try:
                parsed = datetime.strptime(selected, fmt)
                return parsed.strftime("%Y-%m-%d")
            except ValueError:
                continue
        return value

    @staticmethod
    def _build_date_candidates(raw_value: str):
        """Extract date tokens and attach context score for corrected/final-value resolution."""
        if not raw_value:
            return []

        candidates = []
        lowered = raw_value.lower()
        combined_pattern = "|".join(DirectPDFExtractionService._DATE_PATTERNS)
        for idx, match in enumerate(re.finditer(combined_pattern, raw_value)):
            token = match.group(0)
            window_start = max(0, match.start() - 50)
            window_end = min(len(raw_value), match.end() + 50)
            context = lowered[window_start:window_end]

            score = 0
            if any(hint in context for hint in DirectPDFExtractionService._VALUE_POSITIVE_HINTS):
                score += 4
            if any(hint in context for hint in DirectPDFExtractionService._VALUE_INVALID_HINTS):
                score -= 4
            score -= idx * 0.5  # Prefer earlier/top-most candidate when no correction cues exist.

            candidates.append({
                "token": token,
                "score": score,
                "context": context,
                "index": idx,
            })
        return candidates

    def _resolve_date_value(self, field: str, value, request_id=None, source_file=None):
        if value in ("", None, [], {}):
            return value

        raw = str(value).strip()
        candidates = self._build_date_candidates(raw)
        if not candidates:
            return value

        has_invalid_marker = any(hint in raw.lower() for hint in self._VALUE_INVALID_HINTS)
        selected = max(candidates, key=lambda item: (item["score"], -item["index"]))
        strict_date_fields = {"SettlementDate", "ContractRatificationDate", "MLSExpirationDate"}

        has_positive_hint = any(hint in raw.lower() for hint in self._VALUE_POSITIVE_HINTS)

        if not has_positive_hint and len(candidates) >= 2:
            if field in strict_date_fields:
                logger.warning(
                    "Date field rejected: multiple candidates without correction signal",
                    extra={
                        'request_id': request_id,
                        'field': field,
                        'source_file': source_file,
                        'raw_value': raw,
                        'reason': 'ambiguous_multiple_candidates'
                    }
                )
                return None
            selected = candidates[0]

        # If all detected candidates appear invalidated and no replacement signal exists, return null.
        if field in strict_date_fields and has_invalid_marker and not has_positive_hint and len(candidates) == 1:
            logger.warning(
                "Date field rejected: only invalidated candidate detected",
                extra={
                    'request_id': request_id,
                    'field': field,
                    'source_file': source_file,
                    'raw_value': raw,
                    'reason': 'only_invalidated_candidate'
                }
            )
            return None

        resolved = self._normalize_date_value(selected["token"])
        if field == "SettlementDate" and len(candidates) > 1:
            logger.info(
                "SettlementDate resolved from multiple candidates",
                extra={
                    'request_id': request_id,
                    'field': field,
                    'source_file': source_file,
                    'selected_value': resolved,
                    'selected_score': selected['score'],
                    'candidate_count': len(candidates),
                }
            )
        return resolved

    def resolve_field_value(self, field: str, value, request_id=None, source_file=None):
        """Apply deterministic value resolution before merge assignment."""
        if field in ("SettlementDate", "ContractRatificationDate", "MLSExpirationDate"):
            return self._resolve_date_value(field, value, request_id=request_id, source_file=source_file)
        if field == "CommissionPercent":
            return self._normalize_percentage(value)
        return value

    def apply_cross_field_guards(self, final_agg, sources_agg, request_id=None):
        """Resolve cross-field conflicts that the model cannot reliably infer from text alone."""
        for table_name, rows in final_agg.items():
            if table_name == "_sources" or not rows:
                continue

            row = rows[0]
            for guard_policy in PARTY_IDENTITY_GUARD_POLICIES:
                left_prefix = guard_policy["left_prefix"]
                right_prefix = guard_policy["right_prefix"]
                right_fields = self._get_prefixed_fields(row, right_prefix)
                if not right_fields:
                    continue

                has_right_values = any(
                    not self._is_null_like(row.get(field))
                    for field in right_fields
                )
                if not has_right_values:
                    continue

                if not self._has_party_identity_match(
                    row,
                    left_prefix=left_prefix,
                    right_prefix=right_prefix,
                ):
                    continue

                left_fields = self._get_prefixed_fields(row, left_prefix)
                observed_values = {
                    field: row.get(field)
                    for field in [*left_fields, *right_fields]
                }
                observed_sources = {
                    field: (sources_agg.get(table_name) or [{}])[0].get(field)
                    for field in right_fields
                }

                for field in right_fields:
                    row[field] = None
                    if sources_agg.get(table_name) and sources_agg[table_name]:
                        sources_agg[table_name][0].pop(field, None)

                logger.warning(
                    "Party identity guard cleared fallback-side fields",
                    extra={
                        'request_id': request_id,
                        'table': table_name,
                        'guard_policy': guard_policy.get("name"),
                        'left_prefix': left_prefix,
                        'right_prefix': right_prefix,
                        'reason': 'duplicate_party_identity',
                        'observed_values': observed_values,
                        'observed_sources': observed_sources,
                    }
                )

    def build_system_instructions(self, schema_json, batch_context: dict = None):
        scenario_hint = ""
        if batch_context:
            if batch_context.get("has_buyer_agreement") and not batch_context.get("has_listing_agreement"):
                scenario_hint = """
<BATCH-SCENARIO-HINT>
Detected batch context: BUYER AGREEMENT package.
Apply role mapping strictly:
- Customer = BUYER
- OutsideCustomer = SELLER
</BATCH-SCENARIO-HINT>
"""
            elif batch_context.get("has_listing_agreement") and not batch_context.get("has_buyer_agreement"):
                scenario_hint = """
<BATCH-SCENARIO-HINT>
Detected batch context: LISTING AGREEMENT package.
Apply role mapping strictly:
- Customer = SELLER
- OutsideCustomer = BUYER
</BATCH-SCENARIO-HINT>
"""

        return f"""<SYSTEM_INSTRUCTIONS>
REAL ESTATE DOCUMENT EXTRACTION PIPELINE
=======================================
You are an expert real estate document analyst with 20+ years experience processing Virginia/NVAR forms.
Your job: Extract ONLY the specified fields from the provided document text into the EXACT JSON schema below.

{scenario_hint}


CRITICAL CONSTRAINTS (MANDATORY):
1. Output ONLY valid JSON matching schema EXACTLY. ZERO other text.
2. 95%+ CONFIDENCE REQUIRED for every field. Omit uncertain data.
3. Handle Textract LINE quirks: fragmented text, multi-line addresses, tables.
4. Multiple documents per page → create separate JSON objects in arrays.
5. Normalize ALL data: dates=YYYY-MM-DD, $1,250→1250.00, addresses=1 line.
6. DOCUMENT SOURCE STRICT: Each field MUST be extracted ONLY from its designated document type listed below. Even if the same data appears in another document, DO NOT use it — leave the field null.
7. NO EXTRA FIELDS: Do NOT populate any field that is not explicitly listed in the DOCUMENT-TO-FIELD-MAPPING below. Every unlisted field MUST be null, no exceptions.

<GENERAL-VALUE-RESOLUTION-RULES>
When extracting any field value from the document, follow these rules:
1. Anchor Preference: Always extract values closest to the field label or semantic anchor.
2. Multiple Candidate Handling:
    - If multiple candidate values exist for the same field, prefer the most recent/corrected/final value.
    - Prefer values that appear above, beside, or in annotation/stamp areas when they represent corrections.
    - Prefer values that are clearly readable and not visually invalidated.
3. Correction & Strike-through Handling:
    - If a value is crossed out, struck through, or marked invalid, IGNORE it.
    - If a replacement value is written nearby (above, beside, or in annotation), ALWAYS choose the replacement value.
    - This correction rule applies to ALL fields, not just one specific field.
    - If one value is struck through and another value is written above/beside, NEVER return the struck-through value.
    - If strike-through is present but replacement value is unreadable/uncertain, return null for that field.
4. Amendment / Addendum Priority:
    - If a value appears in amendment/addendum/correction sections, it OVERRIDES earlier main-document values.
5. Ignore Irrelevant Context:
    - Do not extract values from unrelated labels or nearby sections that do not match the target field.
6. Output Constraint:
    - Return only ONE final resolved value per field.
    - Do not return multiple candidates or ambiguous outputs.
Goal: Return the final, corrected, authoritative value as legally interpreted in the document.
</GENERAL-VALUE-RESOLUTION-RULES>


<DOCUMENT-PRIORITY-ORDER>
When extracting any field, always follow this document priority order:
1. EXCLUSIVE RIGHT TO SELL LISTING AGREEMENT (HIGHEST PRIORITY — always look here first)
2. RESIDENTIAL CONTRACT OF SALE / RESIDENTIAL CONTRACT OF PURCHASE (second priority — use only if field not found in Exclusive Right to Sell)
3. AGENT FULL or BRIGHT MLS (lowest priority — use only if field not found in the above documents)
</DOCUMENT-PRIORITY-ORDER>


<FIELD-MAPPING-GUIDE>
1. EXCLUSIVE RIGHT TO SELL LISTING AGREEMENT: Look for Property Address, City, State, Zip, List Price, Seller Name, Commission Percent, Gross Commission amounts.
2. RESIDENTIAL CONTRACT OF PURCHASE: Look for Buyer Name, Buyer Email, Date of Ratification.
3. RESIDENTIAL SALES CONTRACT: Look for Settlement Date, Sale Price, "First Trust", "Deposit" amount.
4. AGENT FULL: Look for MLS listing number, Listing Entry Date, Expiration Date, Original Price.
5. BRIGHT MLS: Look for Address, City, State, Zip, County — takes PRIORITY over Listing Agreement for these fields.
</FIELD-MAPPING-GUIDE>


<STRICT-FIELD-EXTRACTION-RULES>
**IMPORTANT**: You MUST ONLY extract values for the following fields from their EXACT designated document. ALL other fields in the schema MUST be set to null. Extracting a field from the wrong document type is NOT allowed even if the value looks correct.


<DOCUMENT-TO-FIELD-MAPPING>


**BUYER AGREEMENT RULE (CRITICAL):**
- If a BUYER AGREEMENT is uploaded (instead of a Listing Agreement), the BUYER is the CUSTOMER.
  → Customer_FirstName, Customer_LastName, Customer_Email = BUYER's info from the Buyer Agreement.
  → OutsideCustomer_FirstName, OutsideCustomer_LastName, OutsideCustomer_Email = SELLER's info from the sales/purchase contract.
- MLSExpirationDate MUST NOT be extracted from a Buyer Agreement — the Buyer Agreement has its own expiration date which is NOT the MLS expiration. If no Listing Agreement or Agent Full document is present, MLSExpirationDate = null.


=== EXCLUSIVE RIGHT TO SELL LISTING AGREEMENT ===
Under "Property" section:
- Property_Address1
- Property_City
- Property_State
- Property_Zip


Under "Header/Seller Information" section (Look for the SELLER/OWNER or COMPANY name):
- Customer_FirstName (Extract the FIRST WORD of the seller/company name. Example: If company is "Shannon Perkins LLC", FirstName = "Shannon")
- Customer_LastName (Extract REMAINING WORDS of the seller/company name. Example: If company is "Shannon Perkins LLC", LastName = "Perkins LLC")
- Customer_Email (This is the PROPERTY OWNER/SELLER's email)


=== BUYER AGREEMENT ===
**When a Buyer Agreement is uploaded, NM represents the BUYER. The BUYER is the Customer.**
- Customer_FirstName (BUYER's first name)
- Customer_LastName (BUYER's last name)
- Customer_Email (BUYER's email)
**NOTE: Do NOT extract MLSExpirationDate from this document. The expiration date in a Buyer Agreement is the buyer representation term, NOT the MLS listing expiration.**


Under "LISTING PRICE" section:
- ListPrice


Under "LISTING BROKER COMPENSATION" / "BROKER'S COMPENSATION" section:
- CommissionPercent (Use BUYER BROKER COMPENSATION percentage when present, especially from any addendum:
    - PRIORITY 1: Buyer Broker Compensation Addendum / amendment value (e.g. 2.5%)
    - PRIORITY 2: Cooperating/buyer agent % from "AUTHORITY TO COOPERATE WITH OTHER BROKERS"
    - PRIORITY 3: Total broker compensation % if no buyer-broker specific value is available
    - If multiple candidates exist, ignore crossed-out/struck-through values and keep the valid replacement
    - Check checkbox-style rows and split lines where percent appears on next line
    - Do NOT multiply. Only convert decimal to percent if shown as decimal like 0.025 → 2.5)
- AdminFeeAmt (Look for broker compensation amount in this section)

**XREF REFERRAL FEE RULE (CRITICAL):**
- XREF1Percent and XREF1Amount are referral fee fields. They MUST remain null UNLESS the document contains a section or clause that EXPLICITLY uses the word "referral" or "referral fee".
- The "AUTHORITY TO COOPERATE WITH OTHER BROKERS" section is about cooperating broker/buyer agent compensation — this is NOT a referral fee. Do NOT use values from this section for XREF fields.
- XREF data will be null the vast majority of the time.
- XREF1Percent (Only populate if document explicitly mentions a referral fee percentage)
- XREF1Amount (Only populate if document explicitly mentions a referral fee dollar amount)


**CONTRACT DOCUMENT RULE: Any document whose heading contains the word "CONTRACT" (e.g., "RESIDENTIAL SALES CONTRACT", "RESIDENTIAL CONTRACT OF PURCHASE", or any similar contract document) must be treated as equally important. Extract party information according to Scenario A/B role mapping below, plus sale price, settlement date, and ratification date from ANY such contract document using the same rules below.**

=== RESIDENTIAL CONTRACT OF PURCHASE ===
(Same as Residential Sales Contract)
**IMPORTANT — TWO SCENARIOS for OutsideCustomer:**
- SCENARIO A (Listing Agreement uploaded / NM represents SELLER): OutsideCustomer = BUYER. Extract BUYER's name and email from this document.
- SCENARIO B (Buyer Agreement uploaded / NM represents BUYER): OutsideCustomer = SELLER. Extract SELLER's name and email from this document.
Identify which scenario applies by checking whether a Listing Agreement or Buyer Agreement was uploaded.

PARTY NAME EXTRACTION CONSTRAINTS (APPLIES TO BOTH SCENARIOS):
- These forms may NOT contain predefined first-name/last-name fields. Derive names from role-labeled party sections (BUYER/SELLER/OWNER/SIGNATURE blocks).
- Never assign names by visual order alone (e.g., first name on page). Always map by party role label.
- If a full legal name appears in one text run, split as:
    - FirstName = first token
    - LastName = remaining tokens
- If only one token is present and you cannot confidently split, set FirstName = null and LastName = full token.
- If multiple parties exist for the same role and schema expects one record, use the primary/first clearly labeled party for that role.

- OutsideCustomer_FirstName (SCENARIO A → BUYER's first name. SCENARIO B → SELLER's first name.)
- OutsideCustomer_LastName (SCENARIO A → BUYER's last name. SCENARIO B → SELLER's last name.)
- OutsideCustomer_Email (SCENARIO A → BUYER's email. SCENARIO B → SELLER's email.)
- CRITICAL ROLE DISAMBIGUATION: If both BUYER and SELLER names are visible in the same contract, NEVER copy BUYER identity into OutsideCustomer for SCENARIO B. In SCENARIO B, OutsideCustomer must come from SELLER party labels/signature blocks only.
- ContractRatificationDate (Look for the LAST date of signature on the contract — this is the date all parties have signed/ratified. Labels to look for: "Date of Ratification", "Date of Ratification (see DEFINITIONS)", "Acceptance Date", "Date of Acceptance". The date value may appear ON THE NEXT LINE below the label, not inline — still use it. Accept formats like m/dd/yyyy, mm/dd/yyyy, or any date format. IMPORTANT: The "Agreement Date", "Date of Agreement", or any date that represents when the agreement document itself was drafted or accepted as an agreement is NOT the ratification date — do NOT use it. If the last signature date / ratification date cannot be found, return null.)
- BasePrice (Look for "Base price", "Base price of the house", or "Base price of home" in the Sales Price breakdown or Schedule of Payments section. Extract the dollar amount. Do NOT use Total Sales Price, Lot Premium, Option Price, or Discount.)


=== RESIDENTIAL SALES CONTRACT ===
(Same as Residential Contract of Purchase)
- SettlementDate (Use anchor-based extraction for the "Project Settlement Date" label:
    - Extract the date on the same line or immediately next line after the project settlement label
    - Exclude nearby unrelated dates such as Date of Offer, Agreement Date, and Contract Date
    - If one date is crossed-out/struck-through and another date is inserted nearby, ignore the crossed-out date and use the replacement
    - The replacement may be handwritten or typed above the original line. Treat this as the final authoritative date even if it is not on the same baseline
    - Example pattern: "05/01/2026" crossed out with "04/17/2026" written above -> output "2026-04-17"
    - If an amendment/addendum later updates settlement date, use the latest valid updated value)
- SalePrice (Final sale price — look for this in the sales contract)
- FirstTrustAmt (Look for "First Trust" field and use the number/amount value next to it — if it is a percentage then do not use it)
- EarnestAmount (Look for "Deposit" field and use the deposit amount value)
- ContractRatificationDate (Look for the LAST date of signature on the contract — this is the date all parties have signed/ratified. Labels to look for: "Date of Ratification", "Date of Ratification (see DEFINITIONS)", "Acceptance Date", "Date of Acceptance". The date value may appear ON THE NEXT LINE below the label, not inline — still use it. Accept formats like m/dd/yyyy, mm/dd/yyyy, or any date format. IMPORTANT: The "Agreement Date", "Date of Agreement", or any date that represents when the agreement document itself was drafted or accepted as an agreement is NOT the ratification date — do NOT use it. If the last signature date / ratification date cannot be found, return null.)
- BasePrice (Look for "Base price", "Base price of the house", or "Base price of home" in the Sales Price breakdown or Schedule of Payments section. Extract the dollar amount. Do NOT use Total Sales Price, Lot Premium, Option Price, or Discount.)

=== AGENT FULL / MRIS FULL LISTING ===
(This document may be named "Copy of MRIS Full Listing", "Agent Full", or similar MLS printout)
- MLSno (MLS listing number — look for "MRIS No.", "MLS#", or "Listing Number")
- DateEnteredMLS (Look for "Listing Entry Date" or "Date Filed" field in the document)
- MLSExpirationDate (Look for expiration/end date in this document. PRIORITY RULE: If a LISTING AMENDMENT / CHANGE OF STATUS / EXTENSION document is also present, use the LATEST expiration date from that amendment instead. NEVER extract this from a Buyer Agreement.)
- OriginalListPrice (Look for "Original Price" or "Original List Price" under "Listing Details" section. It may appear as "Original Price: $825,000". Extract the dollar amount.)


=== LISTING AMENDMENT / CHANGE OF STATUS / EXTENSION ===
(Documents named COS_extension, amendment, addendum, or "Change of Status" that modify the original Listing Agreement)
- MLSExpirationDate (If document says "Extend the expiration date to [DATE]" or similar, use that new date. This OVERRIDES the expiration date from the original Listing Agreement or Agent Full — it is the most recent/latest expiration date.)
- SettlementDate (If amendment/addendum explicitly updates settlement date, use updated value as final SettlementDate.)
- CommissionPercent (If amendment/addendum provides Buyer Broker Compensation percentage, use that as final CommissionPercent.)


=== BRIGHT MLS ===
(Values from Bright MLS take PRIORITY over Listing Agreement for Address fields)
- Property_Address1
- Property_City
- Property_State
- Property_Zip
- Property_County


</DOCUMENT-TO-FIELD-MAPPING>


RULES SUMMARY (NON-NEGOTIABLE):
1. ANY field NOT listed above MUST be null — no exceptions.
2. ANY field listed above MUST only be populated from its designated document type — do NOT cross-fill from other documents.
3. If the designated document is not present in the input, all its fields MUST remain null.
4. You MUST include a top-level "_doc_type" field in your JSON response identifying the document type you detected from the page heading/title. Use EXACTLY one of these values:
   - "listing_agreement"               → document heading contains "EXCLUSIVE RIGHT TO SELL"
   - "buyer_agreement"                 → document heading contains "BUYER AGREEMENT"
   - "residential_contract_of_purchase"→ document heading contains "RESIDENTIAL CONTRACT OF PURCHASE"
   - "residential_sales_contract"      → document heading contains "RESIDENTIAL SALES CONTRACT"
   - "agent_full"                      → document heading contains "AGENT FULL", "MRIS", or "FULL LISTING"
   - "listing_amendment"               → document heading contains "CHANGE OF STATUS", "AMENDMENT", "ADDENDUM", or "EXTENSION"
   - "bright_mls"                      → document heading contains "BRIGHT MLS"
   - "unknown"                         → document type cannot be determined
</STRICT-FIELD-EXTRACTION-RULES>


<SCHEMA-CONSTRAINTS>
EXACT JSON structure required:
{json.dumps(schema_json, indent=2)}
</SCHEMA-CONSTRAINTS>


Output only valid JSON. No Markdown. No Explanations.
</SYSTEM_INSTRUCTIONS>"""

    def load_schema(self, schema_key):
        """Load JSON schema from S3"""
        content = self.s3.download_file(schema_key)
        return json.loads(content)

    def init_agg_from_schema(self, schema_json):
        """Initialize aggregation structure and parallel sources tracker from schema"""
        final_agg = {}
        sources_agg = {}
        for table_name, rows in schema_json.items():
            if isinstance(rows, list) and rows:
                template = {k: None for k in rows[0].keys()}
                final_agg[table_name] = [template]
                sources_agg[table_name] = [{}]
            else:
                final_agg[table_name] = []
                sources_agg[table_name] = []
        return final_agg, sources_agg

    def detect_doc_type(self, filename: str) -> str:
        """Fallback: estimate document type from filename keywords.
        NOTE: Document type is primarily detected from the page heading by Claude (_doc_type field).
        This method is only used when _doc_type is missing from the LLM response.
        """
        filename_lower = filename.lower()
        for doc_type, keywords in DOC_TYPE_FILENAME_PATTERNS.items():
            for keyword in keywords:
                if keyword in filename_lower:
                    return doc_type
        return "unknown"

    def merge_into_single_row(self, final_agg, sources_agg, llm_result, filename, request_id=None, batch_context=None):
        """Merge LLM result into final aggregation.

        Source-aware: each field is only accepted from its designated document type.
        Fields extracted from the wrong document type are rejected and logged.
        """
        if not isinstance(llm_result, dict):
            return

        # Use _doc_type returned by Claude (detected from document heading/title)
        # Fall back to filename-based detection only if Claude did not return _doc_type
        detected_doc_type = llm_result.get('_doc_type') or self.detect_doc_type(filename)

        for table_name, rows in llm_result.items():
            if table_name not in final_agg:
                continue
            if not isinstance(rows, list) or not rows:
                continue

            if not final_agg[table_name]:
                final_agg[table_name] = [{}]
                sources_agg[table_name] = [{}]

            target = final_agg[table_name][0]
            source_target = sources_agg[table_name][0]

            for row in rows:
                if not isinstance(row, dict):
                    continue

                applied_fallback_prefixes = set()
                for field, value in row.items():
                    if value in ("", None, [], {}):
                        continue

                    value = self.resolve_field_value(field, value, request_id=request_id, source_file=filename)
                    if value in ("", None, [], {}):
                        continue

                    # Source-aware check: reject field if it comes from wrong document type
                    expected_doc_type = FIELD_TO_DOC_TYPE.get(field)
                    allowed_types = expected_doc_type if isinstance(expected_doc_type, list) else [expected_doc_type]
                    fallback_policy = None
                    if expected_doc_type and detected_doc_type != "unknown" and detected_doc_type not in allowed_types:
                        fallback_policy = self._select_party_group_fallback_policy(
                            field,
                            expected_doc_type,
                            detected_doc_type,
                            target,
                            batch_context,
                        )
                        if fallback_policy:
                            right_prefix = fallback_policy["right_prefix"]
                            if right_prefix not in applied_fallback_prefixes:
                                for grouped_field in self._get_prefixed_fields(target, right_prefix):
                                    target[grouped_field] = None
                                    source_target.pop(grouped_field, None)
                                applied_fallback_prefixes.add(right_prefix)
                                logger.info(
                                    "Applying party-group fallback from disallowed source",
                                    extra={
                                        'request_id': request_id,
                                        'source_file': filename,
                                        'detected_doc_type': detected_doc_type,
                                        'fallback_policy': fallback_policy.get("name"),
                                        'fallback_right_prefix': right_prefix,
                                    }
                                )
                        else:
                            logger.warning(
                                f"Field '{field}' rejected from wrong document: "
                                f"expected '{expected_doc_type}', got '{detected_doc_type}' ({filename})",
                                extra={
                                    'field': field,
                                    'expected_doc_type': expected_doc_type,
                                    'detected_doc_type': detected_doc_type,
                                    'source_file': filename
                                }
                            )
                            continue

                    # Amendment docs overwrite selected mutable fields if values changed,
                    # so correction/addendum values can replace earlier contract values.
                    amendment_overwrite_fields = {"MLSExpirationDate", "SettlementDate", "CommissionPercent"}
                    is_amendment_field_change = (
                        detected_doc_type == "listing_amendment"
                        and field in amendment_overwrite_fields
                        and target.get(field) not in ("", None, [], {})
                        and target.get(field) != value
                    )

                    if is_amendment_field_change or target.get(field) in ("", None, [], {}) or bool(fallback_policy):
                        target[field] = value
                        source_target[field] = filename

    def validate_sources(self, final_agg, sources_agg, request_id):
        """Post-extraction validation: verify every populated field came from its correct document type.

        Logs a warning for any field whose source file does not match the expected document type.
        Does NOT modify the data — purely diagnostic.
        """
        warnings = []

        for table_name, rows in final_agg.items():
            if table_name == "_sources" or not rows:
                continue
            source_row = (sources_agg.get(table_name) or [{}])[0]

            for field, value in rows[0].items():
                if value in ("", None, [], {}):
                    continue
                expected_doc_type = FIELD_TO_DOC_TYPE.get(field)
                source_file = source_row.get(field, "")
                if not expected_doc_type or not source_file:
                    continue

                detected_doc_type = self.detect_doc_type(source_file)
                allowed_types = expected_doc_type if isinstance(expected_doc_type, list) else [expected_doc_type]
                if detected_doc_type != "unknown" and detected_doc_type not in allowed_types:
                    warnings.append({
                        "field": field,
                        "expected_doc_type": expected_doc_type,
                        "actual_doc_type": detected_doc_type,
                        "source_file": source_file
                    })

        if warnings:
            logger.warning(
                f"Source validation: {len(warnings)} field(s) populated from incorrect document type",
                extra={
                    'request_id': request_id,
                    'validation_warnings': warnings
                }
            )
        else:
            logger.info(
                "Source validation passed: all fields from correct document types",
                extra={'request_id': request_id}
            )

        return warnings

    def process_single_pdf(self, pdf_key, schema, output_prefix, request_id, result_cache=None, batch_context=None, cache_salt=""):
        """
        Process a single document file directly with Claude.

        Features:
        - Result caching by file hash to avoid reprocessing
        - Supports PDF and images only

        Args:
            pdf_key: S3 key to document file
            schema: JSON schema for extraction
            output_prefix: S3 prefix for output
            request_id: Pipeline request ID
            result_cache: Optional ResultCache instance
        """
        import time
        filename = os.path.basename(pdf_key)
        start_time = time.time()

        try:
            # Set request context for logging in worker thread
            set_request_context(request_id)

            # Log file processing start
            logger.info(
                f"Processing file: {filename}",
                extra={
                    'request_id': request_id,
                    'file_name': filename,
                    's3_key': pdf_key,
                    'stage': STAGE_EXTRACTION,
                    'processing_status': 'STARTED'
                }
            )

            # Download file from S3
            pdf_bytes = self.s3.download_file(pdf_key)

            # Check result cache (if enabled)
            cached_result = None
            if result_cache:
                file_hash = result_cache.calculate_hash(pdf_bytes, salt=cache_salt)
                cached_result = result_cache.get(file_hash)

                if cached_result:
                    logger.info(
                        f"Cache HIT: {filename}",
                        extra={
                            'request_id': request_id,
                            'file_name': filename,
                            'file_hash': file_hash,
                            'cache_status': 'HIT'
                        }
                    )
                    # Still save the cached result to output folder
                    fname = filename.replace(".pdf", "_extracted.json").replace(".PDF", "_extracted.json")
                    output_key = f"{output_prefix}/{fname}"
                    self.s3.upload_file(json.dumps(cached_result, indent=2).encode('utf-8'), output_key)

                    return {'success': True, 'data': cached_result, 'file': filename}

            # Build system prompt
            system_prompt = self.build_system_instructions(schema, batch_context=batch_context)

            # Send file directly to Claude (cache miss)
            bedrock_response = self.bedrock.invoke_claude_with_pdf(pdf_bytes, system_prompt, request_id=request_id)
            response_text = bedrock_response["text"]
            input_tokens = bedrock_response["input_tokens"]
            output_tokens = bedrock_response["output_tokens"]

            # Parse response
            cleaned = extract_json_from_codeblock(response_text)
            parsed = json.loads(cleaned)
            result = remove_blank_fields(parsed)

            # Store in cache (if enabled)
            if result_cache and not cached_result:
                file_hash = result_cache.calculate_hash(pdf_bytes, salt=cache_salt)
                result_cache.set(file_hash, result)
                logger.info(
                    f"Cache MISS: {filename} - stored in cache",
                    extra={
                        'request_id': request_id,
                        'file_name': filename,
                        'file_hash': file_hash,
                        'cache_status': 'MISS_STORED'
                    }
                )

            # Save individual result
            fname = filename.replace(".pdf", "_extracted.json").replace(".PDF", "_extracted.json")
            output_key = f"{output_prefix}/{fname}"
            self.s3.upload_file(json.dumps(result, indent=2).encode('utf-8'), output_key)

            # Calculate processing duration
            processing_duration = time.time() - start_time

            # Structured success log with timing and token counts
            logger.info(
                f"✓ File processed successfully: {filename} ({processing_duration:.2f}s) "
                f"[tokens: {input_tokens} in / {output_tokens} out]",
                extra={
                    'request_id': request_id,
                    'file_name': filename,
                    'file_status': 'SUCCESS',
                    'file_size_kb': round(len(pdf_bytes) / 1024, 2),
                    'output_key': output_key,
                    'processing_duration_seconds': round(processing_duration, 2),
                    'input_tokens': input_tokens,
                    'output_tokens': output_tokens,
                    'stage': STAGE_EXTRACTION,
                    'processing_status': 'COMPLETED'
                }
            )
            return {'success': True, 'data': result, 'file': filename}

        except Exception as e:
            error_type = type(e).__name__
            processing_duration = time.time() - start_time

            # Structured failure log with timing
            logger.error(
                f"✗ File processing failed: {filename} ({processing_duration:.2f}s)",
                extra={
                    'request_id': request_id,
                    'file_name': filename,
                    'file_status': 'FAILED',
                    'error_type': error_type,
                    'error_message': str(e),
                    'processing_duration_seconds': round(processing_duration, 2),
                    'stage': STAGE_EXTRACTION,
                    'processing_status': 'FAILED'
                },
                exc_info=True
            )
            return {
                'success': False,
                'file': filename,
                'error': str(e),
                'error_type': error_type
            }

    def run_extraction(self, processed_file_keys, schema_key, request_id, result_cache=None, folder_name=None):
        """
        Run LLM directly on documents (PDF and images only).

        Features:
        - Result caching to avoid reprocessing duplicates
        - Lower concurrency due to larger payloads

        Args:
            processed_file_keys: List of S3 keys to process
            schema_key: S3 key to JSON schema
            request_id: Pipeline request ID
            result_cache: Optional ResultCache instance for caching
        """
        try:
            set_request_context(request_id)
            logger.info("Running document extraction...")

            if not processed_file_keys:
                return "No PDF files to process."

            # Load schema and initialize aggregator
            schema = self.load_schema(schema_key)
            final_agg, sources_agg = self.init_agg_from_schema(schema)
            ordered_file_keys = sorted(processed_file_keys, key=lambda key: os.path.basename(key).lower())
            batch_context = self.detect_batch_context(ordered_file_keys)
            cache_salt = self._build_cache_salt(schema, batch_context)

            logger.info(
                "Detected batch context for role mapping",
                extra={
                    'request_id': request_id,
                    'detected_types': batch_context.get('detected_types', []),
                    'has_buyer_agreement': batch_context.get('has_buyer_agreement', False),
                    'has_listing_agreement': batch_context.get('has_listing_agreement', False),
                }
            )

            output_prefix = f"output/{request_id}"
            total_files = len(ordered_file_keys)

            # Update status
            self.dynamodb.update_status(
                request_id, STAGE_EXTRACTION, STATUS_IN_PROGRESS,
                0, total_files, "Processing documents...",
                folder_name=folder_name
            )

            completed_count = 0
            successful_count = 0
            failed_count = 0
            all_results = []

            # Sequential execution — one file at a time to ensure correct merge order
            for key in ordered_file_keys:
                completed_count += 1

                result = self.process_single_pdf(
                    key,
                    schema,
                    output_prefix,
                    request_id,
                    result_cache,
                    batch_context=batch_context,
                    cache_salt=cache_salt,
                )

                all_results.append(result)

                if result.get('success'):
                    successful_count += 1
                    self.merge_into_single_row(
                        final_agg,
                        sources_agg,
                        result.get('data'),
                        result.get('file'),
                        request_id=request_id,
                        batch_context=batch_context,
                    )
                else:
                    failed_count += 1

                # Update every 5 files (or on last file) to reduce DynamoDB write pressure
                if completed_count % 5 == 0 or completed_count == total_files:
                    try:
                        self.dynamodb.update_status(
                            request_id, STAGE_EXTRACTION, STATUS_IN_PROGRESS,
                            completed_count, total_files,
                            f"Analyzed {successful_count}/{total_files} docs",
                            metadata={
                                'successful': successful_count,
                                'failed': failed_count,
                                'current_file': result.get('file')
                            },
                            folder_name=folder_name
                        )
                    except Exception as e:
                        logger.warning(f"DynamoDB progress update failed (non-fatal): {e}")

            # Log failure summary if any files failed
            if failed_count > 0:
                failed_files = [r for r in all_results if not r.get('success')]
                error_summary = {}
                for f in failed_files[:10]:  # First 10 for summary
                    error_summary[f['file']] = f.get('error_type', 'Unknown')

                logger.warning(
                    f"Failed to process {failed_count} file(s)",
                    extra={
                        'request_id': request_id,
                        'failed_count': failed_count,
                        'total_files': total_files,
                        'failed_files': [f['file'] for f in failed_files[:20]],  # First 20 files
                        'error_summary': error_summary,
                        'stage': STAGE_EXTRACTION
                    }
                )

            # Post-extraction semantic guard for role confusion and duplicate parties.
            self.apply_cross_field_guards(final_agg, sources_agg, request_id=request_id)

            # Post-extraction validation: check all fields came from correct document types
            self.validate_sources(final_agg, sources_agg, request_id)

            # Add _sources as a single top-level block at the end of the output
            top_level_sources = {}
            for table_name in final_agg:
                if sources_agg.get(table_name) and sources_agg[table_name]:
                    top_level_sources[table_name] = sources_agg[table_name][0]
            final_agg['_sources'] = top_level_sources

            # Add cost summary to output
            cost_tracker = self.bedrock.cost_tracker
            total_cost = float(cost_tracker.request_costs.get(request_id, 0))
            total_tokens = cost_tracker.request_tokens.get(request_id, 0)
            total_calls = cost_tracker.request_call_counts.get(request_id, 0)
            final_agg['_cost'] = {
                'total_cost_usd': round(total_cost, 6),
                'total_tokens': total_tokens,
                'total_api_calls': total_calls,
                'model': self.bedrock.model_id,
                'files_processed': successful_count,
            }

            # Save final aggregated output
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            final_output_key = f"{output_prefix}/FINAL_OUTPUT_{timestamp}.json"
            self.s3.upload_file(
                json.dumps(final_agg, indent=2).encode('utf-8'),
                final_output_key
            )

            # Update final status
            self.dynamodb.update_status(
                request_id, "Complete", STATUS_SUCCESS,
                total_files, total_files,
                "Pipeline Finished Successfully",
                metadata={
                    'total_processed': total_files,
                    'successful': successful_count,
                    'failed': failed_count,
                    'output_file': final_output_key
                },
                folder_name=folder_name
            )

            logger.info(f"Extraction complete. Final Output: {final_output_key}")
            return final_output_key

        except Exception as e:
            self.dynamodb.mark_stage_failed(
                request_id, STAGE_EXTRACTION,
                str(e), type(e).__name__, "DIRECT_PDF_ERROR",
                stack_trace=traceback.format_exc(),
                folder_name=folder_name
            )
            logger.error(f"Direct PDF extraction failed: {e}", exc_info=True)
            raise
