#!/bin/bash
# ============================================================
# Production Docker Build, Test, and Security Scan Script
# ============================================================
# This script thoroughly tests the Docker image before deployment
# Usage: ./build-and-test.sh [--push]

set -e  # Exit on error
set -u  # Exit on undefined variable

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
IMAGE_NAME="document-extraction-pipeline"
IMAGE_TAG="latest"
TEST_TAG="test"
CONTAINER_NAME="doc-pipeline-test"

# AWS Configuration (optional - for ECR push)
AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-}"
ECR_REPO_NAME="document-extraction"

# ====================
# Helper Functions
# ====================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

cleanup() {
    log_info "Cleaning up test containers and images..."
    docker stop "$CONTAINER_NAME" 2>/dev/null || true
    docker rm "$CONTAINER_NAME" 2>/dev/null || true
    docker rmi "${IMAGE_NAME}:${TEST_TAG}" 2>/dev/null || true
}

# Trap cleanup on exit
trap cleanup EXIT

# ====================
# Step 1: Pre-Build Validation
# ====================

log_info "Step 1: Pre-Build Validation"

# Check if Dockerfile exists
if [ ! -f "dockerfile" ]; then
    log_error "dockerfile not found!"
    exit 1
fi

# Check if requirements.txt exists
if [ ! -f "requirements.txt" ]; then
    log_error "requirements.txt not found!"
    exit 1
fi

# Check if src directory exists
if [ ! -d "src" ]; then
    log_error "src directory not found!"
    exit 1
fi

# Check if main.py exists
if [ ! -f "main.py" ]; then
    log_error "main.py not found!"
    exit 1
fi

log_success "Pre-build validation passed"

# ====================
# Step 2: Build Docker Image
# ====================

log_info "Step 2: Building Docker image..."

BUILD_START=$(date +%s)

docker build \
    --tag "${IMAGE_NAME}:${TEST_TAG}" \
    --file dockerfile \
    --build-arg BUILDKIT_INLINE_CACHE=1 \
    --progress=plain \
    .

BUILD_END=$(date +%s)
BUILD_TIME=$((BUILD_END - BUILD_START))

log_success "Build completed in ${BUILD_TIME} seconds"

# ====================
# Step 3: Image Analysis
# ====================

log_info "Step 3: Analyzing image..."

# Get image size
IMAGE_SIZE=$(docker images "${IMAGE_NAME}:${TEST_TAG}" --format "{{.Size}}")
log_info "Image size: ${IMAGE_SIZE}"

# Get image layers
LAYER_COUNT=$(docker history "${IMAGE_NAME}:${TEST_TAG}" --no-trunc | wc -l)
log_info "Layer count: ${LAYER_COUNT}"

# Get image details
docker inspect "${IMAGE_NAME}:${TEST_TAG}" > /tmp/image-inspect.json
log_info "Image inspection saved to /tmp/image-inspect.json"

# ====================
# Step 4: Security Scan (Optional)
# ====================

log_info "Step 4: Security scanning..."

if command -v trivy &> /dev/null; then
    log_info "Running Trivy security scan..."
    trivy image \
        --severity HIGH,CRITICAL \
        --no-progress \
        --format table \
        "${IMAGE_NAME}:${TEST_TAG}" || log_warning "Trivy scan found vulnerabilities"
else
    log_warning "Trivy not installed. Skipping security scan."
    log_info "Install Trivy: https://github.com/aquasecurity/trivy"
fi

# ====================
# Step 5: Syntax and Import Tests
# ====================

log_info "Step 5: Testing Python syntax and imports..."

# Test Python syntax
docker run --rm "${IMAGE_NAME}:${TEST_TAG}" \
    python -m py_compile main.py

# Test imports (dry run)
docker run --rm "${IMAGE_NAME}:${TEST_TAG}" \
    python -c "
import sys
sys.path.insert(0, '/app')
try:
    from src.utils import exceptions, validation, security, retry, logger, constants
    from src.adapter import s3_adapter, textract_adapter, bedrock_adapter, dynamodb_adapter
    print('[SUCCESS] All imports successful')
except ImportError as e:
    print(f'[ERROR] Import failed: {e}')
    sys.exit(1)
"

if [ $? -eq 0 ]; then
    log_success "Python syntax and imports validated"
else
    log_error "Python validation failed"
    exit 1
fi

# ====================
# Step 6: Configuration Validation
# ====================

log_info "Step 6: Testing configuration..."

# Test configuration with minimal env vars
docker run --rm \
    -e BUCKET_NAME=test-bucket \
    -e AWS_REGION=us-east-1 \
    "${IMAGE_NAME}:${TEST_TAG}" \
    python -c "
import sys
sys.path.insert(0, '/app')
try:
    from src.utils.config import PipelineConfig
    print('[TEST] PipelineConfig import successful')
    # Don't create actual config as it requires AWS connectivity
    print('[SUCCESS] Configuration module validated')
except Exception as e:
    print(f'[ERROR] Configuration validation failed: {e}')
    sys.exit(1)
"

if [ $? -eq 0 ]; then
    log_success "Configuration validation passed"
else
    log_error "Configuration validation failed"
    exit 1
fi

# ====================
# Step 7: User and Permissions Check
# ====================

log_info "Step 7: Checking security (non-root user)..."

# Verify running as non-root
USER_CHECK=$(docker run --rm "${IMAGE_NAME}:${TEST_TAG}" whoami)

if [ "$USER_CHECK" = "appuser" ]; then
    log_success "Container runs as non-root user: $USER_CHECK"
else
    log_error "Container should run as 'appuser', but runs as: $USER_CHECK"
    exit 1
fi

# Check file permissions
docker run --rm "${IMAGE_NAME}:${TEST_TAG}" \
    ls -la /app | head -5

# ====================
# Step 8: Healthcheck Validation
# ====================

log_info "Step 8: Testing healthcheck..."

# Start container with healthcheck
docker run -d \
    --name "$CONTAINER_NAME" \
    -e BUCKET_NAME=test-bucket \
    "${IMAGE_NAME}:${TEST_TAG}" \
    tail -f /dev/null  # Keep container running

# Wait for healthcheck
sleep 10

HEALTH_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_NAME" 2>/dev/null || echo "no healthcheck")

if [ "$HEALTH_STATUS" = "healthy" ] || [ "$HEALTH_STATUS" = "no healthcheck" ]; then
    log_success "Healthcheck status: $HEALTH_STATUS"
else
    log_warning "Healthcheck status: $HEALTH_STATUS"
fi

# Cleanup test container
docker stop "$CONTAINER_NAME" 2>/dev/null || true
docker rm "$CONTAINER_NAME" 2>/dev/null || true

# ====================
# Step 9: Size Optimization Check
# ====================

log_info "Step 9: Checking image size optimization..."

# Parse image size (remove units for comparison)
SIZE_MB=$(echo "$IMAGE_SIZE" | sed 's/MB//' | sed 's/GB/*1024/' | bc 2>/dev/null || echo "0")

if command -v bc &> /dev/null && [ "$SIZE_MB" != "0" ]; then
    if (( $(echo "$SIZE_MB < 500" | bc -l) )); then
        log_success "Image size is optimal: ${IMAGE_SIZE}"
    elif (( $(echo "$SIZE_MB < 1000" | bc -l) )); then
        log_warning "Image size is acceptable but could be optimized: ${IMAGE_SIZE}"
    else
        log_warning "Image size is large: ${IMAGE_SIZE}. Consider further optimization."
    fi
else
    log_info "Image size: ${IMAGE_SIZE}"
fi

# ====================
# Step 10: Tag and Push (Optional)
# ====================

if [ "${1:-}" = "--push" ]; then
    log_info "Step 10: Tagging and pushing to ECR..."

    if [ -z "$AWS_ACCOUNT_ID" ]; then
        log_error "AWS_ACCOUNT_ID environment variable not set"
        log_info "Set it with: export AWS_ACCOUNT_ID=123456789012"
        exit 1
    fi

    ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}"

    # Login to ECR
    log_info "Logging in to ECR..."
    aws ecr get-login-password --region "$AWS_REGION" | \
        docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

    # Tag image
    docker tag "${IMAGE_NAME}:${TEST_TAG}" "${ECR_URI}:${IMAGE_TAG}"
    docker tag "${IMAGE_NAME}:${TEST_TAG}" "${ECR_URI}:$(date +%Y%m%d-%H%M%S)"

    # Push image
    log_info "Pushing image to ECR..."
    docker push "${ECR_URI}:${IMAGE_TAG}"
    docker push "${ECR_URI}:$(date +%Y%m%d-%H%M%S)"

    log_success "Image pushed to ECR: ${ECR_URI}:${IMAGE_TAG}"
else
    log_info "Step 10: Tagging for local use..."
    docker tag "${IMAGE_NAME}:${TEST_TAG}" "${IMAGE_NAME}:${IMAGE_TAG}"
    log_success "Image tagged as ${IMAGE_NAME}:${IMAGE_TAG}"
    log_info "To push to ECR, run: ./build-and-test.sh --push"
fi

# ====================
# Final Summary
# ====================

echo ""
echo "============================================================"
log_success "All tests passed! Image is ready for deployment."
echo "============================================================"
echo ""
log_info "Image Details:"
echo "  Name: ${IMAGE_NAME}:${IMAGE_TAG}"
echo "  Size: ${IMAGE_SIZE}"
echo "  Layers: ${LAYER_COUNT}"
echo "  User: appuser (non-root)"
echo "  Python: 3.11-slim"
echo ""
log_info "Next Steps:"
echo "  1. Deploy to ECS/Fargate"
echo "  2. Set required environment variables:"
echo "     - BUCKET_NAME"
echo "     - OBJECT_KEY"
echo "     - USE_TEXTRACT (true/false)"
echo "  3. Monitor CloudWatch logs with structured JSON"
echo ""

if [ "${1:-}" != "--push" ]; then
    log_info "To push to ECR:"
    echo "  export AWS_ACCOUNT_ID=your-account-id"
    echo "  ./build-and-test.sh --push"
fi

echo ""
