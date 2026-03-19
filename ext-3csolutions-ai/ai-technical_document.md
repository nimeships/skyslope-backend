# Technical Knowledge Transfer (KT) Document
## ext-3csolutions-ai — Document Ingestion & Embedding Pipeline

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Entry Point — `main.py`](#2-entry-point--mainpy)
3. [Adapter Layer](#3-adapter-layer)
   - [BedrockAdapter](#31-bedrockadapter)
   - [OpenSearchAdapter](#32-opensearchadapter)
   - [S3Adapter](#33-s3adapter)
   - [TextractAdapter](#34-textractadapter)
4. [Controller Layer — `DataController`](#4-controller-layer--datacontroller)
5. [Service Layer — `IngestionService`](#5-service-layer--ingestionservice)
6. [Core Layer — `DataCore`](#6-core-layer--datacore)
7. [Core Utilities — `PDFParser`](#7-core-utilities--pdfparser)
8. [Utility Layer](#8-utility-layer)
   - [CSVToDataFrameConverter](#81-csvtodataframeconverter)
   - [DataFrameEmbeddingFormatter](#82-dataframeembeddingformatter)
   - [EmbeddingFormatter](#83-embeddingformatter)
   - [OpenSearchRecordFormatter](#84-opensearchrecordformatter)
   - [PDFReaderByPyMuPDF](#85-pdfreaderbypymupdf)
   - [PDFToImageConverter](#86-pdftoimageconverter)
   - [SecretManager](#87-secretmanager)
   - [TextractTableCreator](#88-textracttablecreator)
9. [Container Configuration — `dockerfile`](#9-container-configuration--dockerfile)
10. [Dependency Manifest — `requirements.txt`](#10-dependency-manifest--requirementstxt)
11. [Data Flow Diagrams](#11-data-flow-diagrams)
12. [Environment Variables Reference](#12-environment-variables-reference)
13. [Error Handling Patterns](#13-error-handling-patterns)

---

## 1. Architecture Overview

The codebase implements a multi-layer document ingestion pipeline. Raw files (PDF, CSV, XLS) are fetched from AWS S3, parsed, converted to text embeddings via AWS Bedrock (Titan Embed), and indexed into an AWS OpenSearch cluster for semantic search retrieval.

**Layer hierarchy:**

```
main.py (entrypoint)
  └── DataController (controller/)
        └── IngestionService (service/)
              └── DataCore (core/)
                    ├── BedrockAdapter   — embedding generation
                    ├── OpenSearchAdapter — document storage/search
                    ├── S3Adapter        — image upload
                    ├── TextractAdapter  — PDF OCR/table extraction
                    └── Utilities (utils/)
                          ├── CSVToDataFrameConverter
                          ├── DataFrameEmbeddingFormatter
                          ├── EmbeddingFormatter
                          ├── OpenSearchRecordFormatter
                          ├── PDFToImageConverter
                          ├── PDFReaderByPyMuPDF
                          ├── TextractTableCreator
                          └── SecretManager
```

The container is designed to run as an **AWS ECS Fargate task**. Triggering is event-driven: the S3 bucket name and object key are injected at runtime via environment variables (`BUCKET_NAME`, `OBJECT_KEY`).

---

## 2. Entry Point — `main.py`

**File:** `main.py`

### `lambda_style_entrypoint()`

| Attribute | Detail |
|-----------|--------|
| Parameters | None |
| Return type | `None` |
| Called by | Python `__main__` block |

**Behaviour:**

1. Instantiates `DataController`.
2. Calls `DataController.get_details()` to validate environment variables and log S3 source info.
3. The method `get_file_type_and_stream()` is intentionally commented out — it was the next step that routes the file to the correct reader in `IngestionService` based on file extension. Its activation is deferred pending completion of integration work.

**Note:** The name `lambda_style_entrypoint` is a naming convention only. The process runs inside a Docker container on ECS Fargate, not AWS Lambda.

---

## 3. Adapter Layer

The adapter layer wraps all AWS SDK and third-party SDK calls. Each adapter is a single-responsibility class that isolates external service communication from business logic.

---

### 3.1 BedrockAdapter

**File:** [src/adapter/bedrock_adapter.py](src/adapter/bedrock_adapter.py)

**Dependencies:** `boto3`, `botocore`, `concurrent.futures`, `time`, `json`

**Class: `BedrockAdapter`**

#### `__init__(self)`

| Attribute | Detail |
|-----------|--------|
| Parameters | None |
| Return type | None |
| Side effect | Creates a `boto3` client for `bedrock-runtime` using the ambient IAM role credentials. |

The Bedrock Runtime client is stored as `self.client`. AWS credentials are resolved automatically from the execution environment (IAM task role in ECS).

---

#### `generate_embedding(self, text: str) -> List[float]`

| Attribute | Detail |
|-----------|--------|
| Parameters | `text` — plain string to embed (max ~45 000 chars for Titan Embed) |
| Return type | `List[float]` — 1536-dimensional dense vector |
| Model | `amazon.titan-embed-text-v1` |
| Retry logic | Up to 3 attempts; exponential backoff on throttling |

**Internal Logic:**

1. Constructs a JSON body `{"inputText": text}`.
2. Calls `bedrock-runtime.invoke_model` with `contentType: application/json` and `accept: application/json`.
3. Reads and JSON-parses the streaming response body.
4. Returns the `"embedding"` field from the response, which is a list of 1536 floats.

**Error Handling:**

- `ThrottlingException` / `TooManyRequestsException` from AWS: retries up to `max_retries` (3) times with a delay computed as `delay_seconds ** max_retries` (evaluates to `1.5^3 = 3.375` seconds on each retry — not a true per-attempt increasing backoff).
- Other `ClientError`: raises `RuntimeError` immediately.
- Any other `Exception`: raises `RuntimeError` with the raw exception message.

**Edge Cases:**

- If `text` exceeds the Titan Embed input limit, the API call will fail. Callers are expected to truncate before calling (see `_fallback_individual_processing` in `DataCore`).
- Returns `List[float]` directly; the caller stores it as the OpenSearch `knn_vector` field.

---

#### `generate_batch_embeddings(self, texts: List[str], batch_size: int = 10, max_workers: int = 5) -> List[tuple]`

| Attribute | Detail |
|-----------|--------|
| Parameters | `texts` — list of strings; `batch_size` — concurrent items per batch; `max_workers` — thread pool size |
| Return type | `List[Tuple[int, List[float]]]` — list of `(original_index, embedding_vector)` pairs, only for successful items |
| Dependencies | `ThreadPoolExecutor`, `as_completed` from `concurrent.futures` |

**Internal Logic:**

1. Pre-allocates `embeddings = [None] * len(texts)` to maintain positional correspondence.
2. Splits `texts` into sub-lists of `batch_size`.
3. For each batch, launches up to `min(max_workers, len(batch_texts))` threads via `ThreadPoolExecutor`.
4. Each thread calls the inner closure `process_single_embedding(index_text_pair)`:
   - Calls `self.generate_embedding(text)`.
   - Returns a tuple `(index, embedding, None)` on success or `(index, None, error_string)` on failure.
5. Results are gathered via `as_completed` and placed back into the `embeddings` list by index.
6. After each batch (except the last), sleeps for 2 seconds to reduce Bedrock API throttling risk.
7. Returns only successfully-generated `(index, embedding)` pairs — entries where `embedding is not None`.

**Important:** Failed embeddings are silently dropped (index positions remain `None`). Callers must handle the sparse result set by using the returned index to map back to the original data.

**Thread Safety:** The `embeddings` list uses index-based writes. Since each thread writes to a unique index, no locking is required.

---

### 3.2 OpenSearchAdapter

**File:** [src/adapter/opensearch_adapter.py](src/adapter/opensearch_adapter.py)

**Dependencies:** `opensearch-py` (`OpenSearch`, `RequestsHttpConnection`, `bulk`), `time`

**Class: `OpenSearchAdapter`**

#### `__init__(self, host, auth, region, service, index)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `host` | `str` | OpenSearch domain hostname (no scheme, no port) |
| `auth` | `tuple` or `AWSV4SignerAuth` | HTTP basic auth tuple `(username, password)` or AWS SigV4 auth object |
| `region` | `str` | AWS region string (e.g., `us-east-1`) |
| `service` | `str` | `"es"` for OpenSearch Service or `"aoss"` for Serverless |
| `index` | `str` | Target OpenSearch index name |

Creates an `OpenSearch` client configured for HTTPS on port 443 with SSL certificate verification enabled and a connection pool of 20. Immediately calls `create_index_if_not_exists(dimension=1536)`.

---

#### `create_index_if_not_exists(self, dimension: int = 1536)`

| Attribute | Detail |
|-----------|--------|
| Parameters | `dimension` — integer, dimensionality of the knn_vector field |
| Return type | `None` |
| Side effect | Creates the index if absent; no-op if it already exists |

**Index Mapping Created:**

```json
{
  "settings": { "index": { "knn": true } },
  "mappings": {
    "properties": {
      "text":      { "type": "text" },
      "embedding": { "type": "knn_vector", "dimension": 1536 }
    }
  }
}
```

The `knn: true` setting enables the k-NN plugin on this index so approximate nearest-neighbor search can be executed against the `embedding` field.

**Note:** The mapping only defines `text` and `embedding` fields explicitly. All other fields stored in documents (e.g., `client_name`, `file_name`, `content`, `image_url`) are indexed with OpenSearch's dynamic mapping defaults.

---

#### `store_document(self, doc: dict)`

| Attribute | Detail |
|-----------|--------|
| Parameters | `doc` — dictionary to index as a new document |
| Return type | `None` (OpenSearch response is discarded) |
| Side effect | Indexes a single document into `self.index` |

Calls `client.index(index=self.index, body=doc)`. OpenSearch auto-generates the document `_id`. No retry logic. Intended for low-throughput use (single PDF page, single row in fallback mode).

---

#### `bulk_store_documents(self, documents: list, batch_size: int = 100, max_retries: int = 3) -> Tuple[int, int]`

| Attribute | Detail |
|-----------|--------|
| Parameters | `documents` — list of dicts; `batch_size` — docs per bulk request; `max_retries` — per-batch retry limit |
| Return type | `Tuple[int, int]` — `(successful_doc_count, failed_doc_count)` |

**Internal Logic:**

1. Splits `documents` into slices of `batch_size`.
2. For each slice, builds a list of bulk action dicts:
   ```python
   {"_index": self.index, "_source": doc}
   ```
3. Calls `opensearchpy.helpers.bulk()` with:
   - `chunk_size=batch_size`
   - `request_timeout=60`
   - Internal `max_retries=2`, `initial_backoff=2`, `max_backoff=600`
4. If `bulk()` raises, retries with exponential backoff (`2^attempt` seconds).
5. After `max_retries` exhausted, counts the entire batch as failed.
6. Accumulates totals across all batches and returns them.

**Return semantics:** `bulk()` returns `(success_count, failed_list)`. The adapter counts `len(failed_list)` as failures.

---

### 3.3 S3Adapter

**File:** [src/adapter/s3_adapter.py](src/adapter/s3_adapter.py)

**Dependencies:** `boto3`

**Class: `S3Adapter`**

#### `__init__(self, bucket: str = None)`

| Attribute | Detail |
|-----------|--------|
| Parameters | `bucket` — optional S3 bucket name |
| Side effect | Creates a `boto3` S3 client |

`self.bucket` defaults to `None`; must be provided explicitly or the bucket name must be passed elsewhere.

---

#### `upload_image(self, key: str, image_bytes: bytes) -> str`

| Attribute | Detail |
|-----------|--------|
| Parameters | `key` — S3 object key (path within bucket); `image_bytes` — raw PNG bytes |
| Return type | `str` — S3 URI in format `s3://<bucket>/<key>` |
| ContentType | `image/png` (hardcoded) |

Calls `s3.put_object`. No error handling — exceptions propagate to the caller (`DataCore.pdf_file_processor`). The returned URI string is stored as `image_url` in the OpenSearch document.

---

### 3.4 TextractAdapter

**File:** [src/adapter/textract_adapter.py](src/adapter/textract_adapter.py)

**Dependencies:** AWS Textract client (injected via constructor)

**Class: `TextractAdapter`**

#### `__init__(self, textract_client)`

| Attribute | Detail |
|-----------|--------|
| Parameters | `textract_client` — a pre-constructed `boto3` Textract client |

Stores the client as `self.textract_client`. The client is not created internally, allowing the caller to control the AWS region.

---

#### `analyze_document(self, document_stream: bytes) -> dict`

| Attribute | Detail |
|-----------|--------|
| Parameters | `document_stream` — raw bytes of a single-page image (PNG produced from a PDF page) |
| Return type | `dict` — full AWS Textract `AnalyzeDocument` response |
| Feature types | `['TABLES', 'FORMS']` |

**Internal Logic:**

Calls `textract_client.analyze_document(Document={'Bytes': document_stream}, FeatureTypes=['TABLES', 'FORMS'])`.

The response contains a `Blocks` list. Each block has a `BlockType` that can be:
- `PAGE`, `LINE`, `WORD` — for text content
- `TABLE`, `CELL` — for tabular data
- `KEY_VALUE_SET` — for form field key-value pairs

**Error Handling:** Catches all exceptions, logs them, and re-raises as `RuntimeError`.

**Limitation:** `Document={'Bytes': ...}` is a synchronous, single-page call. Maximum document size is 10 MB per AWS limits. Multi-page PDFs must be split into individual page images before calling this method.

---

## 4. Controller Layer — `DataController`

**File:** [src/controller/data_controller.py](src/controller/data_controller.py)

**Dependencies:** `boto3`, `os`, environment variables

**Class: `DataController`**

#### `__init__(self)`

| Env Variable | Purpose |
|-------------|---------|
| `BUCKET_NAME` | S3 bucket from which files are sourced |
| `OBJECT_KEY` | S3 object key (full path to the file) |
| `REGION` | AWS region (used for Textract, Bedrock, etc.) |

**Active initialisation:**
- Creates a `boto3` S3 client (`self.s3`).
- Reads `BUCKET_NAME` → `self.s3_bucket`.
- Reads `OBJECT_KEY` → `self.s3_key`.
- Reads `REGION` → `self.region`.

**Commented-out initialisation (currently disabled):**
All downstream adapters (`BedrockAdapter`, `S3Adapter`, `OpenSearchAdapter`, `TextractAdapter`), utilities, `DataCore`, and `IngestionService` instantiation are present but commented out. They depend on additional env vars (`OpenSearchURL`, `OPENSEARCH_INDEX`) and AWS Secrets Manager integration that is still being wired in.

---

#### `get_details(self)`

| Attribute | Detail |
|-----------|--------|
| Parameters | None |
| Return type | `None` |
| Side effect | Logs bucket and key info; raises `RuntimeError` on any exception |

Currently only logs; the actual S3 `get_object` call is not made in this method. It serves as a connectivity/configuration validation step.

---

#### `get_file_type_and_stream()` *(commented out)*

When re-enabled, this method will:
1. Inspect `self.s3_key` extension (`.pdf`, `.xls`, `.csv`).
2. Route to the corresponding `IngestionService` method: `pdf_stream_reader()`, `xls_stream_reader()`, or `csv_stream_reader()`.
3. Raise `ValueError` for unsupported extensions.

`.xlsx` support is noted in a docstring as intentionally deferred.

---

## 5. Service Layer — `IngestionService`

**File:** [src/service/ingestion_service.py](src/service/ingestion_service.py)

**Class: `IngestionService`**

#### `__init__(self, s3, s3_key, s3_bucket, DataCore)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `s3` | boto3 S3 client | Shared S3 client from `DataController` |
| `s3_key` | `str` | S3 object key for the file to process |
| `s3_bucket` | `str` | S3 bucket name |
| `DataCore` | `DataCore` instance | Injected dependency for file processing |

---

#### `csv_stream_reader(self)`

| Attribute | Detail |
|-----------|--------|
| Parameters | None |
| Return type | `None` |
| Side effect | Reads file from S3, passes stream to `DataCore.csv_file_processor` |

1. Calls `s3.get_object(Bucket=self.s3_bucket, Key=self.s3_key)`.
2. Reads the full body into memory via `.read()`.
3. Wraps in `io.BytesIO` to create a seekable in-memory stream.
4. Passes the stream and `self.s3_key` to `self.data_core.csv_file_processor(xls_file_stream, self.s3_key)`.

**Note:** The variable name `xls_file_stream` in the CSV reader is a naming inconsistency in the source code; the content is a CSV byte stream.

---

#### `xls_stream_reader(self)`

| Attribute | Detail |
|-----------|--------|
| Parameters | None |
| Return type | `None` |
| Side effect | Reads file into memory as `io.BytesIO`; does **not** call `DataCore` |

The XLS processing call to `DataCore` is not yet implemented. The method reads the S3 object but does nothing with the stream (no-op past the read). This is a stub for future development.

---

#### `pdf_stream_reader(self)`

| Attribute | Detail |
|-----------|--------|
| Parameters | None |
| Return type | `None` |
| Side effect | Reads PDF from S3, passes stream to `DataCore.pdf_file_processor` |

1. Same S3 read pattern as `csv_stream_reader`.
2. Wraps bytes in `io.BytesIO`.
3. Delegates to `self.data_core.pdf_file_processor(pdf_file_stream, self.s3_key)`.

---

## 6. Core Layer — `DataCore`

**File:** [src/core/data_core.py](src/core/data_core.py)

**Dependencies:** `pandas`, `json`, `time`

**Class: `DataCore`**

`DataCore` is the central processing engine. It orchestrates file parsing, embedding generation, document formatting, and OpenSearch storage.

#### `__init__(self, s3, s3_key, s3_bucket, BedrockAdapter, TextractAdapter, S3Adapter, OpenSearchAdapter, CsvToDataFrameConverterUtil, dfEmbeddingFormatterUtil, EmbeddingFormatter, OpensearchrecordFormatter, PDFToImageConverterUtil, PDFReaderByPyMuPDFUtil, TextractTableCreatorUtil)`

All dependencies are injected. No default values. All parameters are stored as instance attributes under the same names for use by the processing methods.

---

#### `csv_file_processor(self, file_stream, s3_key, batch_size=50, embedding_batch_size=10)`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_stream` | `io.BytesIO` | required | CSV byte stream |
| `s3_key` | `str` | required | S3 object key, used to extract `client_name` and `username` |
| `batch_size` | `int` | 50 | Documents per OpenSearch bulk request |
| `embedding_batch_size` | `int` | 10 | Target texts per Bedrock batch |

**Key-path parsing from `s3_key`:**
- `client_name = s3_key.split('/')[1]`
- `username = s3_key.split('/')[2]`
- `fileName = s3_key.split('/')[-1]`

This assumes the S3 key follows the structure: `<prefix>/<client_name>/<username>/.../<filename>`.

**Processing Pipeline:**

```
file_stream
  └─ CSVToDataFrameConverter.csv_to_df_convert()         → DataFrame
       └─ DataFrameEmbeddingFormatter.format_rows_for_embedding()  → List[str]
            └─ EmbeddingFormatter.format_text_for_embedding()       → contextual text per row
                 └─ _create_optimized_batches()                      → List of batches
                      └─ BedrockAdapter.generate_batch_embeddings()  → List[(index, vector)]
                           └─ OpenSearchRecordFormatter.format_record_for_opensearch() → dict
                                └─ OpenSearchAdapter.bulk_store_documents()            → stored
```

**Early exit condition:** If `formatted_rows` is empty after parsing and dropping all-null rows, the method logs a warning and returns without making any API calls.

**Error Handling:** Each batch is wrapped in a `try/except`. A failed batch is logged and skipped; processing continues with remaining batches. This means partial results are stored if some batches fail.

---

#### `_create_optimized_batches(self, embedding_contents, row_strings, target_batch_size) -> List[Tuple]`

| Parameter | Type | Description |
|-----------|------|-------------|
| `embedding_contents` | `List[str]` | Pre-formatted text strings to be embedded |
| `row_strings` | `List[str]` | Corresponding raw row content strings (for storage) |
| `target_batch_size` | `int` | Maximum items per batch (count-based limit) |

**Return type:** `List[Tuple[List[str], List[str], List[int]]]`
Each tuple: `(batch_texts, batch_row_strings, original_indices)`

**Limits enforced:**

| Constant | Value | Purpose |
|----------|-------|---------|
| `MAX_CHARS_PER_TEXT` | 45 000 | Titan Embed per-request input cap |
| `MAX_TOTAL_CHARS_PER_BATCH` | 200 000 | Aggregate character cap per concurrent batch |

**Algorithm:** Iterates over `(embedding_content, row_string)` pairs. For each item:
1. If `len(text) > MAX_CHARS_PER_TEXT`, skip it entirely (logged as warning).
2. If adding the item would exceed either the character cap or the count cap, flush the current batch and start a new one.
3. Append item to the current batch.
4. After iteration, flush any remaining items as the final batch.

---

#### `_fallback_individual_processing(self, formatted_rows, client_name, username, fileName, s3_key)`

| Attribute | Detail |
|-----------|--------|
| Parameters | Pre-parsed row list and metadata strings |
| Return type | `None` |
| Purpose | Row-by-row processing when batch processing is unavailable |

Iterates over each row individually:
1. Serialises row to JSON string.
2. Formats embedding content via `EmbeddingFormatter`.
3. Truncates content to 45 000 characters if it exceeds the limit (appends `"..."`).
4. Calls `BedrockAdapter.generate_embedding()` (single-item call).
5. Formats and stores document via `OpenSearchAdapter.store_document()` (single-doc call).
6. Every 10 rows, sleeps 1 second to avoid Bedrock throttling.

**Error Handling:** Each row is in its own `try/except`. Failed rows are counted and skipped.

**Note:** This method is currently not called from any active code path. It is available as a manual fallback.

---

#### `pdf_file_processor(self, file_stream, s3_key)`

| Attribute | Detail |
|-----------|--------|
| Parameters | `file_stream` — PDF `io.BytesIO`; `s3_key` — S3 key for path parsing and output key construction |
| Return type | `None` |

**Processing Pipeline (per page):**

```
file_stream
  └─ PDFToImageConverter.convert_pdf_to_images()      → List[bytes] (PNG per page)
       └─ [loop per page image]
            ├─ TextractAdapter.analyze_document()      → Textract response dict
            ├─ Extract LINE blocks → join as `context` string
            ├─ BedrockAdapter.generate_embedding(context) → vector
            ├─ S3Adapter.upload_image(outkey, image_bytes) → s3_image_url
            └─ OpenSearchRecordFormatter.pdf_document_record_for_opensearch()
                 └─ OpenSearchAdapter.store_document() → stored
```

**Output key construction:**
- `outkey = s3_key.replace("input/", "image/")`
- Per page: `f"{outkey.replace('.pdf','')}/page-{count}.png"`

This assumes the source key contains the string `"input/"` in its path.

**Text extraction from Textract:** Only `BlockType == "LINE"` blocks are used. `TABLE`, `FORM`, and `KEY_VALUE_SET` blocks are currently ignored (code is present but commented out). Extracted LINE texts are joined with a single space to form the full-page context.

**Page counter:** Variable `count` starts at 1 and is manually incremented inside the loop.

**No batch processing for PDF:** Each page is processed sequentially, with one `store_document` call per page (no bulk upload).

---

## 7. Core Utilities — `PDFParser`

**File:** [src/core/pdf_parser.py](src/core/pdf_parser.py)

**Dependencies:** `fitz` (PyMuPDF), `PIL` (Pillow), `io`

**Class: `PDFParser`**

#### `extract_pages(self, file_stream, filename: str) -> List[dict]`

| Attribute | Detail |
|-----------|--------|
| Parameters | `file_stream` — raw bytes or `BytesIO`; `filename` — used for logging only |
| Return type | `List[dict]` with keys: `page_number`, `text`, `image_bytes` |

**Internal Logic:**

1. Opens the PDF with `fitz.open(stream=file_stream, filetype="pdf")`.
2. Iterates over all pages.
3. For each page:
   - Extracts plain text via `page.get_text()`.
   - Renders the page to a pixmap at 2× zoom (`fitz.Matrix(2, 2)`).
   - Converts the pixmap to a PIL `Image` in RGB mode.
   - Saves the image as PNG bytes into a `BytesIO` buffer.
   - Appends `{"page_number": page_index + 1, "text": text.strip(), "image_bytes": image_bytes}`.
4. If image extraction fails for a page, `image_bytes` remains `None` and processing continues.

**Note:** `PDFParser` is a standalone class in `src/core/` and is distinct from `PDFReaderByPyMuPDF` in `src/utils/`. `PDFParser` is simpler and not currently integrated into the active data flow. `PDFReaderByPyMuPDF` (in utils) is the more feature-rich implementation referenced by `DataCore`.

---

## 8. Utility Layer

### 8.1 CSVToDataFrameConverter

**File:** [src/utils/csv_to_dataframe_converter.py](src/utils/csv_to_dataframe_converter.py)

**Dependencies:** `pandas`

#### `csv_to_df_convert(file_stream) -> pd.DataFrame` *(static method)*

| Attribute | Detail |
|-----------|--------|
| Parameters | `file_stream` — `io.BytesIO` of CSV data |
| Return type | `pd.DataFrame` |

Calls `pd.read_csv(file_stream)` with all defaults. Column names are inferred from the first row. No encoding, delimiter, or type override is applied. The resulting DataFrame preserves all columns and raw cell values.

---

### 8.2 DataFrameEmbeddingFormatter

**File:** [src/utils/df_embedding_formatter.py](src/utils/df_embedding_formatter.py)

**Dependencies:** `pandas`

#### `format_rows_for_embedding(df: pd.DataFrame) -> List[str]` *(static method)*

| Attribute | Detail |
|-----------|--------|
| Parameters | `df` — pandas DataFrame |
| Return type | `List[str]` — one string per non-null row |

**Processing:**
1. `df.dropna(how="all")` — drops rows where every column is null. Rows with at least one non-null value are kept.
2. For each remaining row, applies a lambda: joins all non-null column values as `"<column_name>: <value>"` pairs separated by `", "`.
3. Returns the result as a Python list via `.tolist()`.

**Output example:**
```
"Name: Alice, Age: 30, Department: Engineering"
```

Null column values within a row are silently skipped (not represented in the output string).

---

### 8.3 EmbeddingFormatter

**File:** [src/utils/embedding_formatter.py](src/utils/embedding_formatter.py)

**Dependencies:** `json`

#### `format_text_for_embedding(client_name: str, username: str, filename: str, content: str) -> str` *(static method)*

| Attribute | Detail |
|-----------|--------|
| Parameters | `client_name`, `username`, `filename`, `content` — all strings |
| Return type | `str` — a JSON-encoded string |

Constructs an f-string:
```
"Client Name: {client_name}\nUsername: {username}\nFilename: {filename}\nContent:\n{content}"
```

Then passes it through `json.dumps()`, which adds JSON string escaping (quotes, escape sequences). The result is a double-encoded string: the embedding input is a JSON-serialised plain string.

**Effect on embedding quality:** The Titan Embed model receives the JSON-quoted version of the context. This is functional but slightly unconventional — plain-text input would be more typical for embedding models.

---

### 8.4 OpenSearchRecordFormatter

**File:** [src/utils/opensearch_record_creation.py](src/utils/opensearch_record_creation.py)

**Class: `OpenSearchRecordFormatter`**

#### `format_record_for_opensearch(client_name, file_name, content, embedding) -> dict` *(static method)*

| Attribute | Detail |
|-----------|--------|
| Parameters | `client_name` `str`, `file_name` `str`, `content` `str`, `embedding` `List[float]` |
| Return type | `dict` |

Returns:
```python
{
    "client_name": client_name,
    "file_name":   file_name,
    "content":     content,
    "embedding":   embedding  # List[float], stored as knn_vector
}
```

Used for CSV rows and generic text documents.

---

#### `pdf_document_record_for_opensearch(client_name, file_name, content, image_url, embedding) -> dict` *(static method)*

| Attribute | Detail |
|-----------|--------|
| Parameters | Same as above plus `image_url: str` |
| Return type | `dict` |

Returns the same structure as `format_record_for_opensearch` with an additional `"image_url"` key:
```python
{
    "client_name": client_name,
    "file_name":   file_name,
    "content":     content,
    "image_url":   image_url,  # S3 URI string
    "embedding":   embedding
}
```

Used exclusively for PDF pages where a corresponding page image is stored in S3.

---

### 8.5 PDFReaderByPyMuPDF

**File:** [src/utils/pdf_reader_by_pymupdf.py](src/utils/pdf_reader_by_pymupdf.py)

**Dependencies:** `fitz` (PyMuPDF), `PIL` (Pillow), `collections`

**Class: `PDFReaderByPyMuPDF`**

#### `__init__(self)`

Lazily imports `fitz` and `PIL.Image` at construction time and stores them as `self.fitz` and `self.image`. This avoids module-level import failures if optional dependencies are absent.

---

#### `extract_pages(self, file_stream, filename: str) -> List[dict]`

| Attribute | Detail |
|-----------|--------|
| Parameters | `file_stream` — raw bytes; `filename` — for logging |
| Return type | `List[dict]` |

Each result dict contains:

| Key | Type | Description |
|-----|------|-------------|
| `page_number` | `int` | 1-based page index |
| `has_text` | `bool` | True if page contains non-whitespace text |
| `text_content` | `str` | Full plain text of the page |
| `has_image` | `bool` | True if page embeds at least one image |
| `images` | `List[dict]` | List of `{image_index, format, image_bytes}` dicts |
| `has_table_like_structure` | `bool` | True if `extract_table_from_blocks` found any rows |
| `table_data` | `List[List[str]]` | Structured table approximation |

**Image extraction:** Uses `page.get_images(full=True)` and `doc.extract_image(xref)` to get embedded image bytes in their native format.

---

#### `extract_table_from_blocks(self, blocks) -> List[List[str]]`

| Attribute | Detail |
|-----------|--------|
| Parameters | `blocks` — list of block tuples from `page.get_text("blocks")` |
| Return type | `List[List[str]]` — list of row lists |

**Algorithm:**
1. For each block, extracts `(x0, y0, x1, y1, text, ...)`.
2. Skips empty text blocks.
3. Normalises the `y0` coordinate: `y_key = round(y0 / 10)`. Blocks within 10 pixels vertically are grouped into the same row.
4. Within each row, sorts blocks left to right by `x0`.
5. Returns rows sorted by `y_key` (top to bottom).

**Limitation:** This is a heuristic approximation. It does not use Textract and cannot distinguish actual table borders. It works on spatial proximity of text blocks, which may produce incorrect results for complex layouts or multi-column text.

---

### 8.6 PDFToImageConverter

**File:** [src/utils/pdf_to_image_converter.py](src/utils/pdf_to_image_converter.py)

**Dependencies:** `fitz` (PyMuPDF)

**Class: `PDFToImageConverter`**

#### `convert_pdf_to_images(file_stream) -> List[bytes]` *(static method)*

| Attribute | Detail |
|-----------|--------|
| Parameters | `file_stream` — `io.BytesIO` of PDF data |
| Return type | `List[bytes]` — one PNG byte string per page |

**Internal Logic:**
1. Opens PDF with `fitz.open(stream=file_stream, filetype="pdf")`.
2. For each page, calls `page.get_pixmap(matrix=fitz.Matrix(2, 2))` to render at 2× zoom.
3. Converts pixmap to PNG bytes via `pix.tobytes("png")`.
4. Appends to `images` list.
5. Closes the document after all pages are processed.

**Zoom factor:** `fitz.Matrix(2, 2)` doubles both horizontal and vertical resolution, improving OCR accuracy when the output images are passed to Textract.

**Output format:** Raw PNG bytes (not PIL Image objects, not file paths). These bytes are directly sent to `TextractAdapter.analyze_document()` and `S3Adapter.upload_image()`.

---

### 8.7 SecretManager

**File:** [src/utils/secret_manager.py](src/utils/secret_manager.py)

**Dependencies:** `boto3`, `botocore`, `json`, `os`

**Class: `SecretManager`**

#### `__init__(self, region_name: str = "us-east-1")`

| Attribute | Detail |
|-----------|--------|
| Parameters | `region_name` — AWS region for the Secrets Manager endpoint |
| Side effect | Creates a `boto3` Secrets Manager client |

Raises `RuntimeError` if client creation fails.

---

#### `get_secret(self, secret_name: str) -> dict`

| Attribute | Detail |
|-----------|--------|
| Parameters | `secret_name` — ARN or name of the secret |
| Return type | `dict` — parsed JSON from the secret value |

1. Calls `secretsmanager.get_secret_value(SecretId=secret_name)`.
2. If `"SecretString"` is in the response, parses and returns it as JSON.
3. If only `"SecretBinary"` is present, decodes bytes and parses as JSON.
4. On `ThrottlingException` or `TooManyRequestsException`, raises `RuntimeError("Throttling: ...")`.
5. On other `ClientError` or general exceptions, raises `RuntimeError` with contextual message.

---

#### `get_config(self) -> dict`

| Attribute | Detail |
|-----------|--------|
| Parameters | None |
| Return type | `dict` with keys: `opensearch_host`, `opensearch_port`, `opensearch_username`, `opensearch_password`, `opensearch_index` |
| Env variable | `SSM_OPEN_ARN` — must be set to the ARN of the OpenSearch credentials secret |

**Internal Logic:**
1. Reads `SSM_OPEN_ARN` from environment.
2. Calls `self.get_secret(secreto_arn)` to fetch the secret dictionary.
3. Maps secret keys to a configuration dictionary:

| Secret Key | Config Key | Default |
|------------|------------|---------|
| `OpenSearchCredentials` | `opensearch_host` | — |
| `OPENSEARCH_PORT` | `opensearch_port` | `443` (int) |
| `username` | `opensearch_username` | — |
| `password` | `opensearch_password` | — |
| `OPENSEARCH_INDEX` | `opensearch_index` | `"embeddings-index4"` |

Raises `RuntimeError` if any step fails.

---

#### `health_check(self) -> dict`

| Attribute | Detail |
|-----------|--------|
| Parameters | None |
| Return type | `dict` with key `status` (`"healthy"` or `"unhealthy"`) |

Calls `secretsmanager.list_secrets(MaxResults=1)` as a lightweight connectivity probe. On failure, returns `{"status": "unhealthy", "details": str(e)}`.

---

### 8.8 TextractTableCreator

**File:** [src/utils/textract_table_creator.py](src/utils/textract_table_creator.py)

**Dependencies:** `pandas`, `collections.defaultdict`

**Class: `TextractTableCreator`**

#### `create_dataframe_from_textract_table(table_data) -> pd.DataFrame` *(static method)*

| Attribute | Detail |
|-----------|--------|
| Parameters | `table_data` — full Textract `AnalyzeDocument` response dict (containing `"Blocks"` key) |
| Return type | `pd.DataFrame` |

**Algorithm:**

1. If `table_data` is falsy, returns an empty `pd.DataFrame()`.
2. Extracts all blocks with `BlockType == "CELL"` from `table_data["Blocks"]`.
3. For each cell block:
   - Reads `RowIndex` and `ColumnIndex` (1-based integers from Textract).
   - Navigates `Relationships` of type `"CHILD"` to find child block IDs.
   - Resolves each child ID to its block; if `BlockType == "WORD"`, appends `block["Text"]` to the cell text.
   - Stores `table[row][col] = text.strip()` in a nested `defaultdict`.
4. Determines `max_col` as the maximum column index across all rows.
5. Iterates rows in sorted order, building each as a list of cells from `col_idx 1` to `max_col`, using empty string for missing cells.
6. Constructs and returns `pd.DataFrame(data)` with integer column names (0-based).

**Limitation (noted in `DataCore`):** This implementation captures only the first set of table columns when multiple table structures are present in the Textract response. If a document page has more than one `TABLE` block, all cells are mixed into a single `defaultdict` and the resulting DataFrame may be incorrect. This is acknowledged as a known issue in the `DataCore.pdf_file_processor` commented-out block.

---

## 9. Container Configuration — `dockerfile`

**File:** [dockerfile](dockerfile)

| Directive | Value / Purpose |
|-----------|----------------|
| `FROM` | `python:3.11` — Official Python 3.11 base image |
| `apt-get install` | `gcc`, `build-essential` (C compilation for native packages), `libmagic1` (MIME detection) |
| `WORKDIR` | `/app` |
| `COPY requirements.txt .` | Copies only the requirements file first (layer caching optimisation) |
| `RUN pip install` | Installs Python dependencies with `--no-cache-dir` |
| `COPY main.py .` | Copies entrypoint |
| `COPY src/ ./src/` | Copies all source modules |
| `RUN echo ... && ls -R /app` | Debug step to verify file layout at build time |
| `CMD` | `["python", "main.py"]` — runs the ingestion pipeline on container start |

**Layer ordering:** `requirements.txt` is copied and installed before application source to exploit Docker build cache. If source code changes but `requirements.txt` does not, the `pip install` layer is reused.

---

## 10. Dependency Manifest — `requirements.txt`

| Package | Version | Purpose |
|---------|---------|---------|
| `boto3` | latest | AWS SDK (S3, Bedrock, Textract, Secrets Manager) |
| `opensearch-py` | latest | OpenSearch client with bulk helpers |
| `pydantic` | latest | Data validation (imported but not actively used in current code) |
| `PyMuPDF` | `1.23.7` | PDF parsing and rasterisation (`fitz` module) |
| `pillow` | latest | Image processing (used in `PDFParser`) |
| `pandas` | `2.2.2` | DataFrame operations for CSV and table processing |

---

## 11. Data Flow Diagrams

### CSV Ingestion Flow

```
S3 Object (CSV)
    │
    ▼
IngestionService.csv_stream_reader()
    │  io.BytesIO
    ▼
DataCore.csv_file_processor()
    │
    ├─ CSVToDataFrameConverter.csv_to_df_convert()
    │       → pd.DataFrame
    │
    ├─ DataFrameEmbeddingFormatter.format_rows_for_embedding()
    │       → List[str]  ("col: val, col: val, ...")
    │
    ├─ EmbeddingFormatter.format_text_for_embedding()  [per row]
    │       → contextual text with client/user/filename header
    │
    ├─ DataCore._create_optimized_batches()
    │       → List of (texts, rows, indices) batches
    │
    ├─ BedrockAdapter.generate_batch_embeddings()  [per batch]
    │       → List[(index, List[float])]
    │
    ├─ OpenSearchRecordFormatter.format_record_for_opensearch()  [per result]
    │       → {"client_name", "file_name", "content", "embedding"}
    │
    └─ OpenSearchAdapter.bulk_store_documents()
            → Documents indexed in OpenSearch
```

### PDF Ingestion Flow

```
S3 Object (PDF)
    │
    ▼
IngestionService.pdf_stream_reader()
    │  io.BytesIO
    ▼
DataCore.pdf_file_processor()
    │
    ├─ PDFToImageConverter.convert_pdf_to_images()
    │       → List[bytes]  (PNG per page, 2x zoom)
    │
    └─ [loop per page]
         │
         ├─ TextractAdapter.analyze_document(image_bytes)
         │       → Textract response (Blocks)
         │
         ├─ Extract LINE blocks → join as context string
         │
         ├─ BedrockAdapter.generate_embedding(context)
         │       → List[float]  (1536-dim vector)
         │
         ├─ S3Adapter.upload_image(page_key, image_bytes)
         │       → "s3://bucket/image/path/page-N.png"
         │
         ├─ OpenSearchRecordFormatter.pdf_document_record_for_opensearch()
         │       → {"client_name", "file_name", "content", "image_url", "embedding"}
         │
         └─ OpenSearchAdapter.store_document()
                 → Document indexed in OpenSearch
```

---

## 12. Environment Variables Reference

| Variable | Consumed By | Description |
|----------|-------------|-------------|
| `BUCKET_NAME` | `DataController` | S3 bucket containing source files |
| `OBJECT_KEY` | `DataController` | Full S3 key of the file to process |
| `REGION` | `DataController` | AWS region (passed to Textract client) |
| `OpenSearchURL` | `DataController` (commented) | OpenSearch domain hostname |
| `OPENSEARCH_INDEX` | `DataController` (commented) | Index name for document storage |
| `SSM_OPEN_ARN` | `SecretManager.get_config()` | ARN of the AWS Secrets Manager secret holding OpenSearch credentials |

---

## 13. Error Handling Patterns

| Component | Pattern | Behaviour |
|-----------|---------|-----------|
| `BedrockAdapter.generate_embedding` | Retry with backoff | 3 retries on throttling; immediate raise on other errors |
| `BedrockAdapter.generate_batch_embeddings` | Per-item isolation | Failed items logged and skipped; successful results returned |
| `OpenSearchAdapter.bulk_store_documents` | Retry per batch | Up to `max_retries` attempts per batch; exponential backoff; failed batches counted |
| `TextractAdapter.analyze_document` | Catch-all + re-raise | Logs and re-raises as `RuntimeError` |
| `DataCore.csv_file_processor` | Batch-level isolation | Failed batches logged and skipped; partial results stored |
| `DataCore._fallback_individual_processing` | Row-level isolation | Failed rows counted and skipped |
| `SecretManager.get_secret` | Throttle detection | Distinguishes throttling from other `ClientError`; raises `RuntimeError` in both cases |
| `SecretManager.health_check` | Non-raising probe | Returns status dict instead of raising; safe for health endpoints |
| `PDFParser.extract_pages` | Per-page image fallback | Image extraction failure sets `image_bytes = None`; page text is still returned |
| `PDFReaderByPyMuPDF.extract_pages` | No explicit error handling | Exceptions propagate to caller |
| `S3Adapter.upload_image` | No explicit error handling | Exceptions propagate to caller (`DataCore.pdf_file_processor`) |
