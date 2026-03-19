# Technical Knowledge Transfer — ext-3csolotions-s3presignedurl-lambda

---

## Table of Contents

1. [Technology Stack & Dependencies](#1-technology-stack--dependencies)
2. [Project Structure](#2-project-structure)
3. [Entry Point — `main.py`](#3-entry-point--mainpy)
4. [Environment Configuration — `src/env/environmentVariables.py`](#4-environment-configuration)
5. [Exception Hierarchy — `src/exceptions/custom_exceptions.py`](#5-exception-hierarchy)
6. [Authentication Middleware — `src/middleware/auth.py`](#6-authentication-middleware)
7. [Request & Response Models — `src/model/`](#7-request--response-models)
8. [Shared Validator — `src/model/validators/unique_id_validator.py`](#8-shared-validator)
9. [Utilities — `src/utils/`](#9-utilities)
10. [AWS Adapters — `src/adapter/`](#10-aws-adapters)
11. [Core Logic — `src/core/threecsolutioncore.py`](#11-core-logic)
12. [Controller — `src/controller/threecsolutionController.py`](#12-controller)
13. [Request Flow — End to End](#13-request-flow--end-to-end)
14. [Error Handling Chain](#14-error-handling-chain)
15. [IAM Permission Requirements](#15-iam-permission-requirements)

---

## 1. Technology Stack & Dependencies

| Package | Version | Role |
|---|---|---|
| `fastapi` | 0.109.2 | HTTP API framework |
| `pydantic` | 2.6.1 | Request/response schema validation |
| `mangum` | 0.17.0 | Adapter that wraps FastAPI as an AWS Lambda handler |
| `boto3` | 1.34.52 | AWS SDK — S3 and DynamoDB client |
| `botocore` | 1.34.52 | Low-level AWS SDK core (retry, config, exceptions) |

**Runtime:** Python 3.x on AWS Lambda (container image deployment).
**Deployment model:** API Gateway → Lambda → Mangum → FastAPI.
**Excluded packages:** `uvicorn` (local-only), `python-multipart` (no file upload through Lambda), `email-validator` (no email fields).

---

## 2. Project Structure

```
ext-3csolotions-s3presignedurl-lambda/
├── main.py                                   ← FastAPI app, middleware, Lambda handler
├── requirements.txt
└── src/
    ├── adapter/
    │   ├── s3service.py                      ← S3 client wrapper
    │   └── dynamodbservice.py                ← DynamoDB client wrapper
    ├── controller/
    │   └── threecsolutionController.py       ← Route definitions, HTTP I/O
    ├── core/
    │   └── threecsolutioncore.py             ← Business logic, orchestration
    ├── env/
    │   └── environmentVariables.py           ← Lambda env var loader
    ├── exceptions/
    │   └── custom_exceptions.py              ← Typed exception hierarchy
    ├── middleware/
    │   └── auth.py                           ← API key bearer authentication
    ├── model/
    │   ├── presignurlInputValidator.py
    │   ├── presignedUrlResponseValidator.py
    │   ├── downloadInputValidator.py
    │   ├── downloadResponseValidator.py
    │   ├── monitorInputValidator.py
    │   ├── monitorResponseValidator.py
    │   ├── recentRequestsResponseValidator.py
    │   └── validators/
    │       └── unique_id_validator.py        ← Shared unique_id regex validator
    └── utils/
        ├── s3helper.py                       ← S3 key builder, filename sanitizer
        └── file_type_detector.py             ← Magic-byte file type detection
```

**Dependency flow (top-down, no circular imports):**

```
main.py
  └── controller
        └── core
              ├── adapter/s3service
              ├── adapter/dynamodbservice
              └── utils/s3helper
        └── model/*
        └── middleware/auth
        └── env/environmentVariables
        └── exceptions/custom_exceptions
```

---

## 3. Entry Point — `main.py`

### Module-level setup

#### `request_id_var: ContextVar[str]`
- **Type:** `contextvars.ContextVar`
- **Default:** empty string `''`
- **Purpose:** Stores the correlation/request ID per async execution context. Because Lambda handles concurrent requests in separate execution contexts, `ContextVar` is safe — each request gets its own isolated value without thread-local issues.

---

### Class: `JSONFormatter`

**Inherits:** `logging.Formatter`
**Purpose:** Formats every log record as a single-line JSON object, enabling structured querying in AWS CloudWatch Logs Insights.

#### `JSONFormatter.format(record: logging.LogRecord) -> str`

**Parameters:**
- `record` — standard Python `LogRecord` object containing level, message, function name, line number, and optional exception info.

**Returns:** A JSON string with the following fields:

| Field | Source | Always present |
|---|---|---|
| `timestamp` | `datetime.utcnow().isoformat() + 'Z'` | Yes |
| `level` | `record.levelname` | Yes |
| `logger` | `record.name` | Yes |
| `message` | `record.getMessage()` | Yes |
| `function` | `record.funcName` | Yes |
| `line` | `record.lineno` | Yes |
| `request_id` | `request_id_var.get()` | Only if non-empty |
| `exception` | `self.formatException(record.exc_info)` | Only if exception present |

**Behaviour:** Uses `request_id_var.get()` to inject the active request's correlation ID into every log line produced during that request's execution, enabling full cross-service log correlation by filtering on `request_id` in CloudWatch.

---

### Logging configuration (module-level)

```python
handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.root.addHandler(handler)
logging.root.setLevel(logging.INFO)
```

- A single `StreamHandler` (stdout) is attached to the root logger.
- All loggers in every module inherit this handler automatically.
- Level is set to `INFO`; `DEBUG` lines appear only if the root level is lowered.

---

### FastAPI application

```python
app = FastAPI(root_path="/api/v1", ...)
```

- `root_path="/api/v1"` — tells FastAPI that API Gateway prepends `/api/v1` to all routes. This is required for correct URL generation in OpenAPI docs and redirects when deployed behind API Gateway.

---

### Middleware: `add_correlation_id`

**Type:** ASGI middleware (`@app.middleware("http")`)
**Signature:** `async def add_correlation_id(request: Request, call_next) -> Response`

**Parameters:**
- `request` — incoming FastAPI `Request` object.
- `call_next` — callable that passes the request to the next middleware or route handler and returns a `Response`.

**Logic:**
1. Reads the `X-Request-ID` header from the incoming request. If absent, generates a new UUID4 string.
2. Sets `request_id_var` to this value for the duration of the request — all log lines produced during this request will include the same `request_id`.
3. Calls `call_next(request)` to process the request through the stack.
4. Writes the same `X-Request-ID` value into the response headers before returning.

**Effect:** Every response carries back the same `X-Request-ID` the client sent (or a server-generated one), enabling client-side log correlation.

---

### CORS Middleware

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=env.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
    max_age=3600
)
```

- `allow_origins` — read from `allowed_origins` env var (comma-separated list). Only requests from these origins are allowed cross-origin.
- `allow_methods` — explicitly whitelisted; `PUT`, `DELETE`, `PATCH` are blocked.
- `allow_headers` — only three headers are explicitly permitted in preflight responses.
- `max_age=3600` — browsers cache the preflight `OPTIONS` response for 1 hour, reducing preflight round-trips.

---

### Router registration

```python
app.include_router(threec_router, prefix="/threecsolutions")
```

All routes defined in `threecsolutionController.py` are mounted under `/threecsolutions`. Combined with `root_path="/api/v1"`, full paths become `/api/v1/threecsolutions/{route}`.

---

### Root route

```python
@app.get("/")
async def read_root():
    return {"message": "3C Solutions API is running", "status": "healthy"}
```

No authentication. No dependency on any service. Used for basic reachability checks.

---

### Lambda handler

```python
handler = Mangum(app)

def lambda_handler(event, context):
```

**Two objects named `handler`:**
- `handler = Mangum(app)` — the Mangum ASGI adapter (module-level).
- `handler = logging.StreamHandler()` earlier is a different local variable in the logging setup block. At module-level, the last assignment wins. The `lambda_handler` function below captures the Mangum instance via closure.

#### `lambda_handler(event: dict, context: LambdaContext) -> dict`

**Parameters:**
- `event` — API Gateway proxy event dict. Contains `path`, `httpMethod`, `headers`, `body`, `requestContext`, etc.
- `context` — Lambda runtime context object (function name, remaining time, etc.).

**Returns:** API Gateway-compatible response dict `{ statusCode, headers, body }`.

**Logic:**
1. Logs invocation start with `requestId` from `event['requestContext']`, `path`, and `httpMethod`.
2. Calls `handler(event, context)` — the Mangum adapter translates the Lambda event into an ASGI scope, runs it through FastAPI, and converts the response back to an API Gateway dict.
3. Logs invocation complete with the returned `statusCode`.

**Note:** Mangum handles all ASGI lifecycle steps (startup, request, shutdown). `lambda_handler` is a thin wrapper that adds structured logging around the Mangum call.

---

## 4. Environment Configuration

**File:** `src/env/environmentVariables.py`

### Class: `EnvironmentVariables`

Loaded once at application startup in `main.py` and passed into every service/core constructor.

#### `__init__(self, logger)`

Reads the following environment variables:

| Attribute | Env Var | Default | Type | Description |
|---|---|---|---|---|
| `aws_region` | `aws_region` | `"us-east-1"` | `str` | AWS region for boto3 clients |
| `s3_bucket` | `s3_bucket` | `"my-default-bucket"` | `str` | Target S3 bucket name |
| `presigned_url_expiration` | `presign_ttl_seconds` | `3600` | `int` | Presigned URL TTL in seconds |
| `dynamodb_table` | `dynamodb_table` | `"PipelineStatus"` | `str` | DynamoDB table for pipeline status |
| `allowed_origins` | `allowed_origins` | `"http://localhost:3000"` | `List[str]` | CORS origin whitelist (comma-separated, split into list) |

**`allowed_origins` parsing:**
```python
allowed_origins_str = os.getenv("allowed_origins", "http://localhost:3000")
self.allowed_origins = [origin.strip() for origin in allowed_origins_str.split(",") if origin.strip()]
```
Empty entries after splitting are discarded. Whitespace is stripped from each origin.

---

## 5. Exception Hierarchy

**File:** `src/exceptions/custom_exceptions.py`

All exceptions form a single inheritance tree rooted at `ThreeCSolutionError`. Each exception carries a `status_code` that maps directly to an HTTP status code, allowing the controller to construct `HTTPException` responses without any conditional mapping logic.

### Base class: `ThreeCSolutionError(Exception)`

**Constructor:** `__init__(self, message: str, status_code: int = 500, original_error: Optional[Exception] = None)`

| Attribute | Type | Description |
|---|---|---|
| `message` | `str` | Human-readable description (used in HTTP response for 4xx; suppressed for 5xx) |
| `status_code` | `int` | HTTP status code |
| `original_error` | `Optional[Exception]` | Original low-level exception (logged server-side only; never sent to client) |

---

### Derived exception classes

| Class | `status_code` | Default message | Raised when |
|---|---|---|---|
| `NotFoundError` | 404 | `"Resource not found"` | Requested S3 file or DynamoDB record does not exist |
| `ValidationError` | 400 | `"Validation failed"` | Input fails business-level validation |
| `UnauthorizedError` | 401 | `"Unauthorized"` | Missing or invalid API key |
| `ForbiddenError` | 403 | `"Forbidden"` | Authenticated but lacks permission |
| `S3ServiceError` | 500 | `"S3 service error"` | Any boto3 S3 operation failure |
| `DynamoDBServiceError` | 500 | `"DynamoDB service error"` | Any boto3 DynamoDB operation failure |
| `ServiceUnavailableError` | 503 | `"Service unavailable"` | Dependent service (S3, DynamoDB) is unreachable |
| `InternalServerError` | 500 | `"Internal server error"` | Unexpected application-level failure |

All derived constructors accept the same `(message, original_error)` parameters and pass them to `ThreeCSolutionError.__init__` with a fixed `status_code`.

---

## 6. Authentication Middleware

**File:** `src/middleware/auth.py`

### Module-level cache

```python
_CACHED_API_KEY = os.getenv("API_KEY")
```

The API key is read from the environment **once** at module load time and cached. This prevents repeated `os.getenv` calls on every request and reduces attack surface.

---

### `verify_api_key`

**Type:** FastAPI dependency (used with `Depends()`)
**Signature:** `async def verify_api_key(credentials: Optional[HTTPAuthorizationCredentials] = Security(security)) -> Optional[str]`

**Parameters:**
- `credentials` — automatically populated by `HTTPBearer(auto_error=False)`. Contains `scheme` (`"Bearer"`) and `credentials` (the token string). Is `None` if no `Authorization` header is present. `auto_error=False` prevents FastAPI from auto-rejecting missing headers — the function handles that manually.

**Returns:** The validated API key string, or `None` if auth is disabled.

**Raises:**
- `HTTPException(401)` with `WWW-Authenticate: Bearer` header if:
  - `_CACHED_API_KEY` is set AND `credentials` is `None` (missing header).
  - `_CACHED_API_KEY` is set AND `hmac.compare_digest(credentials.credentials, _CACHED_API_KEY)` returns `False` (wrong key).

**Logic flow:**

```
_CACHED_API_KEY is falsy?
  └─ Yes → return None (auth disabled, all requests pass through)
  └─ No  → credentials present?
              └─ No  → raise 401 "Missing authentication credentials"
              └─ Yes → hmac.compare_digest match?
                          └─ No  → raise 401 "Invalid API key"
                          └─ Yes → return credentials.credentials
```

**Security detail — constant-time comparison:**
`hmac.compare_digest(a, b)` runs in time proportional to the length of the strings, not their content. Standard `!=` short-circuits on the first differing character, leaking information about how many leading characters of the key are correct. `compare_digest` prevents this timing side-channel.

**Applied to routes:** `/presign`, `/download`, `/monitor`, `/recent-requests`. The `/health` and root `/` routes do not use this dependency.

---

## 7. Request & Response Models

All models use **Pydantic v2** `BaseModel`. FastAPI automatically deserializes request JSON into these models and serializes response objects back to JSON.

---

### `PresignRequest` — `src/model/presignurlInputValidator.py`

**Used by:** `POST /presign`

| Field | Type | Constraints | Default |
|---|---|---|---|
| `filename` | `str` | min_length=1, max_length=255, custom validator | — (required) |
| `content_type` | `Literal[...]` | Must be one of four allowed MIME types | `"application/zip"` |

**Allowed content types (whitelist):**
- `application/zip`
- `application/json`
- `application/pdf`
- `text/plain`

#### `validate_filename(cls, v: str) -> str` (Pydantic `@validator`)

Runs after the base length check. Rejects the filename if:
1. Contains `..`, `/`, or `\` — path traversal prevention.
2. Contains any of `< > : " | ? * \0 \n \r` — OS-unsafe characters.
3. Starts with `.` — hidden file rejection.
4. Contains any character with `ord(char) < 32` — control character rejection.

Returns the string unchanged if all checks pass; raises `ValueError` on failure (Pydantic converts this to a 422 response automatically).

---

### `PresignResponse` — `src/model/presignedUrlResponseValidator.py`

**Used by:** `POST /presign` response

| Field | Type | Default | Description |
|---|---|---|---|
| `method` | `str` | `"PUT"` | HTTP method the client must use when uploading to the URL |
| `bucket` | `str` | — | S3 bucket name |
| `key` | `str` | — | Full S3 object key |
| `upload_url` | `str` | — | Pre-signed S3 PUT URL |
| `expires_in` | `int` | — | TTL of the URL in seconds |

---

### `DownloadRequest` — `src/model/downloadInputValidator.py`

**Used by:** `POST /download`

| Field | Type | Constraints | Description |
|---|---|---|---|
| `unique_id` | `str` | min_length=10, max_length=100, regex validator | The S3 folder identifier under `output/` |

Uses `validate_unique_id_format` (see §8) as a Pydantic validator via `validator('unique_id', allow_reuse=True)`.

---

### `DownloadResponse` — `src/model/downloadResponseValidator.py`

**Used by:** `POST /download` response

| Field | Type | Default | Description |
|---|---|---|---|
| `method` | `str` | `"GET"` | HTTP method the client must use to download |
| `bucket` | `str` | — | S3 bucket name |
| `key` | `str` | — | Resolved S3 object key (may include timestamp in filename) |
| `download_url` | `str` | — | Pre-signed S3 GET URL |
| `expires_in` | `int` | — | TTL of the URL in seconds |

---

### `MonitorRequest` — `src/model/monitorInputValidator.py`

**Used by:** `POST /monitor`

| Field | Type | Constraints | Description |
|---|---|---|---|
| `unique_id` | `str` | min_length=10, max_length=100, regex validator | DynamoDB partition key (`request_id`) |

Same `validate_unique_id_format` validator as `DownloadRequest`.

---

### `MonitorResponse` — `src/model/monitorResponseValidator.py`

**Used by:** `POST /monitor` response

| Field | Type | Optional | Description |
|---|---|---|---|
| `request_id` | `str` | No | Pipeline job ID |
| `stage` | `str` | No | Current pipeline stage name |
| `status` | `str` | No | Status within the stage (e.g., `RUNNING`, `COMPLETED`) |
| `progress` | `int` | No | Number of units completed |
| `total` | `int` | No | Total units in the stage |
| `message` | `str` | No | Human-readable status message |
| `last_updated` | `str` | No | ISO timestamp of last update |
| `percentage` | `int` | Yes (`None`) | Computed: `(progress / total) * 100`, or `0` if total is zero |

---

### `RequestItem` and `RecentRequestsResponse` — `src/model/recentRequestsResponseValidator.py`

**Used by:** `GET /recent-requests` response

#### `RequestItem`

| Field | Type | Optional | Description |
|---|---|---|---|
| `request_id` | `str` | No | Unique pipeline job ID |
| `status` | `str` | No | Last known status |
| `last_updated` | `str` | No | ISO timestamp of last DynamoDB write |
| `folder_name` | `str` | Yes (`None`) | S3 folder name associated with the request |

#### `RecentRequestsResponse`

| Field | Type | Description |
|---|---|---|
| `requests` | `List[RequestItem]` | Ordered list (newest first) of recent pipeline jobs |
| `count` | `int` | Number of items in `requests` |

---

## 8. Shared Validator

**File:** `src/model/validators/unique_id_validator.py`

### `UNIQUE_ID_PATTERN`

```python
r'^[a-f0-9]{32}_\d{8}T\d{6}Z$'
```

Matches a string of exactly:
- 32 lowercase hex characters (UUID4 without hyphens)
- Literal `_`
- 8 digits (YYYYMMDD)
- Literal `T`
- 6 digits (HHMMSS)
- Literal `Z`

Example: `17eeb4ec5aa947708990cb220295e4c4_20260205T181422Z`

---

### `validate_unique_id_format(v: str) -> str`

**Parameters:**
- `v` — the `unique_id` string submitted in the request body.

**Returns:** `v` unchanged if it passes the regex.

**Raises:** `ValueError` with `UNIQUE_ID_ERROR_MSG` if the regex does not match. Pydantic converts this to an HTTP 422 Unprocessable Entity response with the error message in the body.

**Reuse:** This function is declared with `allow_reuse=True` so it can be attached to both `DownloadRequest` and `MonitorRequest` via `validator('unique_id', allow_reuse=True)(validate_unique_id_format)`.

---

## 9. Utilities

### 9.1 `S3Helper` — `src/utils/s3helper.py`

Stateless helper that builds S3 keys and sanitizes filenames. Instantiated once in the controller and injected into the core.

#### `__init__(self, logger, env)`
Stores references to the logger and `EnvironmentVariables` instance. No network connections are made.

---

#### `sanitize_filename(self, name: str, content_type: Optional[str] = None) -> str`

**Purpose:** Produces a filesystem- and S3-safe filename string.

**Parameters:**
- `name` — raw filename string from the client (may include path separators).
- `content_type` — MIME type string used to enforce the `.zip` extension.

**Returns:** Sanitized filename string.

**Raises:** `Exception("Could not sanitize filename")` if any internal step fails.

**Internal logic:**
1. **Basename extraction:** Splits on `/` and `\`, takes the last segment to strip any path prefix the client may have included.
2. **Whitespace strip:** `str.strip()` removes leading/trailing whitespace.
3. **Character substitution:** `re.sub(r"[^A-Za-z0-9._-]", "_", base)` replaces any character not in `[A-Za-z0-9._-]` with `_`. This covers spaces, Unicode characters, and shell-special characters.
4. **Empty check:** If the result is empty (e.g., input was all special chars), raises `ValueError`.
5. **ZIP extension enforcement:** If `content_type` is `"application/zip"` or `"application/x-zip-compressed"` and the sanitized name does not already end with `.zip` (case-insensitive), appends `.zip`. This is required by the downstream AI pipeline to identify ZIP archives.

---

#### `build_unique_key(self, filename: str, content_type: Optional[str] = None) -> str`

**Purpose:** Constructs a globally unique S3 object key for uploaded files.

**Parameters:**
- `filename` — original filename (passed through `sanitize_filename`).
- `content_type` — MIME type (forwarded to `sanitize_filename` for extension enforcement).

**Returns:** S3 key string in the format:
```
input/{uuid_hex}_{timestamp}/{sanitized_filename}
```
Example: `input/17eeb4ec5aa947708990cb220295e4c4_20260205T181422Z/report.zip`

**Internal logic:**
1. Generates UTC timestamp with `datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")`.
2. Generates a 32-char hex UUID with `uuid4().hex` (no hyphens).
3. Calls `sanitize_filename` to produce a safe base name.
4. Concatenates: `f"input/{u}_{now}/{safe_file}"`.

**Raises:** `Exception("Could not build unique S3 key")` wrapping any internal error.

**Design note:** The `{uuid}_{timestamp}` segment in the key path is the same string that becomes `unique_id` in subsequent `DownloadRequest` and `MonitorRequest` calls. Clients receive this `key` from the `PresignResponse` and parse the `unique_id` from it.

---

### 9.2 `file_type_detector` — `src/utils/file_type_detector.py`

A stateless module (no class) providing magic-byte-based file type detection. Magic bytes are the first few bytes of a file that identify its format, independent of the file extension.

#### `MAGIC_BYTES` constant

```python
MAGIC_BYTES = {
    'zip': [b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08'],
    'pdf': [b'%PDF-'],
    'json': [b'{', b'['],
}
```

| Type | Signatures | Notes |
|---|---|---|
| `zip` | 3 PK signatures | Standard, empty, and spanned ZIP variants |
| `pdf` | `%PDF-` | PDF header |
| `json` | `{` or `[` | Object or array opening; checked after stripping leading whitespace |

---

#### `detect_file_type(file_bytes: bytes, max_check_bytes: int = 512) -> FileType`

**Parameters:**
- `file_bytes` — raw file content bytes.
- `max_check_bytes` — number of bytes from the start to inspect (default 512). Avoids loading large files entirely.

**Returns:** `FileType` literal: `'zip'`, `'pdf'`, `'json'`, or `'unknown'`.

**Logic:**
1. Returns `'unknown'` immediately for empty input.
2. Slices `header = file_bytes[:max_check_bytes]`.
3. Checks ZIP — iterates the three PK signatures, returns `'zip'` on first `header.startswith` match.
4. Checks PDF — returns `'pdf'` if `header.startswith(b'%PDF-')`.
5. Checks JSON — strips leading whitespace from `header` (`lstrip()`), then checks for `{` or `[`. Whitespace stripping is intentional: valid JSON files may start with spaces or BOM characters.
6. Returns `'unknown'` if no signature matched.

---

#### `validate_file_type(file_bytes: bytes, expected_type: FileType) -> Tuple[bool, str]`

**Parameters:**
- `file_bytes` — raw file content.
- `expected_type` — the type the file is expected to be.

**Returns:** `(True, "")` on match, `(False, error_message)` on mismatch or unknown.

**Logic:**
1. Calls `detect_file_type(file_bytes)`.
2. If detected is `'unknown'`: returns `(False, "Unable to detect file type. Expected {expected_type}")`.
3. If detected does not equal `expected_type`: returns `(False, "File content is {detected} but expected {expected_type}")`.
4. Otherwise returns `(True, "")`.

---

#### `is_zip_file(file_bytes: bytes) -> bool`
Convenience wrapper: returns `detect_file_type(file_bytes) == 'zip'`.

#### `is_pdf_file(file_bytes: bytes) -> bool`
Convenience wrapper: returns `detect_file_type(file_bytes) == 'pdf'`.

---

#### `get_content_type_from_bytes(file_bytes: bytes) -> str`

**Returns:** MIME type string derived from detected file type.

| Detected type | Returned MIME |
|---|---|
| `'zip'` | `"application/zip"` |
| `'pdf'` | `"application/pdf"` |
| `'json'` | `"application/json"` |
| `'unknown'` | `"application/octet-stream"` |

---

## 10. AWS Adapters

Both adapters share the same boto3 `Config` pattern:

```python
Config(
    max_pool_connections=50,   # Supports Lambda burst concurrency
    connect_timeout=5,         # Fail-fast on TCP connect
    read_timeout=30,           # Allow time for S3 reads
    retries={'max_attempts': 3, 'mode': 'adaptive'}
)
```

`adaptive` retry mode applies exponential backoff with jitter and respects `Retry-After` headers from AWS, reducing thundering-herd effects under throttling.

---

### 10.1 `S3Service` — `src/adapter/s3service.py`

#### `__init__(self, logger, env)`

Creates a `boto3` S3 client with `s3v4` signature (required for certain AWS regions and for SSE-S3 operations). The client is stored as `self.s3_client` and reused across requests (connection pool is shared across Lambda invocations within the same execution environment).

---

#### `list_objects(self, bucket: str, prefix: str) -> List[str]`

**Parameters:**
- `bucket` — S3 bucket name.
- `prefix` — S3 key prefix to list under (e.g., `"output/abc123_20260205T181422Z/"`).

**Returns:** List of full S3 key strings found under the prefix. Empty list if no objects exist under the prefix (the `Contents` key is absent from the response in this case).

**Internal logic:**
```python
response = self.s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)
keys = [obj['Key'] for obj in response.get('Contents', [])]
```

**Edge cases:**
- If the prefix does not exist in S3, `list_objects_v2` returns a response with no `Contents` key → the method returns `[]`.
- If more than 1000 objects exist under the prefix, `list_objects_v2` paginates. This method does **not** handle pagination — it returns only the first page (1000 objects). For the use case of listing output files per job, this is not a practical concern.

**Exception mapping:**

| Exception caught | Raised as | Logged info |
|---|---|---|
| `ClientError` | `S3ServiceError` | AWS error code + full exception |
| `BotoCoreError` | `S3ServiceError` | Full exception |
| Any other | `S3ServiceError` | Full exception |

---

#### `generate_presigned_url(self, params: dict, clientMethod: str = 'put_object') -> str`

**Parameters:**
- `params` — dict passed directly to `generate_presigned_url` as `Params`. For uploads: `{"Bucket": ..., "Key": ..., "ContentType": ...}`. For downloads: `{"Bucket": ..., "Key": ...}`.
- `clientMethod` — S3 operation to pre-sign. `'put_object'` for upload URLs, `'get_object'` for download URLs.

**Returns:** A fully-signed HTTPS URL string containing authentication query parameters. The URL is valid for `env.presigned_url_expiration` seconds.

**Security note on logging:** Only the URL path is logged (`scheme://host/path`), not the query string. The query string contains the pre-signed authentication parameters (`X-Amz-Signature`, `X-Amz-Credential`, etc.) — logging these would expose exploitable credentials.

```python
parsed = urlparse(url)
safe_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
self.logger.info(f"Generated presigned URL: {safe_url} ...")
```

**Exception mapping:** Same three-tier pattern as `list_objects` (`ClientError` → `S3ServiceError`, `BotoCoreError` → `S3ServiceError`, generic → `S3ServiceError`).

---

### 10.2 `DynamoDBService` — `src/adapter/dynamodbservice.py`

#### `__init__(self, logger, env)`

Creates a low-level `boto3` DynamoDB **client** (not resource). The client API is 50–100ms faster than the resource abstraction because it skips the resource-level response parsing layer. Table name is stored as `self.table_name` from `env.dynamodb_table`.

---

#### `get_pipeline_status(self, request_id: str) -> Optional[dict]`

**Parameters:**
- `request_id` — DynamoDB partition key value (string).

**Returns:** Parsed Python `dict` of the DynamoDB item, or `None` if the item does not exist.

**Internal logic:**
```python
response = self.dynamodb_client.get_item(
    TableName=self.table_name,
    Key={'request_id': {'S': request_id}}
)
```

- The key is provided in DynamoDB wire format: `{'S': value}` for string types.
- If `'Item'` is in the response, calls `_parse_dynamodb_item` to convert it to a plain Python dict.
- If `'Item'` is absent, logs a warning and returns `None`.

**Exception mapping:** `ClientError` → `DynamoDBServiceError`, `BotoCoreError` → `DynamoDBServiceError`, generic → `DynamoDBServiceError`. All use exception chaining (`from e`) to preserve the original traceback in the server logs.

---

#### `_parse_dynamodb_item(self, dynamodb_item: dict) -> dict`

**Parameters:**
- `dynamodb_item` — raw DynamoDB client API item format: `{ "AttributeName": {"TypeCode": value}, ... }`.

**Returns:** Plain Python dict with native types.

**DynamoDB type code mapping:**

| DynamoDB type code | Python conversion |
|---|---|
| `S` | `str` (direct) |
| `N` | `int` if no `.` in value, else `float` (DynamoDB stores numbers as strings) |
| `BOOL` | `bool` (already Python bool from boto3) |
| `NULL` | `None` |
| `M` | Recursive call to `_parse_dynamodb_item` |
| `L` | List comprehension calling `_parse_dynamodb_value` on each element |
| Other | Raw value (fallback) |

**Recursive:** Map (`M`) and List (`L`) types trigger recursive parsing, supporting arbitrarily nested DynamoDB structures.

---

#### `_parse_dynamodb_value(self, value_dict: dict)`

**Parameters:**
- `value_dict` — a single DynamoDB typed value dict: `{"TypeCode": value}`.

**Returns:** Native Python value.

This is the element-level parser used for items inside a `L` (List) type. It handles `S`, `N`, `M`, and `L` — not `BOOL` or `NULL` (these would fall through to the raw value fallback).

---

#### `get_recent_requests(self, limit: int = 10) -> List[dict]`

**Parameters:**
- `limit` — maximum number of results to return (default 10).

**Returns:** List of dicts, each containing:
```python
{
    "request_id": str,
    "status": str,          # defaults to "unknown" if not present
    "last_updated": str,
    "folder_name": Optional[str]
}
```

**Internal logic:**
1. Calls `dynamodb_client.scan` with `ProjectionExpression` to fetch only 4 attributes:
   - `request_id`, `status` (aliased as `#st`), `last_updated`, `folder_name`
   - `status` requires an alias `#st` because `status` is a DynamoDB reserved word; `ExpressionAttributeNames` provides the mapping.
2. Parses each returned item with `_parse_dynamodb_item`.
3. Filters: only items that contain `request_id` and `last_updated` are included.
4. Sorts by `last_updated` descending (lexicographic sort works because `last_updated` is in ISO format `YYYY-MM-DDTHH:MM:SS`).
5. Returns `items[:limit]`.

**Scan limitation:** `scan` reads the entire table (eventually consistent). For large tables this has cost and latency implications. For this service it is acceptable because the dataset is bounded by active pipeline jobs.

**Exception mapping:** Same three-tier pattern as `get_pipeline_status`.

---

## 11. Core Logic

**File:** `src/core/threecsolutioncore.py`

The core class contains all business logic and orchestrates calls to adapters and utilities. It does not interact with HTTP directly — that is the controller's responsibility.

### Class: `ThreeCSolutionCore`

#### `__init__(self, logger, env, s3helper, s3service, dynamodbservice=None)`

Stores all injected dependencies. `dynamodbservice` is optional (`None` by default) because not all endpoints require DynamoDB. Methods that need it perform a `None` check before use.

---

#### `health_check(self) -> dict`

**Type:** `async`
**Returns:** `{"status": "healthy"}`
**No dependencies.** Used only to confirm the application layer is running.

---

#### `generate_presigned_url(self, request: PresignRequest) -> dict`

**Parameters:**
- `request` — validated `PresignRequest` model.

**Returns:** `{"key": str, "url": str}` where `key` is the full S3 object key and `url` is the pre-signed PUT URL.

**Logic:**
1. Calls `s3helper.build_unique_key(request.filename, request.content_type)` to produce the S3 key.
2. Builds params dict: `{"Bucket": bucket, "Key": key, "ContentType": request.content_type}`.
3. Calls `s3service.generate_presigned_url(params, clientMethod='put_object')`.
4. Returns `{"key": key, "url": url}`.

**Error handling:**
- If `e` already has `status_code` (is a `ThreeCSolutionError` subclass), re-raises as-is.
- Any other exception is wrapped in `InternalServerError("Could not generate presigned URL")`.

---

#### `_resolve_final_output_key(self, unique_id: str) -> str`

**Private method** (prefixed `_`). Called only by `generate_download_url`.

**Parameters:**
- `unique_id` — the S3 folder name under `output/`, matching the `unique_id` from the upload key.

**Returns:** The resolved S3 key string for the correct output file.

**Raises:**
- `ServiceUnavailableError` — if `s3service.list_objects` throws `S3ServiceError`.
- `NotFoundError` — if no matching `FINAL_OUTPUT*.json` file exists under the prefix.

**Resolution algorithm:**

```
prefix = "output/{unique_id}/"
ts_pattern = re.compile(r"FINAL_OUTPUT_(\d{8}_\d{6})\.json$")

1. Call s3service.list_objects(bucket, prefix)
   ├─ S3ServiceError → raise ServiceUnavailableError (user-safe message)
   └─ Success → all_keys (list of S3 key strings)

2. Log: full list of all_keys with count

3. For each key in all_keys:
   ├─ filename = key.split("/")[-1]
   ├─ ts_pattern.match(filename)? → add to timestamped[]
   └─ filename == "FINAL_OUTPUT.json"? → set has_plain = True

4. If timestamped is non-empty:
   ├─ latest_key = sorted(timestamped)[-1]   ← lexicographic = chronological for YYYYMMDD_HHMMSS
   ├─ Log: selected latest file + candidate count
   └─ return latest_key

5. If has_plain:
   ├─ Log: falling back to plain file
   └─ return "{prefix}FINAL_OUTPUT.json"

6. Log error: full all_keys list
   └─ raise NotFoundError (user-safe message)
```

**Why lexicographic sort works:** The timestamp format `YYYYMMDD_HHMMSS` is designed such that lexicographic ordering matches chronological ordering. The newest file will always sort last in ascending order, so `sorted(timestamped)[-1]` gives the most recently generated file.

**Debug logs emitted at each decision point:**

| Log level | Content |
|---|---|
| `DEBUG` | Listing objects under prefix |
| `INFO` | Full file list with count |
| `DEBUG` | Timestamped candidates + plain file presence flag |
| `INFO` | Selected file (timestamped or plain) |
| `ERROR` | Full object list when no match found |

---

#### `generate_download_url(self, request: DownloadRequest) -> dict`

**Parameters:**
- `request` — validated `DownloadRequest` model.

**Returns:** `{"key": str, "url": str}` where `key` is the resolved S3 key and `url` is the pre-signed GET URL.

**Logic:**
1. Calls `_resolve_final_output_key(request.unique_id)` to get the correct S3 key.
2. Builds params: `{"Bucket": bucket, "Key": key}`.
3. Calls `s3service.generate_presigned_url(params, clientMethod='get_object')` inside a nested `try/except S3ServiceError`.
   - On `S3ServiceError` → raises `ServiceUnavailableError("Unable to prepare the download link. Please try again later.")`.
4. Returns `{"key": key, "url": url}`.

**Outer exception handling:**
```python
except ThreeCSolutionError:
    raise   # propagate already-safe exceptions unchanged
except Exception as e:
    raise InternalServerError("An unexpected error occurred ...")
```

This two-level catch ensures that:
- All `ThreeCSolutionError` subclasses (which already carry user-safe messages) pass through to the controller.
- Any unexpected Python exceptions (e.g., `AttributeError`, `TypeError`) are caught and converted to a safe `InternalServerError` before reaching the controller.

---

#### `get_pipeline_status(self, request: MonitorRequest) -> dict`

**Parameters:**
- `request` — validated `MonitorRequest` model.

**Returns:** Dict matching `MonitorResponse` shape:
```python
{
    "request_id": str,
    "stage": str,
    "status": str,
    "progress": int,
    "total": int,
    "message": str,
    "last_updated": str,
    "percentage": int   # 0 if total == 0
}
```

**Logic:**
1. Checks `self.dynamodbservice is not None` → raises `ServiceUnavailableError` if `None`.
2. Calls `dynamodbservice.get_pipeline_status(request.unique_id)`.
3. If result is `None` → raises `NotFoundError`.
4. Computes `percentage = int((progress / total) * 100) if total > 0 else 0`. Division by zero is explicitly guarded.
5. Returns the assembled dict.

---

#### `get_recent_requests(self, limit: int = 10) -> dict`

**Parameters:**
- `limit` — passed to `dynamodbservice.get_recent_requests`.

**Returns:**
```python
{"requests": List[dict], "count": int}
```

**Logic:**
1. Checks `self.dynamodbservice is not None` → raises `ServiceUnavailableError` if `None`.
2. Calls `dynamodbservice.get_recent_requests(limit)`.
3. Returns `{"requests": result, "count": len(result)}`.

---

## 12. Controller

**File:** `src/controller/threecsolutionController.py`

The controller's role is to:
- Define HTTP routes and bind them to core methods.
- Map Pydantic request models to core calls.
- Map core return dicts to Pydantic response models.
- Translate `ThreeCSolutionError` exceptions to `HTTPException`.

---

### `_safe_detail(e: ThreeCSolutionError) -> str`

**Module-level function** (not part of any class).

**Parameters:**
- `e` — any `ThreeCSolutionError` subclass instance.

**Returns:** A string safe to include in the HTTP response body.

**Logic:**

```python
if e.status_code == 503:
    return "The service is temporarily unavailable. Please try again later."
if e.status_code >= 500:
    return "An unexpected error occurred. Please try again or contact support."
return e.message
```

| Status code range | Returned string | Rationale |
|---|---|---|
| 503 | Fixed string | S3/DynamoDB unreachable — user-friendly, no internals |
| 500+ | Fixed string | Server fault — never expose internal paths or AWS codes |
| 4xx | `e.message` | Client error — the message is deliberately user-friendly |

This function is the single enforcement point for the rule: **no internal error detail leaves the service in HTTP responses**.

---

### Class: `ThreeCSolutionController`

#### `__init__(self)`

Instantiates and wires all dependencies:
```
EnvironmentVariables → S3Helper, S3Service, DynamoDBService → ThreeCSolutionCore
```

Each dependency is created with the shared `logger` instance. The controller itself is not instantiated at import time (see lazy initialization below).

---

#### `health_check(self) -> dict`
Delegates directly to `core.health_check()`. No exception handling at this layer.

#### `generate_presigned_url(self, request: PresignRequest) -> PresignResponse`
Calls `core.generate_presigned_url(request)` and constructs `PresignResponse` from the returned dict plus `env.s3_bucket` and `env.presigned_url_expiration`.

#### `generate_download_url(self, request: DownloadRequest) -> DownloadResponse`
Calls `core.generate_download_url(request)` and constructs `DownloadResponse`. The `key` field in the response will reflect the resolved filename (with or without timestamp), giving the client visibility into which exact file was selected.

#### `get_pipeline_status(self, request: MonitorRequest) -> MonitorResponse`
Calls `core.get_pipeline_status(request)` and constructs `MonitorResponse`.

#### `get_recent_requests(self) -> RecentRequestsResponse`
Calls `core.get_recent_requests(limit=10)` and constructs `RecentRequestsResponse` with a typed list of `RequestItem`.

---

### Lazy initialization

```python
controller: Optional[ThreeCSolutionController] = None

def get_controller() -> ThreeCSolutionController:
    global controller
    if controller is None:
        controller = ThreeCSolutionController()
    return controller
```

`ThreeCSolutionController` is constructed on the **first request** after a cold start, not at import time. This matters because the controller's `__init__` creates boto3 clients, which involve network lookups (endpoint resolution, credential fetching). Deferring this reduces Lambda cold start latency by 1–3 seconds.

After the first construction, the same controller instance is reused for the lifetime of the Lambda execution environment (warm invocations).

---

### Route definitions

All routes are registered on `router = APIRouter()`. The router is mounted in `main.py` with prefix `/threecsolutions`, resulting in the full paths below.

---

#### `GET /health` → `health_check()`

No authentication. No request body.
Returns: `{"status": "healthy"}`

---

#### `POST /presign` → `generate_presigned_url(request, api_key)`

**Auth:** `Depends(verify_api_key)`
**Request body:** `PresignRequest`
**Response model:** `PresignResponse`
**Status:** 200

Exception handling:
```python
except ThreeCSolutionError as e:
    raise HTTPException(status_code=e.status_code, detail=_safe_detail(e))
except Exception as e:
    raise HTTPException(status_code=500, detail="Failed to generate presigned URL")
```

---

#### `POST /download` → `generate_download_url(request, api_key)`

**Auth:** `Depends(verify_api_key)`
**Request body:** `DownloadRequest`
**Response model:** `DownloadResponse`
**Status:** 200

Same exception handling pattern. `_safe_detail` ensures S3 listing errors and presign errors do not expose internal S3 paths or AWS error codes.

---

#### `POST /monitor` → `get_pipeline_status(request, api_key)`

**Auth:** `Depends(verify_api_key)`
**Request body:** `MonitorRequest`
**Response model:** `MonitorResponse`
**Status:** 200

---

#### `GET /recent-requests` → `get_recent_requests(api_key)`

**Auth:** `Depends(verify_api_key)`
**No request body.**
**Response model:** `RecentRequestsResponse`
**Status:** 200

---

## 13. Request Flow — End to End

### Upload flow (`POST /presign`)

```
Client
  │── POST /api/v1/threecsolutions/presign
  │   Headers: Authorization: Bearer {key}
  │   Body: {"filename": "data.zip", "content_type": "application/zip"}
  │
  ▼
API Gateway → Lambda Event
  │
  ▼
lambda_handler (main.py)
  │── Logs invocation start
  │
  ▼
Mangum (ASGI adapter)
  │
  ▼
add_correlation_id middleware
  │── Extract or generate X-Request-ID
  │── Set request_id_var (all logs tagged)
  │
  ▼
CORSMiddleware
  │── Validate Origin header
  │
  ▼
verify_api_key (FastAPI Dependency)
  │── Read Authorization: Bearer header
  │── hmac.compare_digest against cached key
  │
  ▼
generate_presigned_url route handler
  │── Pydantic validates PresignRequest
  │     ├─ filename: length, path traversal, dangerous chars, control chars
  │     └─ content_type: must be in ALLOWED_CONTENT_TYPES literal
  │
  ▼
ThreeCSolutionController.generate_presigned_url
  │
  ▼
ThreeCSolutionCore.generate_presigned_url
  │── S3Helper.build_unique_key
  │     ├─ sanitize_filename
  │     └─ format: "input/{uuid}_{timestamp}/{filename}"
  │── S3Service.generate_presigned_url (put_object)
  │
  ▼
PresignResponse → JSON
  {
    "method": "PUT",
    "bucket": "...",
    "key": "input/abc_20260205T181422Z/data.zip",
    "upload_url": "https://s3.amazonaws.com/...",
    "expires_in": 3600
  }
  │
  ▼
Client → PUT file directly to S3 using upload_url
```

---

### Download flow (`POST /download`)

```
Client
  │── POST /api/v1/threecsolutions/download
  │   Body: {"unique_id": "abc123_20260205T181422Z"}
  │
  ▼
[middleware chain — same as above]
  │
  ▼
generate_download_url route handler
  │── Pydantic validates DownloadRequest
  │     └─ unique_id: validate_unique_id_format regex
  │
  ▼
ThreeCSolutionCore.generate_download_url
  │── _resolve_final_output_key("abc123_20260205T181422Z")
  │     ├─ S3Service.list_objects("output/abc123_20260205T181422Z/")
  │     │   → logs full file list
  │     ├─ Filter: FINAL_OUTPUT_YYYYMMDD_HHMMSS.json  → timestamped[]
  │     │          FINAL_OUTPUT.json                  → has_plain
  │     └─ Priority: timestamped latest > plain > NotFoundError
  │
  │── S3Service.generate_presigned_url (get_object, resolved key)
  │
  ▼
DownloadResponse → JSON
  {
    "method": "GET",
    "bucket": "...",
    "key": "output/abc123_.../FINAL_OUTPUT_20260227_195302.json",
    "download_url": "https://s3.amazonaws.com/...",
    "expires_in": 3600
  }
  │
  ▼
Client → GET file directly from S3 using download_url
```

---

### Monitor flow (`POST /monitor`)

```
Client
  │── POST /api/v1/threecsolutions/monitor
  │   Body: {"unique_id": "abc123_20260205T181422Z"}
  │
  ▼
ThreeCSolutionCore.get_pipeline_status
  │── DynamoDBService.get_pipeline_status("abc123_20260205T181422Z")
  │     └─ dynamodb_client.get_item(Key={'request_id': {'S': unique_id}})
  │     └─ _parse_dynamodb_item → plain Python dict
  │── Compute percentage = (progress / total) * 100
  │
  ▼
MonitorResponse → JSON
  {
    "request_id": "abc123_20260205T181422Z",
    "stage": "EXTRACTION",
    "status": "RUNNING",
    "progress": 45,
    "total": 100,
    "message": "Processing files...",
    "last_updated": "2026-02-27T19:53:02Z",
    "percentage": 45
  }
```

---

## 14. Error Handling Chain

### Exception propagation path

```
AWS SDK (boto3)
  └─ ClientError / BotoCoreError
        └─ Adapter (S3Service / DynamoDBService)
              └─ raises S3ServiceError / DynamoDBServiceError
                    └─ Core (_resolve_final_output_key / generate_download_url)
                          ├─ catches S3ServiceError → raises ServiceUnavailableError
                          │   (message: user-safe, no S3 paths)
                          ├─ catches ThreeCSolutionError → re-raises unchanged
                          └─ catches Exception → raises InternalServerError
                                └─ Controller route handler
                                      ├─ ThreeCSolutionError → HTTPException(_safe_detail(e))
                                      └─ Exception → HTTPException(500, fixed string)
```

### What reaches the HTTP client

| Scenario | HTTP status | Body `detail` |
|---|---|---|
| Invalid `filename` (path traversal) | 422 | Pydantic validation error (field, message) |
| Invalid `unique_id` format | 422 | Pydantic validation error |
| Missing `Authorization` header | 401 | `"Missing authentication credentials"` |
| Wrong API key | 401 | `"Invalid API key"` |
| Output file not found in S3 | 404 | `"The output file for this request is not ready or does not exist."` |
| S3 listing fails (IAM, network) | 503 | `"The service is temporarily unavailable. Please try again later."` |
| S3 presign fails | 503 | `"Unable to prepare the download link. Please try again later."` |
| DynamoDB record not found | 404 | `"Pipeline status not found for request_id: ..."` |
| Any unhandled exception | 500 | `"An unexpected error occurred. Please try again or contact support."` |

### What stays server-side (logs only)

- AWS error codes (`AccessDenied`, `NoSuchBucket`, `ThrottlingException`)
- S3 key paths and bucket names
- DynamoDB table name
- Python exception types, stack traces
- `original_error` on every `ThreeCSolutionError`

---

## 15. IAM Permission Requirements

The Lambda execution role must grant the following permissions on the target S3 bucket and DynamoDB table:

### S3 permissions

| Action | Resource | Required by |
|---|---|---|
| `s3:PutObject` | `arn:aws:s3:::{bucket}/input/*` | `generate_presigned_url` (pre-signs PUT) |
| `s3:GetObject` | `arn:aws:s3:::{bucket}/output/*` | `generate_download_url` (pre-signs GET) |
| `s3:ListObjectsV2` | `arn:aws:s3:::{bucket}/output/*` | `_resolve_final_output_key` (lists output files) |

**Note:** `s3:ListObjectsV2` was added with the dynamic output file resolution feature. If this permission is absent, all `/download` requests will return 503.

### DynamoDB permissions

| Action | Resource | Required by |
|---|---|---|
| `dynamodb:GetItem` | `arn:aws:dynamodb:{region}:{account}:table/{table}` | `get_pipeline_status` |
| `dynamodb:Scan` | `arn:aws:dynamodb:{region}:{account}:table/{table}` | `get_recent_requests` |

### CloudWatch Logs permissions (standard Lambda)

| Action | Resource |
|---|---|
| `logs:CreateLogGroup` | `arn:aws:logs:*:*:*` |
| `logs:CreateLogStream` | `arn:aws:logs:*:*:log-group:/aws/lambda/{function}:*` |
| `logs:PutLogEvents` | `arn:aws:logs:*:*:log-group:/aws/lambda/{function}:*` |
