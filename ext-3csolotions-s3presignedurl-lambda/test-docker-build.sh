#!/bin/bash
# Test script to verify Docker build for AWS Lambda compatibility

set -e  # Exit on error

echo "=========================================="
echo "Testing Docker Build for AWS Lambda"
echo "=========================================="

IMAGE_NAME="3c-solutions-test"

echo ""
echo "Step 1: Building Docker image for linux/amd64..."
docker build --platform linux/amd64 -t $IMAGE_NAME:latest .

echo ""
echo "Step 2: Inspecting image architecture..."
docker inspect $IMAGE_NAME:latest | grep -A 5 "Architecture"

echo ""
echo "Step 3: Checking image size..."
docker images $IMAGE_NAME:latest

echo ""
echo "Step 4: Testing Lambda handler locally (optional)..."
echo "To test locally, run:"
echo "  docker run -p 9000:8080 $IMAGE_NAME:latest"
echo ""
echo "Then in another terminal:"
echo "  curl -X POST http://localhost:9000/2015-03-31/functions/function/invocations \\"
echo "    -d '{\"httpMethod\":\"GET\",\"path\":\"/api/v1/threecsolutions/health\"}'"

echo ""
echo "=========================================="
echo "✅ Docker build test completed successfully!"
echo "=========================================="
echo ""
echo "Expected output:"
echo "  - Architecture: amd64"
echo "  - Size: ~200-250MB"
echo ""
echo "If all looks good, commit and push to trigger CI/CD pipeline."
