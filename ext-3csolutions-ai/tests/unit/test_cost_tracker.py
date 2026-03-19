"""
Unit tests for CostTracker
"""

import pytest
from decimal import Decimal
from src.utils.cost_tracker import CostTracker
from src.utils.exceptions import CostLimitExceededError, RateLimitExceededError


@pytest.mark.unit
class TestCostTracker:
    """Test cost tracking functionality"""

    def test_track_bedrock_call_calculates_cost(self):
        """Test cost calculation for Bedrock API call"""
        tracker = CostTracker(max_cost_per_request=100.0)

        cost = tracker.track_bedrock_call(
            request_id="req-123",
            model_id="claude-sonnet-4-5",
            input_tokens=100000,   # 100k tokens
            output_tokens=10000    # 10k tokens
        )

        # Cost = (100k/1M)*$3 + (10k/1M)*$15 = $0.30 + $0.15 = $0.45
        assert cost == Decimal('0.45')

    def test_cumulative_cost_tracking(self):
        """Test cost accumulates across multiple calls"""
        tracker = CostTracker(max_cost_per_request=100.0)

        # Call 1: $0.45
        tracker.track_bedrock_call("req-123", "claude-sonnet-4-5", 100000, 10000)

        # Call 2: $0.30
        tracker.track_bedrock_call("req-123", "claude-sonnet-4-5", 50000, 10000)

        # Total: $0.75
        total_cost = tracker.get_request_cost("req-123")
        assert total_cost == Decimal('0.75')

    def test_cost_limit_enforcement(self):
        """Test cost limit is enforced"""
        tracker = CostTracker(max_cost_per_request=1.0)  # $1 limit

        # Make calls that exceed limit
        tracker.track_bedrock_call("req-123", "claude-sonnet-4-5", 100000, 10000)  # $0.45

        # This call would push total to $0.90 (under limit)
        tracker.track_bedrock_call("req-123", "claude-sonnet-4-5", 100000, 10000)  # $0.45

        # This call would exceed limit
        with pytest.raises(CostLimitExceededError) as exc:
            tracker.track_bedrock_call("req-123", "claude-sonnet-4-5", 100000, 10000)

        assert "exceeded cost limit" in str(exc.value)

    def test_token_limit_enforcement(self):
        """Test token limit is enforced"""
        tracker = CostTracker(
            max_cost_per_request=100.0,
            max_tokens_per_request=200000  # 200k tokens
        )

        # Use 150k tokens (under limit)
        tracker.track_bedrock_call("req-123", "claude-sonnet-4-5", 100000, 50000)

        # Attempt to use another 100k tokens (would exceed 200k limit)
        with pytest.raises(RateLimitExceededError) as exc:
            tracker.track_bedrock_call("req-123", "claude-sonnet-4-5", 50000, 50000)

        assert "exceeded token limit" in str(exc.value)

    def test_separate_request_tracking(self):
        """Test different requests are tracked separately"""
        tracker = CostTracker(max_cost_per_request=100.0)

        tracker.track_bedrock_call("req-123", "claude-sonnet-4-5", 100000, 10000)
        tracker.track_bedrock_call("req-456", "claude-sonnet-4-5", 50000, 5000)

        cost_123 = tracker.get_request_cost("req-123")
        cost_456 = tracker.get_request_cost("req-456")

        assert cost_123 == Decimal('0.45')
        assert cost_456 == Decimal('0.225')

    def test_get_request_metrics(self):
        """Test getting request metrics"""
        tracker = CostTracker(max_cost_per_request=100.0)

        tracker.track_bedrock_call("req-123", "claude-sonnet-4-5", 100000, 10000)
        tracker.track_bedrock_call("req-123", "claude-sonnet-4-5", 50000, 5000)

        assert tracker.get_request_cost("req-123") == Decimal('0.675')
        assert tracker.get_request_tokens("req-123") == 165000
        assert tracker.get_request_calls("req-123") == 2

    def test_get_all_metrics(self):
        """Test getting all tracked metrics"""
        tracker = CostTracker(max_cost_per_request=100.0)

        tracker.track_bedrock_call("req-123", "claude-sonnet-4-5", 100000, 10000)
        tracker.track_bedrock_call("req-456", "claude-sonnet-4-5", 50000, 5000)

        metrics = tracker.get_all_metrics()

        assert metrics['total_requests'] == 2
        assert metrics['total_calls'] == 2
        assert metrics['total_tokens'] == 165000
        assert float(metrics['total_cost_usd']) == pytest.approx(0.675, rel=0.01)

    def test_reset_request(self):
        """Test resetting request tracking"""
        tracker = CostTracker(max_cost_per_request=100.0)

        tracker.track_bedrock_call("req-123", "claude-sonnet-4-5", 100000, 10000)
        assert tracker.get_request_cost("req-123") > 0

        tracker.reset_request("req-123")
        assert tracker.get_request_cost("req-123") == Decimal('0')
