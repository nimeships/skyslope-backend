"""
Circuit Breaker Pattern Implementation
Prevents cascading failures by stopping requests to failing services
"""

import time
import threading
from enum import Enum
from typing import Callable, TypeVar, Optional
from .exceptions import CircuitOpenError
from .logger import get_logger

logger = get_logger(__name__)

T = TypeVar('T')


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "CLOSED"          # Normal operation, requests allowed
    OPEN = "OPEN"              # Failing, requests blocked
    HALF_OPEN = "HALF_OPEN"    # Testing if service recovered


class CircuitBreaker:
    """
    Circuit breaker pattern for AWS service resilience.

    Protects against cascading failures by detecting repeated errors
    and temporarily blocking requests to failing services.

    States:
    - CLOSED: Normal operation, all requests allowed
    - OPEN: Service failing, requests blocked (fail fast)
    - HALF_OPEN: Testing recovery, limited requests allowed

    Example:
        breaker = CircuitBreaker(failure_threshold=5, timeout=60, name="Bedrock")

        try:
            result = breaker.call(bedrock_client.invoke_model, payload)
        except CircuitOpenError:
            logger.error("Circuit breaker is OPEN, service unavailable")
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout: int = 60,
        name: str = "default"
    ):
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Number of failures before opening circuit
            success_threshold: Number of successes to close circuit from HALF_OPEN
            timeout: Seconds to wait before transitioning from OPEN to HALF_OPEN
            name: Circuit breaker name (for logging)
        """
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout = timeout
        self.name = name

        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None
        self.state = CircuitState.CLOSED
        self._lock = threading.Lock()

    def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """
        Execute function with circuit breaker protection.

        Args:
            func: Function to execute
            *args, **kwargs: Function arguments

        Returns:
            Function result

        Raises:
            CircuitOpenError: If circuit is OPEN (service unavailable)
            Exception: Any exception raised by the function
        """
        # Check if we should try the request
        with self._lock:
            current_state = self.state

            if current_state == CircuitState.OPEN:
                # Check if timeout has elapsed
                if self.last_failure_time and time.time() - self.last_failure_time >= self.timeout:
                    logger.info(
                        f"Circuit '{self.name}' transitioning: OPEN → HALF_OPEN",
                        extra={'circuit_name': self.name, 'transition': 'OPEN->HALF_OPEN'}
                    )
                    self.state = CircuitState.HALF_OPEN
                    self.success_count = 0
                else:
                    time_remaining = self.timeout - (time.time() - self.last_failure_time)
                    raise CircuitOpenError(
                        f"Circuit breaker '{self.name}' is OPEN. "
                        f"Service unavailable. Retry in {time_remaining:.0f}s"
                    )

        # Attempt the request
        try:
            result = func(*args, **kwargs)

            # Handle success
            with self._lock:
                if self.state == CircuitState.HALF_OPEN:
                    self.success_count += 1
                    logger.info(
                        f"Circuit '{self.name}' recovery progress: "
                        f"{self.success_count}/{self.success_threshold} successes",
                        extra={
                            'circuit_name': self.name,
                            'success_count': self.success_count,
                            'success_threshold': self.success_threshold
                        }
                    )

                    if self.success_count >= self.success_threshold:
                        logger.info(
                            f"Circuit '{self.name}' transitioning: HALF_OPEN → CLOSED",
                            extra={'circuit_name': self.name, 'transition': 'HALF_OPEN->CLOSED'}
                        )
                        self.state = CircuitState.CLOSED
                        self.failure_count = 0
                        self.success_count = 0

                elif self.state == CircuitState.CLOSED:
                    # Reset failure count on successful request
                    self.failure_count = 0

            return result

        except Exception as e:
            # Handle failure
            with self._lock:
                self.failure_count += 1
                self.last_failure_time = time.time()

                error_type = type(e).__name__

                logger.warning(
                    f"Circuit '{self.name}' failure detected: "
                    f"{self.failure_count}/{self.failure_threshold} failures",
                    extra={
                        'circuit_name': self.name,
                        'failure_count': self.failure_count,
                        'failure_threshold': self.failure_threshold,
                        'error_type': error_type,
                        'state': self.state.value
                    }
                )

                if self.failure_count >= self.failure_threshold:
                    if self.state != CircuitState.OPEN:
                        logger.error(
                            f"⚠️  Circuit '{self.name}' transitioning: {self.state.value} → OPEN",
                            extra={
                                'circuit_name': self.name,
                                'transition': f'{self.state.value}->OPEN',
                                'failure_count': self.failure_count
                            }
                        )
                        self.state = CircuitState.OPEN
                        self.success_count = 0

            # Re-raise original exception
            raise

    def reset(self):
        """
        Manually reset circuit breaker to CLOSED state.
        Use with caution - typically for testing or manual intervention.
        """
        with self._lock:
            logger.info(
                f"Circuit '{self.name}' manually reset to CLOSED",
                extra={'circuit_name': self.name}
            )
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.success_count = 0
            self.last_failure_time = None

    def get_state(self) -> CircuitState:
        """Get current circuit state (thread-safe)."""
        with self._lock:
            return self.state

    def get_metrics(self) -> dict:
        """Get circuit breaker metrics (for monitoring)."""
        with self._lock:
            return {
                'name': self.name,
                'state': self.state.value,
                'failure_count': self.failure_count,
                'success_count': self.success_count,
                'failure_threshold': self.failure_threshold,
                'success_threshold': self.success_threshold,
                'last_failure_time': self.last_failure_time
            }
