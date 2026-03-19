"""
Unit tests for authentication middleware.

Tests cover:
- API key caching at module level
- Constant-time comparison (timing attack protection)
- Missing credentials handling
- Invalid API key handling
"""
import pytest
import os
from unittest.mock import patch, Mock
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials


class TestAPIKeyAuthentication:
    """Test suite for API key authentication"""

    @patch.dict(os.environ, {}, clear=True)
    @pytest.mark.asyncio
    async def test_no_api_key_configured_allows_access(self):
        """When API_KEY env var not set, authentication is disabled"""
        # Reload module to reset cached API key
        import importlib
        import src.middleware.auth as auth_module
        importlib.reload(auth_module)

        # Should return None when no API key configured
        result = await auth_module.verify_api_key(None)
        assert result is None

    @patch.dict(os.environ, {'API_KEY': 'test-secret-key-123'}, clear=True)
    @pytest.mark.asyncio
    async def test_missing_credentials_raises_401(self):
        """When API key required but credentials missing, raise 401"""
        import importlib
        import src.middleware.auth as auth_module
        importlib.reload(auth_module)

        with pytest.raises(HTTPException) as exc_info:
            await auth_module.verify_api_key(None)

        assert exc_info.value.status_code == 401
        assert "Missing authentication credentials" in exc_info.value.detail

    @patch.dict(os.environ, {'API_KEY': 'test-secret-key-123'}, clear=True)
    @pytest.mark.asyncio
    async def test_invalid_api_key_raises_401(self):
        """When API key is incorrect, raise 401"""
        import importlib
        import src.middleware.auth as auth_module
        importlib.reload(auth_module)

        # Create mock credentials with wrong key
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "wrong-key"

        with pytest.raises(HTTPException) as exc_info:
            await auth_module.verify_api_key(mock_credentials)

        assert exc_info.value.status_code == 401
        assert "Invalid API key" in exc_info.value.detail

    @patch.dict(os.environ, {'API_KEY': 'test-secret-key-123'}, clear=True)
    @pytest.mark.asyncio
    async def test_valid_api_key_returns_key(self):
        """When API key is correct, return the key"""
        import importlib
        import src.middleware.auth as auth_module
        importlib.reload(auth_module)

        # Create mock credentials with correct key
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "test-secret-key-123"

        result = await auth_module.verify_api_key(mock_credentials)
        assert result == "test-secret-key-123"

    @patch.dict(os.environ, {'API_KEY': 'test-secret-key-123'}, clear=True)
    def test_api_key_cached_at_module_level(self):
        """API key should be read once at module load, not on every call"""
        import importlib
        import src.middleware.auth as auth_module
        importlib.reload(auth_module)

        # Cached key should exist
        assert auth_module._CACHED_API_KEY == "test-secret-key-123"

        # Changing env var should NOT affect cached value
        with patch.dict(os.environ, {'API_KEY': 'different-key'}):
            # Cached value should remain unchanged
            assert auth_module._CACHED_API_KEY == "test-secret-key-123"

    @patch.dict(os.environ, {'API_KEY': 'test-secret-key-123'}, clear=True)
    @pytest.mark.asyncio
    async def test_timing_attack_protection(self):
        """Verify hmac.compare_digest is used (constant-time comparison)"""
        import importlib
        import src.middleware.auth as auth_module
        import hmac
        from unittest.mock import patch

        importlib.reload(auth_module)

        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "wrong-key"

        # Mock hmac.compare_digest to verify it's called
        with patch('src.middleware.auth.hmac.compare_digest', return_value=False) as mock_compare:
            with pytest.raises(HTTPException):
                await auth_module.verify_api_key(mock_credentials)

            # Verify constant-time comparison was used
            mock_compare.assert_called_once_with("wrong-key", "test-secret-key-123")
