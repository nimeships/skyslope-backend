import logging
import json
from contextvars import ContextVar
from uuid import uuid4
from datetime import datetime
from mangum import Mangum
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from src.controller.threecsolutionController import router as threec_router
from src.env.environmentVariables import EnvironmentVariables


# Correlation ID context variable for request tracing
request_id_var: ContextVar[str] = ContextVar('request_id', default='')


# Structured JSON logging formatter
class JSONFormatter(logging.Formatter):
    """Format logs as JSON for better CloudWatch Insights querying"""
    def format(self, record):
        log_obj = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'function': record.funcName,
            'line': record.lineno
        }

        # Add request_id if available
        request_id = request_id_var.get()
        if request_id:
            log_obj['request_id'] = request_id

        # Add exception info if present
        if record.exc_info:
            log_obj['exception'] = self.formatException(record.exc_info)

        return json.dumps(log_obj)


# Configure structured logging
handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.root.addHandler(handler)
logging.root.setLevel(logging.INFO)

# Initialize environment configuration
logger = logging.getLogger(__name__)
env = EnvironmentVariables(logger=logger)

# Initialize FastAPI app
app = FastAPI(
    root_path="/api/v1",
    title="3C Solutions API",
    description="API for generating presigned URLs and monitoring pipeline status"
)

# Correlation ID Middleware - Track requests across services
@app.middleware("http")
async def add_correlation_id(request: Request, call_next):
    """Add correlation ID to all requests for tracing"""
    request_id = request.headers.get('X-Request-ID', str(uuid4()))
    request_id_var.set(request_id)

    response = await call_next(request)
    response.headers['X-Request-ID'] = request_id
    return response

# CORS Configuration - Restricted to necessary methods and headers for security
app.add_middleware(
    CORSMiddleware,
    allow_origins=env.allowed_origins,  # From environment variables
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],  # Only allow necessary methods
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],  # Explicit headers only
    max_age=3600  # Cache preflight requests for 1 hour
)

app.include_router(threec_router, prefix="/threecsolutions")

@app.get("/")
async def read_root():
    return {"message": "3C Solutions API is running", "status": "healthy"}

# Lambda-compatible handler
handler = Mangum(app)

def lambda_handler(event, context):
    """
    AWS Lambda handler function.
    Processes API Gateway events through FastAPI via Mangum adapter.
    """
    # Use structured logging instead of print
    logger.info("Lambda invocation started", extra={
        'request_id': event.get('requestContext', {}).get('requestId', 'unknown'),
        'path': event.get('path', 'unknown'),
        'http_method': event.get('httpMethod', 'unknown')
    })

    # Let Mangum handle FastAPI logic
    response = handler(event, context)

    logger.info("Lambda invocation completed", extra={
        'status_code': response.get('statusCode', 0)
    })

    return response
