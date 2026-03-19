This file contains targeted instructions for AI coding agents working on the ext-3Csolutions-backend repository.

Be concise. Follow the project's concrete patterns and file layout. When in doubt, run tests or start the local service to validate changes.

Key facts and high-level architecture
- This repo contains a small AWS Lambda + FastAPI service in the folder `ext-3csolotions-s3presignedurl-lambda/`.
- Entry point: `main.py` creates a FastAPI app, mounts the router defined in `src/controller/threecsolutionController.py` under the path `/api/v1/threecsoltions`, and exposes a Lambda handler `lambda_handler` (via Mangum).
- The code follows a controller -> service -> core -> adapter layering:
  - Controller: `src/controller/threecsolutionController.py` (FastAPI router, request handling, logging)
  - Service: `src/service/threecsolutionservice.py` (business logic, orchestrates core/adapter)
  - Core: `src/core/threecsolutioncore.py` (stateless core operations)
  - Adapter: `src/adpater/s3service.py` (external systems, e.g., S3 client wrapper)

Patterns and conventions to preserve
- Minimal dependency injection: constructors get `logger`, and adapters accept low-level clients (e.g., `S3Service(s3_client, logger)`). Preserve these signatures.
- Logging: modules accept a `logger` and call `logger.info(...)` on init. Use the same pattern for new modules.
- Synchronous vs asynchronous: Controllers and service health-check are async, while `core.health_check()` is currently synchronous. Keep async boundaries where present; prefer `async def` for controller/service endpoints and `def` for simple core utilities unless you need I/O.
- Project layout must remain under `src/` for import paths used by `main.py` (it appends `./src/` to sys.path). Avoid moving modules outside `src/` unless you update `main.py` accordingly.

Integration and external dependencies
- AWS Lambda: `main.py` is Lambda-compatible. The Dockerfile uses `public.ecr.aws/lambda/python:3.12` and sets the handler to `main.lambda_handler`.
- AWS SDK: `boto3` and `botocore` are declared in `requirements.txt`. `src/adpater/s3service.py` expects an `s3_client` with `generate_presigned_url` (boto3-style client).
- Mangum: `mangum` adapts FastAPI to Lambda; keep `handler = Mangum(app)` intact.

Local dev, build, and debug commands
- Install dependencies for local runs (venv + pip):
  - Create venv: `python -m venv .venv` then `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process; .\.venv\Scripts\Activate.ps1` (PowerShell)
  - Install: `pip install -r ext-3csolotions-s3presignedurl-lambda\requirements.txt`
- Run with Uvicorn for local API testing (Mangum is only for Lambda):
  - From repo root: `uvicorn ext-3csolotions-s3presignedurl-lambda.main:app --reload --port 8000 --root-path "/api/v1"`
  - Health endpoint: GET http://localhost:8000/api/v1/threecsoltions/health
- Run as Lambda in Docker (matches production image):
  - Build: `docker build -t threec-lambda ext-3csolotions-s3presignedurl-lambda/`
  - Run (invoking handler via AWS Lambda runtime image requires AWS SAM or `aws-lambda-ric`); but you can run the container to inspect files: `docker run --rm -it threec-lambda powershell` or use SAM for proper Lambda invocation.

What to change and how to verify
- When adding endpoints, update the router in `src/controller/threecsolutionController.py`. Use the `controller` instance pattern already present. Keep exception handling and HTTPException usage consistent.
- When adding business logic, prefer adding methods to `src/service/threecsolutionservice.py` and call shared logic in `src/core/threecsolutioncore.py` for pure computations.
- For AWS interactions, add adapters under `src/adpater/` and keep adapter constructors accepting low-level clients. Example: `S3Service.generate_presigned_url(bucket, key, expiration)`.
- Validate changes locally with Uvicorn and by calling the endpoint; inspect logs printed by the service. For Lambda-level validation, build the Docker image and use SAM or AWS console.

Files to inspect for examples
- `ext-3csolotions-s3presignedurl-lambda/main.py` — app setup, CORS, middleware, Mangum handler
- `ext-3csolotions-s3presignedurl-lambda/scr/controller/threecsolutionController.py` — router, controller pattern, logging and error handling
- `ext-3csolotions-s3presignedurl-lambda/scr/service/threecsolutionservice.py` — service layer calling core
- `ext-3csolotions-s3presignedurl-lambda/scr/core/threecsolutioncore.py` — simple core methods
- `ext-3csolotions-s3presignedurl-lambda/scr/adpater/s3service.py` — S3 adapter example

Style and code generation rules for AI edits
- Preserve function/method signatures and logging patterns. If you must change a public signature, update all call sites.
- Keep changes minimal and focused per PR. Create a single logical change per branch.
- Use existing filesystem layout and naming; adapters go into `src/adpater/`, services into `src/service/`, core logic into `src/core/`, controllers into `src/controller/`.
- Tests: repository currently has no tests. If adding tests, mirror the module structure under `tests/` and show how to run them in the README.

If you are unsure or a change touches runtime behavior (Lambda vs local server), add a short runnable verification snippet in the PR description showing the endpoint and expected response.

Need clarification?
- If anything in this document is unclear or you want a different level of detail (examples of new endpoints, test harness, or CI steps), say which area you want expanded and I'll update this file.
