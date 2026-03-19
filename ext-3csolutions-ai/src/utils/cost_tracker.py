"""
Cost Tracking and Rate Limiting for LLM Operations
Prevents cost explosions from malicious or accidental overuse
"""

import time
from typing import Dict, Optional
from decimal import Decimal
from datetime import datetime
from ..utils.exceptions import CostLimitExceededError, RateLimitExceededError
from ..utils.logger import get_logger

logger = get_logger(__name__)


# Claude Haiku 4.5 Pricing on AWS Bedrock (as of 2025)
CLAUDE_HAIKU_4_5_INPUT_COST_PER_1M = Decimal('0.80')   # $0.80 per 1M input tokens
CLAUDE_HAIKU_4_5_OUTPUT_COST_PER_1M = Decimal('4.00')  # $4.00 per 1M output tokens


class CostTracker:
    """
    Tracks Bedrock LLM costs and enforces limits per request.

    Prevents cost explosions by:
    1. Tracking token usage and calculating costs
    2. Enforcing per-request cost limits
    3. Providing cost visibility via metrics

    Example:
        tracker = CostTracker(max_cost_per_request=100.0)

        tracker.track_bedrock_call(
            request_id="req-123",
            model_id="claude-haiku-4-5",
            input_tokens=50000,
            output_tokens=5000
        )
        # Cost = (50k/1M)*$1.00 + (5k/1M)*$5.00 = $0.05 + $0.025 = $0.075

        total_cost = tracker.get_request_cost("req-123")
        # Returns: Decimal('0.225')
    """

    def __init__(
        self,
        max_cost_per_request: float = 100.0,
        max_tokens_per_request: int = 10_000_000
    ):
        """
        Initialize cost tracker.

        Args:
            max_cost_per_request: Maximum USD allowed per request_id (default: $100)
            max_tokens_per_request: Maximum total tokens per request_id (default: 10M)
        """
        self.max_cost_per_request = Decimal(str(max_cost_per_request))
        self.max_tokens_per_request = max_tokens_per_request

        # Track costs by request_id
        self.request_costs: Dict[str, Decimal] = {}
        self.request_tokens: Dict[str, int] = {}
        self.request_call_counts: Dict[str, int] = {}

        logger.info(
            f"CostTracker initialized: max_cost=${max_cost_per_request}, "
            f"max_tokens={max_tokens_per_request:,}"
        )

    def track_bedrock_call(
        self,
        request_id: str,
        model_id: str,
        input_tokens: int,
        output_tokens: int
    ) -> Decimal:
        """
        Track a Bedrock API call and calculate cost.

        Args:
            request_id: Unique pipeline request ID
            model_id: Bedrock model ID
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens

        Returns:
            Cost of this call in USD (Decimal)

        Raises:
            CostLimitExceededError: If request exceeds cost limit
            RateLimitExceededError: If request exceeds token limit
        """
        # Calculate cost for this call
        call_cost = self._calculate_cost(model_id, input_tokens, output_tokens)

        # Update totals
        self.request_costs[request_id] = self.request_costs.get(request_id, Decimal('0')) + call_cost
        self.request_tokens[request_id] = self.request_tokens.get(request_id, 0) + input_tokens + output_tokens
        self.request_call_counts[request_id] = self.request_call_counts.get(request_id, 0) + 1

        total_cost = self.request_costs[request_id]
        total_tokens = self.request_tokens[request_id]
        call_count = self.request_call_counts[request_id]

        logger.info(
            f"Bedrock call tracked: ${call_cost:.4f} "
            f"(total: ${total_cost:.2f}, {total_tokens:,} tokens, {call_count} calls)",
            extra={
                'request_id': request_id,
                'model_id': model_id,
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
                'call_cost': float(call_cost),
                'total_cost': float(total_cost),
                'total_tokens': total_tokens,
                'call_count': call_count
            }
        )

        # Check limits
        if total_cost > self.max_cost_per_request:
            logger.error(
                f"⚠️  COST LIMIT EXCEEDED for request {request_id}",
                extra={
                    'request_id': request_id,
                    'total_cost': float(total_cost),
                    'max_cost': float(self.max_cost_per_request),
                    'call_count': call_count
                }
            )
            raise CostLimitExceededError(
                f"Request {request_id} exceeded cost limit: "
                f"${total_cost:.2f} > ${self.max_cost_per_request:.2f} "
                f"({call_count} Bedrock calls)"
            )

        if total_tokens > self.max_tokens_per_request:
            logger.error(
                f"⚠️  TOKEN LIMIT EXCEEDED for request {request_id}",
                extra={
                    'request_id': request_id,
                    'total_tokens': total_tokens,
                    'max_tokens': self.max_tokens_per_request
                }
            )
            raise RateLimitExceededError(
                f"Request {request_id} exceeded token limit: "
                f"{total_tokens:,} > {self.max_tokens_per_request:,} tokens"
            )

        return call_cost

    def _calculate_cost(self, model_id: str, input_tokens: int, output_tokens: int) -> Decimal:
        """
        Calculate cost for a Bedrock API call.

        Args:
            model_id: Bedrock model ID
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens

        Returns:
            Cost in USD (Decimal)
        """
        # Default to Claude Haiku 4.5 pricing
        if 'claude-haiku-4-5' in model_id.lower() or 'haiku-4' in model_id.lower():
            input_cost_per_1m = CLAUDE_HAIKU_4_5_INPUT_COST_PER_1M
            output_cost_per_1m = CLAUDE_HAIKU_4_5_OUTPUT_COST_PER_1M
        else:
            # Conservative estimate for unknown models
            logger.warning(f"Unknown model pricing: {model_id}, using Claude Haiku 4.5 rates")
            input_cost_per_1m = CLAUDE_HAIKU_4_5_INPUT_COST_PER_1M
            output_cost_per_1m = CLAUDE_HAIKU_4_5_OUTPUT_COST_PER_1M

        # Calculate: (tokens / 1,000,000) * cost_per_million
        input_cost = (Decimal(input_tokens) / Decimal('1000000')) * input_cost_per_1m
        output_cost = (Decimal(output_tokens) / Decimal('1000000')) * output_cost_per_1m

        return input_cost + output_cost

    def get_request_cost(self, request_id: str) -> Decimal:
        """Get total cost for a request."""
        return self.request_costs.get(request_id, Decimal('0'))

    def get_request_tokens(self, request_id: str) -> int:
        """Get total tokens for a request."""
        return self.request_tokens.get(request_id, 0)

    def get_request_calls(self, request_id: str) -> int:
        """Get total Bedrock calls for a request."""
        return self.request_call_counts.get(request_id, 0)

    def get_all_metrics(self) -> dict:
        """Get all tracked metrics (for monitoring)."""
        return {
            'total_requests': len(self.request_costs),
            'total_cost_usd': float(sum(self.request_costs.values())),
            'total_tokens': sum(self.request_tokens.values()),
            'total_calls': sum(self.request_call_counts.values()),
            'max_cost_limit': float(self.max_cost_per_request),
            'max_token_limit': self.max_tokens_per_request
        }

    def reset_request(self, request_id: str):
        """Reset tracking for a request (for testing/cleanup)."""
        self.request_costs.pop(request_id, None)
        self.request_tokens.pop(request_id, None)
        self.request_call_counts.pop(request_id, None)


# Global cost tracker instance
_cost_tracker: Optional[CostTracker] = None


def get_cost_tracker(
    max_cost_per_request: float = 100.0,
    max_tokens_per_request: int = 10_000_000
) -> CostTracker:
    """
    Get or create the global cost tracker instance.

    Args:
        max_cost_per_request: Maximum USD per request (default: $100)
        max_tokens_per_request: Maximum tokens per request (default: 10M)

    Returns:
        CostTracker instance
    """
    global _cost_tracker
    if _cost_tracker is None:
        _cost_tracker = CostTracker(max_cost_per_request, max_tokens_per_request)
    return _cost_tracker


def reset_cost_tracker():
    """Reset global cost tracker (for testing)."""
    global _cost_tracker
    _cost_tracker = None
