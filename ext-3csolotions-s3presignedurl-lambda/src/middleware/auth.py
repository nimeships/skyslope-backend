"""
Authentication middleware for API key validation.
API key authentication can be enabled via environment variable.

SECURITY FIXES APPLIED:
- Timing-attack resistant comparison (hmac.compare_digest)
- API key cached at module level (not read on every request)
"""
import os
import hmac
from fastapi import Security, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional

security = HTTPBearer(auto_error=False)

# Cache API key at module level to avoid repeated OS calls
# This improves performance and reduces attack surface
_CACHED_API_KEY = os.getenv("API_KEY")


async def verify_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security)
) -> Optional[str]:
    """
    Verify API key from Authorization header using constant-time comparison.

    SECURITY FEATURES:
    - Constant-time comparison prevents timing attacks
    - API key cached at module level (read once)
    - Backward compatible (auth optional if API_KEY not set)

    If API_KEY environment variable is set, authentication is required.
    If not set, authentication is disabled (for backward compatibility).

    Args:
        credentials: Bearer token from Authorization header

    Returns:
        str: The validated API key, or None if auth is disabled

    Raises:
        HTTPException: If API key is invalid or missing (when auth is enabled)
    """
    # Use cached API key (read once at module load)
    if not _CACHED_API_KEY:
        return None

    # API key is configured, so authentication is required
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # SECURITY: Use constant-time comparison to prevent timing attacks
    # Regular string comparison (!=) leaks information about key length/content
    # hmac.compare_digest is cryptographically secure and prevents side-channel attacks
    if not hmac.compare_digest(credentials.credentials, _CACHED_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return credentials.credentials
