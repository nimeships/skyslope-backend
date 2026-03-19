"""
Unit tests for CircuitBreaker pattern
"""

import pytest
import time
from src.utils.circuit_breaker import CircuitBreaker, CircuitState
from src.utils.exceptions import CircuitOpenError


@pytest.mark.unit
class TestCircuitBreaker:
    """Test circuit breaker functionality"""

    def test_circuit_starts_closed(self):
        """Test circuit breaker starts in CLOSED state"""
        breaker = CircuitBreaker(failure_threshold=3, timeout=1, name="test")
        assert breaker.get_state() == CircuitState.CLOSED

    def test_successful_calls_keep_circuit_closed(self):
        """Test successful calls don't open circuit"""
        breaker = CircuitBreaker(failure_threshold=3, timeout=1, name="test")

        # Make 10 successful calls
        for _ in range(10):
            result = breaker.call(lambda: "success")
            assert result == "success"

        assert breaker.get_state() == CircuitState.CLOSED

    def test_circuit_opens_after_threshold_failures(self):
        """Test circuit opens after failure threshold reached"""
        breaker = CircuitBreaker(failure_threshold=3, timeout=1, name="test")

        def failing_function():
            raise Exception("Test failure")

        # First 2 failures - circuit stays closed
        for _ in range(2):
            with pytest.raises(Exception):
                breaker.call(failing_function)
            assert breaker.get_state() == CircuitState.CLOSED

        # 3rd failure - circuit opens
        with pytest.raises(Exception):
            breaker.call(failing_function)
        assert breaker.get_state() == CircuitState.OPEN

    def test_circuit_open_rejects_calls(self):
        """Test OPEN circuit rejects calls with CircuitOpenError"""
        breaker = CircuitBreaker(failure_threshold=2, timeout=60, name="test")

        # Force circuit to open
        for _ in range(2):
            with pytest.raises(Exception):
                breaker.call(lambda: 1 / 0)

        assert breaker.get_state() == CircuitState.OPEN

        # Next call should be rejected immediately
        with pytest.raises(CircuitOpenError) as exc:
            breaker.call(lambda: "success")

        assert "Circuit breaker" in str(exc.value)
        assert "OPEN" in str(exc.value)

    def test_circuit_transitions_to_half_open_after_timeout(self):
        """Test circuit transitions from OPEN to HALF_OPEN after timeout"""
        breaker = CircuitBreaker(failure_threshold=2, timeout=1, name="test")

        # Open the circuit
        for _ in range(2):
            with pytest.raises(Exception):
                breaker.call(lambda: 1 / 0)

        assert breaker.get_state() == CircuitState.OPEN

        # Wait for timeout
        time.sleep(1.1)

        # Next call should transition to HALF_OPEN
        result = breaker.call(lambda: "success")
        assert result == "success"
        # After successful call in HALF_OPEN, may go to CLOSED
        # (depends on success_threshold)

    def test_half_open_closes_after_success_threshold(self):
        """Test HALF_OPEN transitions to CLOSED after success threshold"""
        breaker = CircuitBreaker(
            failure_threshold=2,
            success_threshold=2,
            timeout=1,
            name="test"
        )

        # Open circuit
        for _ in range(2):
            with pytest.raises(Exception):
                breaker.call(lambda: 1 / 0)

        assert breaker.get_state() == CircuitState.OPEN

        # Wait for timeout
        time.sleep(1.1)

        # First successful call -> HALF_OPEN
        breaker.call(lambda: "success")
        # Circuit might still be HALF_OPEN (need 2 successes)

        # Second successful call -> CLOSED
        breaker.call(lambda: "success")
        assert breaker.get_state() == CircuitState.CLOSED

    def test_half_open_reopens_on_failure(self):
        """Test HALF_OPEN reopens on failure"""
        breaker = CircuitBreaker(failure_threshold=2, timeout=1, name="test")

        # Open circuit
        for _ in range(2):
            with pytest.raises(Exception):
                breaker.call(lambda: 1 / 0)

        time.sleep(1.1)

        # Fail in HALF_OPEN -> back to OPEN
        with pytest.raises(Exception):
            breaker.call(lambda: 1 / 0)

        assert breaker.get_state() == CircuitState.OPEN

    def test_circuit_breaker_with_args_and_kwargs(self):
        """Test circuit breaker works with function arguments"""
        breaker = CircuitBreaker(failure_threshold=3, timeout=1, name="test")

        def add(a, b):
            return a + b

        result = breaker.call(add, 2, 3)
        assert result == 5

        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}!"

        result = breaker.call(greet, "Alice", greeting="Hi")
        assert result == "Hi, Alice!"

    def test_circuit_breaker_reset(self):
        """Test manual circuit breaker reset"""
        breaker = CircuitBreaker(failure_threshold=2, timeout=60, name="test")

        # Open circuit
        for _ in range(2):
            with pytest.raises(Exception):
                breaker.call(lambda: 1 / 0)

        assert breaker.get_state() == CircuitState.OPEN

        # Manual reset
        breaker.reset()
        assert breaker.get_state() == CircuitState.CLOSED

        # Should work again
        result = breaker.call(lambda: "success")
        assert result == "success"

    def test_get_metrics(self):
        """Test circuit breaker metrics"""
        breaker = CircuitBreaker(failure_threshold=3, timeout=1, name="test")

        metrics = breaker.get_metrics()
        assert metrics['name'] == 'test'
        assert metrics['state'] == 'CLOSED'
        assert metrics['failure_count'] == 0
        assert metrics['failure_threshold'] == 3

        # Cause failure
        with pytest.raises(Exception):
            breaker.call(lambda: 1 / 0)

        metrics = breaker.get_metrics()
        assert metrics['failure_count'] == 1
