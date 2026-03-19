"""
Result Caching for Document Extraction
Caches extraction results by file hash to avoid reprocessing
"""

import json
import hashlib
from typing import Optional, Dict, Any
from src.adapter.s3_adapter import S3Adapter
from src.utils.config import PipelineConfig
from src.utils.logger import get_logger
from src.utils.exceptions import S3OperationError

logger = get_logger(__name__)


class ResultCache:
    """
    Caches document extraction results in S3.

    Benefits:
    1. Avoid reprocessing identical files (by SHA256 hash)
    2. Faster pipeline execution for duplicate uploads
    3. Cost savings (no redundant Bedrock calls)

    Cache structure:
        s3://bucket/cache/{file_hash}.json

    Example:
        cache = ResultCache(config)

        # Check cache
        cached_result = cache.get(file_hash)
        if cached_result:
            return cached_result  # Cache hit!

        # Process file
        result = extract_data(file)

        # Store in cache
        cache.set(file_hash, result)
    """

    def __init__(
        self,
        config: PipelineConfig,
        cache_prefix: str = "cache/extractions/",
        enable_cache: bool = True
    ):
        """
        Initialize result cache.

        Args:
            config: Pipeline configuration
            cache_prefix: S3 prefix for cache storage
            enable_cache: Whether caching is enabled (default: True)
        """
        self.s3 = S3Adapter(config)
        self.cache_prefix = cache_prefix
        self.enable_cache = enable_cache

        # Cache statistics
        self.hits = 0
        self.misses = 0
        self.stores = 0

        if not enable_cache:
            logger.warning("Result cache is DISABLED")
        else:
            logger.info(f"Result cache initialized: {cache_prefix}")

    def calculate_hash(self, file_bytes: bytes) -> str:
        """
        Calculate SHA256 hash of file content.

        Args:
            file_bytes: Raw file content

        Returns:
            Hex-encoded SHA256 hash
        """
        sha = hashlib.sha256()
        sha.update(file_bytes)
        return sha.hexdigest()

    def get(self, file_hash: str) -> Optional[Dict[str, Any]]:
        """
        Get cached extraction result by file hash.

        Args:
            file_hash: SHA256 hash of file content

        Returns:
            Cached extraction result (dict) or None if not found
        """
        if not self.enable_cache:
            return None

        cache_key = f"{self.cache_prefix}{file_hash}.json"

        try:
            # Use download_if_exists to avoid error logs on cache miss
            cached_bytes = self.s3.download_file_if_exists(cache_key)

            if cached_bytes is None:
                # Cache miss (file doesn't exist) - log for visibility
                self.misses += 1
                logger.info(
                    f"Cache MISS: {file_hash[:16]}... (processing file)",
                    extra={
                        'file_hash': file_hash,
                        'cache_key': cache_key,
                        'cache_stats': self.get_stats(),
                        'cache_status': 'MISS'
                    }
                )
                return None

            # Cache hit - parse JSON
            cached_result = json.loads(cached_bytes.decode('utf-8'))

            self.hits += 1
            logger.info(
                f"Cache HIT: {file_hash[:16]}... (using cached result)",
                extra={
                    'file_hash': file_hash,
                    'cache_key': cache_key,
                    'cache_stats': self.get_stats(),
                    'cache_status': 'HIT'
                }
            )

            return cached_result

        except json.JSONDecodeError as e:
            # Corrupted cache entry
            logger.warning(
                f"Cache corrupted for {file_hash[:16]}...: {e}",
                extra={'file_hash': file_hash, 'error': str(e)}
            )
            self.misses += 1
            return None

        except Exception as e:
            # Unexpected error - don't fail pipeline, just miss cache
            logger.error(
                f"Cache error for {file_hash[:16]}...: {e}",
                extra={'file_hash': file_hash, 'error': str(e)},
                exc_info=True
            )
            self.misses += 1
            return None

    def set(self, file_hash: str, result: Dict[str, Any]):
        """
        Store extraction result in cache.

        Args:
            file_hash: SHA256 hash of file content
            result: Extraction result to cache
        """
        if not self.enable_cache:
            return

        cache_key = f"{self.cache_prefix}{file_hash}.json"

        try:
            result_json = json.dumps(result, indent=2)
            self.s3.upload_file(result_json.encode('utf-8'), cache_key)

            self.stores += 1
            logger.info(
                f"Cache STORE: {file_hash[:16]}...",
                extra={
                    'file_hash': file_hash,
                    'cache_key': cache_key,
                    'size_bytes': len(result_json),
                    'cache_stats': self.get_stats()
                }
            )

        except Exception as e:
            # Cache store failure - log but don't fail pipeline
            logger.error(
                f"Failed to cache result for {file_hash[:16]}...: {e}",
                extra={'file_hash': file_hash, 'error': str(e)},
                exc_info=True
            )

    def get_with_file_bytes(self, file_bytes: bytes) -> Optional[Dict[str, Any]]:
        """
        Convenience method: calculate hash and get from cache.

        Args:
            file_bytes: Raw file content

        Returns:
            Cached result or None
        """
        file_hash = self.calculate_hash(file_bytes)
        return self.get(file_hash)

    def set_with_file_bytes(self, file_bytes: bytes, result: Dict[str, Any]):
        """
        Convenience method: calculate hash and store in cache.

        Args:
            file_bytes: Raw file content
            result: Extraction result to cache
        """
        file_hash = self.calculate_hash(file_bytes)
        self.set(file_hash, result)

    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache metrics
        """
        total_requests = self.hits + self.misses
        hit_rate = (self.hits / total_requests * 100) if total_requests > 0 else 0

        return {
            'enabled': self.enable_cache,
            'hits': self.hits,
            'misses': self.misses,
            'stores': self.stores,
            'total_requests': total_requests,
            'hit_rate_percent': round(hit_rate, 2)
        }

    def clear_stats(self):
        """Reset cache statistics (for testing)."""
        self.hits = 0
        self.misses = 0
        self.stores = 0

    def invalidate(self, file_hash: str):
        """
        Invalidate (delete) a cached result.

        Args:
            file_hash: SHA256 hash of file to invalidate
        """
        if not self.enable_cache:
            return

        cache_key = f"{self.cache_prefix}{file_hash}.json"

        try:
            # Delete from S3
            self.s3.delete_file(cache_key)

            logger.info(
                f"Cache invalidated: {file_hash[:16]}...",
                extra={
                    'file_hash': file_hash,
                    'cache_key': cache_key,
                    'cache_operation': 'invalidate'
                }
            )
        except S3OperationError as e:
            # If file doesn't exist, that's fine - already invalidated
            if 'NoSuchKey' in str(e) or 'not found' in str(e).lower():
                logger.info(
                    f"Cache already invalidated (not found): {file_hash[:16]}...",
                    extra={'file_hash': file_hash, 'cache_key': cache_key}
                )
            else:
                logger.error(
                    f"Failed to invalidate cache for {file_hash[:16]}...: {e}",
                    extra={'file_hash': file_hash, 'error': str(e)},
                    exc_info=True
                )
        except Exception as e:
            logger.error(
                f"Unexpected error invalidating cache for {file_hash[:16]}...: {e}",
                extra={'file_hash': file_hash, 'error': str(e)},
                exc_info=True
            )


# Global result cache instance
_result_cache: Optional[ResultCache] = None


def get_result_cache(
    config: PipelineConfig,
    enable_cache: bool = True
) -> ResultCache:
    """
    Get or create the global result cache instance.

    Args:
        config: Pipeline configuration
        enable_cache: Whether caching is enabled

    Returns:
        ResultCache instance
    """
    global _result_cache
    if _result_cache is None:
        _result_cache = ResultCache(config, enable_cache=enable_cache)
    return _result_cache


def reset_result_cache():
    """Reset global result cache (for testing)."""
    global _result_cache
    _result_cache = None
