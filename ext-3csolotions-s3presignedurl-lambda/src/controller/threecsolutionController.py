# This code base is for controlling and centralizing the 3C solutions related API endpoints.

import logging
from typing import Optional
from starlette import status
from fastapi import APIRouter, HTTPException, Depends

from src.core.threecsolutioncore import ThreeCSolutionCore
from src.model.presignurlInputValidator import PresignRequest
from src.model.presignedUrlResponseValidator import PresignResponse
from src.model.downloadInputValidator import DownloadRequest
from src.model.downloadResponseValidator import DownloadResponse
from src.model.monitorInputValidator import MonitorRequest
from src.model.monitorResponseValidator import MonitorResponse
from src.model.recentRequestsResponseValidator import RecentRequestsResponse
from src.env.environmentVariables import EnvironmentVariables
from src.adapter.s3service import S3Service
from src.adapter.dynamodbservice import DynamoDBService
from src.utils.s3helper import S3Helper
from src.exceptions.custom_exceptions import ThreeCSolutionError
from src.middleware.auth import verify_api_key

router = APIRouter()


def _safe_detail(e: ThreeCSolutionError) -> str:
    """Return a client-safe error message.

    4xx errors (client mistakes) surface the exception's own message, which
    is already written to be user-friendly (e.g. 'Resource not found').
    5xx errors (server/infrastructure faults) always return a generic phrase
    so that internal paths, AWS error codes, and stack details are never
    leaked to the caller.
    """
    if e.status_code == 503:
        return "The service is temporarily unavailable. Please try again later."
    if e.status_code >= 500:
        return "An unexpected error occurred. Please try again or contact support."
    return e.message

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ThreeCSolutionController:
    """
    Controller for 3C Solutions API endpoints.
    Handles HTTP request/response and delegates business logic to Core.
    """
    def __init__(self):
        self.logger = logger
        self.logger.info("ThreeCSolutionController initialized")
        self.env = EnvironmentVariables(logger=self.logger)
        self.s3helper = S3Helper(logger=self.logger, env=self.env)
        self.s3service = S3Service(logger=self.logger, env=self.env)
        self.dynamodbservice = DynamoDBService(logger=self.logger, env=self.env)
        self.core = ThreeCSolutionCore(
            logger=self.logger,
            env=self.env,
            s3helper=self.s3helper,
            s3service=self.s3service,
            dynamodbservice=self.dynamodbservice
        )

    async def health_check(self) -> dict:
        """Health check endpoint - returns service status"""
        return await self.core.health_check()

    def generate_presigned_url(self, request: PresignRequest) -> PresignResponse:
        """Generate presigned URL for S3 upload"""
        self.logger.info("Generating presigned URL")
        details = self.core.generate_presigned_url(request)
        return PresignResponse(
            bucket=self.env.s3_bucket,
            key=details["key"],
            upload_url=details["url"],
            expires_in=self.env.presigned_url_expiration,
        )

    def generate_download_url(self, request: DownloadRequest) -> DownloadResponse:
        """Generate presigned URL for S3 download"""
        self.logger.info("Generating download URL")
        details = self.core.generate_download_url(request)
        return DownloadResponse(
            bucket=self.env.s3_bucket,
            key=details["key"],
            download_url=details["url"],
            expires_in=self.env.presigned_url_expiration,
            testing=request.testing
        )

    def get_pipeline_status(self, request: MonitorRequest) -> MonitorResponse:
        """Get pipeline processing status from DynamoDB"""
        self.logger.info("Fetching pipeline status")
        status_data = self.core.get_pipeline_status(request)
        return MonitorResponse(
            request_id=status_data["request_id"],
            stage=status_data["stage"],
            status=status_data["status"],
            progress=status_data["progress"],
            total=status_data["total"],
            message=status_data["message"],
            last_updated=status_data["last_updated"],
            percentage=status_data.get("percentage", 0)
        )

    def get_recent_requests(self) -> RecentRequestsResponse:
        """Get top 10 most recent request IDs from DynamoDB"""
        self.logger.info("Fetching recent requests")
        recent_data = self.core.get_recent_requests(limit=10)
        return RecentRequestsResponse(
            requests=recent_data["requests"],
            count=recent_data["count"]
        )


# Lazy initialization: controller is created on first request, not at import
# This avoids creating AWS clients during cold start if not needed
controller: Optional[ThreeCSolutionController] = None


def get_controller() -> ThreeCSolutionController:
    """
    Get or create controller instance (lazy initialization).

    This ensures AWS service clients are only created when actually needed,
    reducing Lambda cold start time by ~1-3 seconds.
    """
    global controller
    if controller is None:
        logger.info("Initializing controller (first request after cold start)")
        controller = ThreeCSolutionController()
    return controller

@router.get("/test", status_code=status.HTTP_200_OK)
async def test_check():
    """test check endpoint"""
    try:
        return {"message": "Test endpoint is working", "status": "healthy"}
    except ThreeCSolutionError as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=e.status_code, detail=_safe_detail(e))
    except Exception as e:
        logger.error(f"Unexpected error in health check: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    """Health check endpoint"""
    try:
        ctrl = get_controller()
        return await ctrl.health_check()
    except ThreeCSolutionError as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=e.status_code, detail=_safe_detail(e))
    except Exception as e:
        logger.error(f"Unexpected error in health check: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/presign", response_model=PresignResponse, status_code=status.HTTP_200_OK)
def generate_presigned_url(
    request: PresignRequest,
    api_key: Optional[str] = Depends(verify_api_key)
):
    """
    Generate presigned URL for uploading files to S3.
    Requires API key authentication if API_KEY environment variable is set.
    """
    try:
        ctrl = get_controller()
        return ctrl.generate_presigned_url(request)
    except ThreeCSolutionError as e:
        logger.error(f"Failed to generate presigned URL: {e}")
        raise HTTPException(status_code=e.status_code, detail=_safe_detail(e))
    except Exception as e:
        logger.error(f"Unexpected error generating presigned URL: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate presigned URL"
        )


@router.post("/download", response_model=DownloadResponse, status_code=status.HTTP_200_OK)
def generate_download_url(
    request: DownloadRequest,
    api_key: Optional[str] = Depends(verify_api_key)
):
    """
    Generate presigned URL for downloading processed files from S3.
    Requires API key authentication if API_KEY environment variable is set.
    """
    try:
        ctrl = get_controller()
        return ctrl.generate_download_url(request)
    except ThreeCSolutionError as e:
        logger.error(f"Failed to generate download URL: {e}")
        raise HTTPException(status_code=e.status_code, detail=_safe_detail(e))
    except Exception as e:
        logger.error(f"Unexpected error generating download URL: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate download URL"
        )


@router.post("/monitor", response_model=MonitorResponse, status_code=status.HTTP_200_OK)
def get_pipeline_status(
    request: MonitorRequest,
    api_key: Optional[str] = Depends(verify_api_key)
):
    """
    Get pipeline processing status.
    Requires API key authentication if API_KEY environment variable is set.
    """
    try:
        ctrl = get_controller()
        return ctrl.get_pipeline_status(request)
    except ThreeCSolutionError as e:
        logger.error(f"Failed to get pipeline status: {e}")
        raise HTTPException(status_code=e.status_code, detail=_safe_detail(e))
    except Exception as e:
        logger.error(f"Unexpected error getting pipeline status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get pipeline status"
        )


@router.get("/recent-requests", response_model=RecentRequestsResponse, status_code=status.HTTP_200_OK)
def get_recent_requests(
    api_key: Optional[str] = Depends(verify_api_key)
):
    """
    Get top 10 most recent request IDs from DynamoDB, ordered by last_updated (newest first).
    Requires API key authentication if API_KEY environment variable is set.
    """
    try:
        ctrl = get_controller()
        return ctrl.get_recent_requests()
    except ThreeCSolutionError as e:
        logger.error(f"Failed to get recent requests: {e}")
        raise HTTPException(status_code=e.status_code, detail=_safe_detail(e))
    except Exception as e:
        logger.error(f"Unexpected error getting recent requests: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get recent requests"
        )
