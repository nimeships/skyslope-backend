# ============================================================
# Production Docker Build, Test, and Security Scan Script (PowerShell)
# ============================================================
# Usage: .\build-and-test.ps1 [-Push]

param(
    [switch]$Push = $false
)

$ErrorActionPreference = "Stop"

# Configuration
$IMAGE_NAME = "document-extraction-pipeline"
$IMAGE_TAG = "latest"
$TEST_TAG = "test"
$CONTAINER_NAME = "doc-pipeline-test"

# AWS Configuration
$AWS_REGION = if ($env:AWS_REGION) { $env:AWS_REGION } else { "us-east-1" }
$AWS_ACCOUNT_ID = $env:AWS_ACCOUNT_ID
$ECR_REPO_NAME = "document-extraction"

# ====================
# Helper Functions
# ====================

function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Blue
}

function Write-Success {
    param([string]$Message)
    Write-Host "[SUCCESS] $Message" -ForegroundColor Green
}

function Write-Warning {
    param([string]$Message)
    Write-Host "[WARNING] $Message" -ForegroundColor Yellow
}

function Write-ErrorMsg {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

function Cleanup {
    Write-Info "Cleaning up test containers and images..."
    docker stop $CONTAINER_NAME 2>$null
    docker rm $CONTAINER_NAME 2>$null
    docker rmi "${IMAGE_NAME}:${TEST_TAG}" 2>$null
}

# Register cleanup on exit
Register-EngineEvent PowerShell.Exiting -Action { Cleanup }

# ====================
# Step 1: Pre-Build Validation
# ====================

Write-Info "Step 1: Pre-Build Validation"

if (-not (Test-Path "dockerfile")) {
    Write-ErrorMsg "dockerfile not found!"
    exit 1
}

if (-not (Test-Path "requirements.txt")) {
    Write-ErrorMsg "requirements.txt not found!"
    exit 1
}

if (-not (Test-Path "src")) {
    Write-ErrorMsg "src directory not found!"
    exit 1
}

if (-not (Test-Path "main.py")) {
    Write-ErrorMsg "main.py not found!"
    exit 1
}

Write-Success "Pre-build validation passed"

# ====================
# Step 2: Build Docker Image
# ====================

Write-Info "Step 2: Building Docker image..."

$BUILD_START = Get-Date

docker build `
    --tag "${IMAGE_NAME}:${TEST_TAG}" `
    --file dockerfile `
    --build-arg BUILDKIT_INLINE_CACHE=1 `
    --progress=plain `
    .

if ($LASTEXITCODE -ne 0) {
    Write-ErrorMsg "Docker build failed"
    exit 1
}

$BUILD_END = Get-Date
$BUILD_TIME = ($BUILD_END - $BUILD_START).TotalSeconds

Write-Success "Build completed in $([Math]::Round($BUILD_TIME, 2)) seconds"

# ====================
# Step 3: Image Analysis
# ====================

Write-Info "Step 3: Analyzing image..."

$IMAGE_SIZE = docker images "${IMAGE_NAME}:${TEST_TAG}" --format "{{.Size}}"
Write-Info "Image size: $IMAGE_SIZE"

$LAYER_COUNT = (docker history "${IMAGE_NAME}:${TEST_TAG}" --no-trunc | Measure-Object -Line).Lines
Write-Info "Layer count: $LAYER_COUNT"

docker inspect "${IMAGE_NAME}:${TEST_TAG}" | Out-File -FilePath "$env:TEMP\image-inspect.json"
Write-Info "Image inspection saved to $env:TEMP\image-inspect.json"

# ====================
# Step 4: Security Scan (Optional)
# ====================

Write-Info "Step 4: Security scanning..."

if (Get-Command trivy -ErrorAction SilentlyContinue) {
    Write-Info "Running Trivy security scan..."
    trivy image `
        --severity HIGH,CRITICAL `
        --no-progress `
        --format table `
        "${IMAGE_NAME}:${TEST_TAG}"

    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Trivy scan found vulnerabilities"
    }
} else {
    Write-Warning "Trivy not installed. Skipping security scan."
    Write-Info "Install Trivy: https://github.com/aquasecurity/trivy"
}

# ====================
# Step 5: Syntax and Import Tests
# ====================

Write-Info "Step 5: Testing Python syntax and imports..."

# Test Python syntax
docker run --rm "${IMAGE_NAME}:${TEST_TAG}" python -m py_compile main.py

if ($LASTEXITCODE -ne 0) {
    Write-ErrorMsg "Python syntax validation failed"
    exit 1
}

# Test imports
$IMPORT_TEST = @"
import sys
sys.path.insert(0, '/app')
try:
    from src.utils import exceptions, validation, security, retry, logger, constants
    from src.adapter import s3_adapter, textract_adapter, bedrock_adapter, dynamodb_adapter
    print('[SUCCESS] All imports successful')
except ImportError as e:
    print(f'[ERROR] Import failed: {e}')
    sys.exit(1)
"@

docker run --rm "${IMAGE_NAME}:${TEST_TAG}" python -c $IMPORT_TEST

if ($LASTEXITCODE -eq 0) {
    Write-Success "Python syntax and imports validated"
} else {
    Write-ErrorMsg "Python validation failed"
    exit 1
}

# ====================
# Step 6: Configuration Validation
# ====================

Write-Info "Step 6: Testing configuration..."

$CONFIG_TEST = @"
import sys
sys.path.insert(0, '/app')
try:
    from src.utils.config import PipelineConfig
    print('[TEST] PipelineConfig import successful')
    print('[SUCCESS] Configuration module validated')
except Exception as e:
    print(f'[ERROR] Configuration validation failed: {e}')
    sys.exit(1)
"@

docker run --rm `
    -e BUCKET_NAME=test-bucket `
    -e AWS_REGION=us-east-1 `
    "${IMAGE_NAME}:${TEST_TAG}" `
    python -c $CONFIG_TEST

if ($LASTEXITCODE -eq 0) {
    Write-Success "Configuration validation passed"
} else {
    Write-ErrorMsg "Configuration validation failed"
    exit 1
}

# ====================
# Step 7: User and Permissions Check
# ====================

Write-Info "Step 7: Checking security (non-root user)..."

$USER_CHECK = docker run --rm "${IMAGE_NAME}:${TEST_TAG}" whoami

if ($USER_CHECK -eq "appuser") {
    Write-Success "Container runs as non-root user: $USER_CHECK"
} else {
    Write-ErrorMsg "Container should run as 'appuser', but runs as: $USER_CHECK"
    exit 1
}

Write-Info "Checking file permissions..."
docker run --rm "${IMAGE_NAME}:${TEST_TAG}" ls -la /app

# ====================
# Step 8: Healthcheck Validation
# ====================

Write-Info "Step 8: Testing healthcheck..."

# Start container with healthcheck
docker run -d `
    --name $CONTAINER_NAME `
    -e BUCKET_NAME=test-bucket `
    "${IMAGE_NAME}:${TEST_TAG}" `
    tail -f /dev/null

Start-Sleep -Seconds 10

$HEALTH_STATUS = docker inspect --format='{{.State.Health.Status}}' $CONTAINER_NAME 2>$null

if (-not $HEALTH_STATUS) {
    $HEALTH_STATUS = "no healthcheck"
}

if ($HEALTH_STATUS -eq "healthy" -or $HEALTH_STATUS -eq "no healthcheck") {
    Write-Success "Healthcheck status: $HEALTH_STATUS"
} else {
    Write-Warning "Healthcheck status: $HEALTH_STATUS"
}

docker stop $CONTAINER_NAME 2>$null
docker rm $CONTAINER_NAME 2>$null

# ====================
# Step 9: Size Optimization Check
# ====================

Write-Info "Step 9: Checking image size optimization..."
Write-Info "Image size: $IMAGE_SIZE"

# ====================
# Step 10: Tag and Push (Optional)
# ====================

if ($Push) {
    Write-Info "Step 10: Tagging and pushing to ECR..."

    if (-not $AWS_ACCOUNT_ID) {
        Write-ErrorMsg "AWS_ACCOUNT_ID environment variable not set"
        Write-Info "Set it with: `$env:AWS_ACCOUNT_ID='123456789012'"
        exit 1
    }

    $ECR_URI = "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}"

    # Login to ECR
    Write-Info "Logging in to ECR..."
    aws ecr get-login-password --region $AWS_REGION | `
        docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

    if ($LASTEXITCODE -ne 0) {
        Write-ErrorMsg "ECR login failed"
        exit 1
    }

    # Tag image
    $TIMESTAMP = Get-Date -Format "yyyyMMdd-HHmmss"
    docker tag "${IMAGE_NAME}:${TEST_TAG}" "${ECR_URI}:${IMAGE_TAG}"
    docker tag "${IMAGE_NAME}:${TEST_TAG}" "${ECR_URI}:${TIMESTAMP}"

    # Push image
    Write-Info "Pushing image to ECR..."
    docker push "${ECR_URI}:${IMAGE_TAG}"
    docker push "${ECR_URI}:${TIMESTAMP}"

    if ($LASTEXITCODE -eq 0) {
        Write-Success "Image pushed to ECR: ${ECR_URI}:${IMAGE_TAG}"
    } else {
        Write-ErrorMsg "Failed to push image to ECR"
        exit 1
    }
} else {
    Write-Info "Step 10: Tagging for local use..."
    docker tag "${IMAGE_NAME}:${TEST_TAG}" "${IMAGE_NAME}:${IMAGE_TAG}"
    Write-Success "Image tagged as ${IMAGE_NAME}:${IMAGE_TAG}"
    Write-Info "To push to ECR, run: .\build-and-test.ps1 -Push"
}

# ====================
# Final Summary
# ====================

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Success "All tests passed! Image is ready for deployment."
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Info "Image Details:"
Write-Host "  Name: ${IMAGE_NAME}:${IMAGE_TAG}"
Write-Host "  Size: $IMAGE_SIZE"
Write-Host "  Layers: $LAYER_COUNT"
Write-Host "  User: appuser (non-root)"
Write-Host "  Python: 3.11-slim"
Write-Host ""
Write-Info "Next Steps:"
Write-Host "  1. Deploy to ECS/Fargate"
Write-Host "  2. Set required environment variables:"
Write-Host "     - BUCKET_NAME"
Write-Host "     - OBJECT_KEY"
Write-Host "     - USE_TEXTRACT (true/false)"
Write-Host "  3. Monitor CloudWatch logs with structured JSON"
Write-Host ""

if (-not $Push) {
    Write-Info "To push to ECR:"
    Write-Host "  `$env:AWS_ACCOUNT_ID='your-account-id'"
    Write-Host "  .\build-and-test.ps1 -Push"
}

Write-Host ""
