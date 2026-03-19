# Direct Document Extraction Service - Sends documents/images directly to Claude (No Textract)

import os
import json
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
    "CommissionPercent":          "listing_agreement",
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
    "SettlementDate":             "residential_sales_contract",
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
    "buyer_agreement":                  ["buyer agreement", "buyer_agreement", "buyeragreement", "buyer-agreement"],
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
            "detected_types": list(detected_types),
        }

    def build_system_instructions(self, schema_json, batch_context: dict = None):
        return f"""<SYSTEM_INSTRUCTIONS>
REAL ESTATE DOCUMENT EXTRACTION PIPELINE
=======================================
You are an expert real estate document analyst with 20+ years experience processing Virginia/NVAR forms.
Your job: Extract ONLY the specified fields from the provided document text into the EXACT JSON schema below.


CRITICAL CONSTRAINTS (MANDATORY):
1. Output ONLY valid JSON matching schema EXACTLY. ZERO other text.
2. 95%+ CONFIDENCE REQUIRED for every field. Omit uncertain data.
3. Handle Textract LINE quirks: fragmented text, multi-line addresses, tables.
4. Multiple documents per page → create separate JSON objects in arrays.
5. Normalize ALL data: dates=YYYY-MM-DD, $1,250→1250.00, addresses=1 line.
6. DOCUMENT SOURCE STRICT: Each field MUST be extracted ONLY from its designated document type listed below. Even if the same data appears in another document, DO NOT use it — leave the field null.
7. NO EXTRA FIELDS: Do NOT populate any field that is not explicitly listed in the DOCUMENT-TO-FIELD-MAPPING below. Every unlisted field MUST be null, no exceptions.


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
- CommissionPercent (Calculate as follows:
  - Find the TOTAL broker compensation % from "BROKER'S COMPENSATION" section (e.g. 4.5%)
  - Find the cooperating/buyer agent % from "AUTHORITY TO COOPERATE WITH OTHER BROKERS" section (e.g. 2.5%)
  - If BOTH values exist: CommissionPercent = Total % - Cooperating Broker % (e.g. 4.5 - 2.5 = 2)
  - If ONLY the total broker compensation % exists and NO cooperating broker % is found: CommissionPercent = Total % as-is (e.g. 4.5)
  - Do NOT multiply. Only convert decimal to percent if shown as decimal like 0.025 → 2.5)
- AdminFeeAmt (Look for broker compensation amount in this section)

**XREF REFERRAL FEE RULE (CRITICAL):**
- XREF1Percent and XREF1Amount are referral fee fields. They MUST remain null UNLESS the document contains a section or clause that EXPLICITLY uses the word "referral" or "referral fee".
- The "AUTHORITY TO COOPERATE WITH OTHER BROKERS" section is about cooperating broker/buyer agent compensation — this is NOT a referral fee. Do NOT use values from this section for XREF fields.
- XREF data will be null the vast majority of the time.
- XREF1Percent (Only populate if document explicitly mentions a referral fee percentage)
- XREF1Amount (Only populate if document explicitly mentions a referral fee dollar amount)


**CONTRACT DOCUMENT RULE: Any document whose heading contains the word "CONTRACT" (e.g., "RESIDENTIAL SALES CONTRACT", "RESIDENTIAL CONTRACT OF PURCHASE", or any similar contract document) must be treated as equally important. Extract buyer information, sale price, settlement date, and ratification date from ANY such contract document using the same rules below.**

=== RESIDENTIAL CONTRACT OF PURCHASE ===
(Same as Residential Sales Contract)
**IMPORTANT — TWO SCENARIOS for OutsideCustomer:**
- SCENARIO A (Listing Agreement uploaded / NM represents SELLER): OutsideCustomer = BUYER. Extract BUYER's name and email from this document.
- SCENARIO B (Buyer Agreement uploaded / NM represents BUYER): OutsideCustomer = SELLER. Extract SELLER's name and email from this document.
Identify which scenario applies by checking whether a Listing Agreement or Buyer Agreement was uploaded.

- OutsideCustomer_FirstName (SCENARIO A → BUYER's first name. SCENARIO B → SELLER's first name. If TWO names listed, put the FIRST FULL NAME here.)
- OutsideCustomer_LastName (SCENARIO A → BUYER's last name. SCENARIO B → SELLER's last name. If TWO names listed, put the SECOND FULL NAME here.)
- OutsideCustomer_Email (SCENARIO A → BUYER's email. SCENARIO B → SELLER's email.)
- ContractRatificationDate (Look for the LAST date of signature on the contract — this is the date all parties have signed/ratified. Labels to look for: "Date of Ratification", "Date of Ratification (see DEFINITIONS)", "Acceptance Date", "Date of Acceptance". The date value may appear ON THE NEXT LINE below the label, not inline — still use it. Accept formats like m/dd/yyyy, mm/dd/yyyy, or any date format. IMPORTANT: The "Agreement Date", "Date of Agreement", or any date that represents when the agreement document itself was drafted or accepted as an agreement is NOT the ratification date — do NOT use it. If the last signature date / ratification date cannot be found, return null.)
- BasePrice (Look for "Base price", "Base price of the house", or "Base price of home" in the Sales Price breakdown or Schedule of Payments section. Extract the dollar amount. Do NOT use Total Sales Price, Lot Premium, Option Price, or Discount.)


=== RESIDENTIAL SALES CONTRACT ===
(Same as Residential Contract of Purchase)
- SettlementDate (Project settlement date — look for this in the sales contract)
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

    def merge_into_single_row(self, final_agg, sources_agg, llm_result, filename):
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
                for field, value in row.items():
                    if value in ("", None, [], {}):
                        continue

                    # Source-aware check: reject field if it comes from wrong document type
                    expected_doc_type = FIELD_TO_DOC_TYPE.get(field)
                    allowed_types = expected_doc_type if isinstance(expected_doc_type, list) else [expected_doc_type]
                    if expected_doc_type and detected_doc_type != "unknown":
                        if detected_doc_type not in allowed_types:
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

                    # Amendment docs overwrite MLSExpirationDate if the value changed —
                    # ensures the latest amendment's date always wins over earlier ones.
                    is_amendment_date_change = (
                        detected_doc_type == "listing_amendment"
                        and field == "MLSExpirationDate"
                        and target.get(field) not in ("", None, [], {})
                        and target.get(field) != value
                    )

                    if is_amendment_date_change or target.get(field) in ("", None, [], {}):
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

    def process_single_pdf(self, pdf_key, schema, output_prefix, request_id, result_cache=None):
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
                file_hash = result_cache.calculate_hash(pdf_bytes)
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
            system_prompt = self.build_system_instructions(schema)

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
                file_hash = result_cache.calculate_hash(pdf_bytes)
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

            output_prefix = f"output/{request_id}"
            total_files = len(processed_file_keys)

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
            for key in processed_file_keys:
                completed_count += 1

                result = self.process_single_pdf(key, schema, output_prefix, request_id, result_cache)

                all_results.append(result)

                if result.get('success'):
                    successful_count += 1
                    self.merge_into_single_row(final_agg, sources_agg, result.get('data'), result.get('file'))
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
