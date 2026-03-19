"""
Enhanced Bedrock Adapter with Retry Logic and JSON Parsing
Provides LLM operations with automatic retries and response parsing
"""

import json
import base64
from typing import Dict, Any
from botocore.exceptions import ClientError

from src.utils.config import PipelineConfig
from src.utils.logger import get_logger
from src.utils.exceptions import BedrockOperationError
from src.utils.retry import retry_on_throttling
from src.utils.constants import LLM_MAX_TOKENS, LLM_TEMPERATURE
from src.utils.circuit_breaker import CircuitBreaker
from src.utils.cost_tracker import get_cost_tracker

logger = get_logger(__name__)


class BedrockAdapter:
    """
    Enhanced adapter for AWS Bedrock LLM operations.

    Features:
    - Automatic retry with exponential backoff
    - Circuit breaker (prevents cascading failures)
    - Cost tracking and rate limiting
    - Proper error handling
    - JSON response parsing
    - PDF document handling
    """

    def __init__(self, config: PipelineConfig):
        """
        Initialize Bedrock adapter with configuration.

        Args:
            config: Pipeline configuration instance
        """
        self.bedrock_client = config.bedrock_client
        self.model_id = config.bedrock_model

        # Circuit breaker to prevent cascading failures
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=5,
            success_threshold=2,
            timeout=60,
            name="Bedrock"
        )

        # Cost tracker for rate limiting
        self.cost_tracker = get_cost_tracker(
            max_cost_per_request=100.0,  # $100 limit per request
            max_tokens_per_request=10_000_000  # 10M tokens per request
        )

    def invoke_claude(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = LLM_MAX_TOKENS,
        temperature: float = LLM_TEMPERATURE,
        request_id: str = None
    ) -> str:
        """
        Invoke Claude model with text input, circuit breaker, and cost tracking.

        Args:
            system_prompt: System instructions for Claude
            user_prompt: User message/prompt
            max_tokens: Maximum tokens in response (default: 5000)
            temperature: Temperature for response randomness (default: 0)
            request_id: Optional request ID for cost tracking

        Returns:
            Claude's response text

        Raises:
            BedrockOperationError: If invocation fails
            CircuitOpenError: If circuit breaker is OPEN
            CostLimitExceededError: If cost limit exceeded
        """
        # Use circuit breaker to protect against cascading failures
        return self.circuit_breaker.call(
            self._invoke_claude_impl,
            system_prompt,
            user_prompt,
            max_tokens,
            temperature,
            request_id
        )

    @retry_on_throttling(max_attempts=5, backoff_base=2, max_backoff=60)
    def _invoke_claude_impl(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        request_id: str = None
    ) -> str:
        """Internal implementation of Claude invocation (wrapped by circuit breaker)."""
        import time

        # Log API call start
        api_start_time = time.time()
        prompt_size = len(system_prompt) + len(user_prompt)

        logger.info(
            f"Bedrock API call starting: text mode, prompt_size={prompt_size} chars",
            extra={
                'model_id': self.model_id,
                'prompt_size_chars': prompt_size,
                'max_tokens': max_tokens,
                'temperature': temperature,
                'request_id': request_id,
                'api_call_status': 'STARTED'
            }
        )

        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature
        }

        try:
            response = self.bedrock_client.invoke_model(
                modelId=self.model_id,
                body=json.dumps(payload),
                accept="application/json",
                contentType="application/json"
            )

            # Read response body once (StreamingBody can only be read once)
            response_bytes = response['body'].read()
            body = json.loads(response_bytes)

            # Calculate API duration
            api_duration = time.time() - api_start_time

            # Extract token usage
            usage = body.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            total_tokens = input_tokens + output_tokens

            # Log API call completion
            logger.info(
                f"✓ Bedrock API call completed: {api_duration:.2f}s, tokens={total_tokens}",
                extra={
                    'model_id': self.model_id,
                    'api_duration_seconds': round(api_duration, 2),
                    'input_tokens': input_tokens,
                    'output_tokens': output_tokens,
                    'total_tokens': total_tokens,
                    'request_id': request_id,
                    'api_call_status': 'COMPLETED'
                }
            )

            # Extract text from response
            content = body.get("content")
            response_text = ""
            if isinstance(content, list) and len(content) > 0:
                response_text = content[0].get("text", "")
            elif isinstance(content, str):
                response_text = content
            else:
                logger.warning(
                    "Unexpected response format from Bedrock",
                    extra={'content_type': type(content).__name__}
                )
                response_text = str(content)

            # Cost tracking
            if request_id:
                if input_tokens > 0 or output_tokens > 0:
                    self.cost_tracker.track_bedrock_call(
                        request_id=request_id,
                        model_id=self.model_id,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens
                    )

            return response_text

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            logger.error(
                f"Bedrock invocation failed: {e}",
                extra={
                    'error_code': error_code,
                    'model_id': self.model_id
                },
                exc_info=True
            )
            raise BedrockOperationError(
                f"Bedrock invocation failed: {error_code}"
            ) from e

        except json.JSONDecodeError as e:
            logger.error(
                f"Failed to parse Bedrock response as JSON: {e}",
                exc_info=True
            )
            raise BedrockOperationError(
                "Failed to parse Bedrock response as JSON"
            ) from e

        except Exception as e:
            logger.error(
                f"Unexpected error invoking Bedrock: {e}",
                exc_info=True
            )
            raise BedrockOperationError(
                f"Unexpected error invoking Bedrock"
            ) from e

    @retry_on_throttling(max_attempts=5, backoff_base=2, max_backoff=60)
    def invoke_claude_with_pdf(
        self,
        pdf_bytes: bytes,
        system_prompt: str,
        max_tokens: int = LLM_MAX_TOKENS,
        temperature: float = LLM_TEMPERATURE,
        request_id: str = None
    ) -> str:
        """
        Send PDF directly to Claude without using Textract.
        Uses Claude's native document understanding capabilities.

        Args:
            pdf_bytes: PDF file content as bytes
            system_prompt: System instructions for Claude
            max_tokens: Maximum tokens in response (default: 5000)
            temperature: Temperature for response randomness (default: 0)

        Returns:
            Claude's response text

        Raises:
            BedrockOperationError: If invocation fails
        """
        import time

        # Log API call start with request details
        api_start_time = time.time()
        pdf_size_kb = round(len(pdf_bytes) / 1024, 2)

        logger.info(
            f"Bedrock API call starting: PDF size={pdf_size_kb}KB",
            extra={
                'model_id': self.model_id,
                'pdf_size_bytes': len(pdf_bytes),
                'pdf_size_kb': pdf_size_kb,
                'max_tokens': max_tokens,
                'temperature': temperature,
                'api_call_status': 'STARTED'
            }
        )

        # Encode PDF as base64
        pdf_base64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_base64
                            }
                        },
                        {
                            "type": "text",
                            "text": "Please extract all fields from this document according to the System Instructions. Output only valid JSON."
                        }
                    ]
                }
            ],
            "max_tokens": max_tokens,
            "temperature": temperature
        }

        try:
            response = self.bedrock_client.invoke_model(
                modelId=self.model_id,
                body=json.dumps(payload),
                accept="application/json",
                contentType="application/json"
            )

            # Read response body once
            response_bytes = response['body'].read()
            body = json.loads(response_bytes)

            # Calculate API call duration
            api_duration = time.time() - api_start_time

            # Extract token usage
            usage = body.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            total_tokens = input_tokens + output_tokens

            # Log API call completion with response details
            logger.info(
                f"✓ Bedrock API call completed: {api_duration:.2f}s, tokens={total_tokens}",
                extra={
                    'model_id': self.model_id,
                    'api_duration_seconds': round(api_duration, 2),
                    'input_tokens': input_tokens,
                    'output_tokens': output_tokens,
                    'total_tokens': total_tokens,
                    'api_call_status': 'COMPLETED'
                }
            )

            # Extract text from response
            content = body.get("content")
            if isinstance(content, list) and len(content) > 0:
                response_text = content[0].get("text", "")
            elif isinstance(content, str):
                response_text = content
            else:
                logger.warning(
                    "Unexpected response format from Bedrock (PDF mode)",
                    extra={'content_type': type(content).__name__}
                )
                response_text = str(content)

            # Cost tracking
            if request_id and (input_tokens > 0 or output_tokens > 0):
                self.cost_tracker.track_bedrock_call(
                    request_id=request_id,
                    model_id=self.model_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens
                )

            return {
                "text": response_text,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens
            }

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            logger.error(
                f"Bedrock PDF invocation failed: {e}",
                extra={
                    'error_code': error_code,
                    'model_id': self.model_id,
                    'pdf_size_bytes': len(pdf_bytes)
                },
                exc_info=True
            )
            raise BedrockOperationError(
                f"Bedrock PDF invocation failed: {error_code}"
            ) from e

        except json.JSONDecodeError as e:
            logger.error(
                f"Failed to parse Bedrock PDF response as JSON: {e}",
                exc_info=True
            )
            raise BedrockOperationError(
                "Failed to parse Bedrock PDF response as JSON"
            ) from e

        except Exception as e:
            logger.error(
                f"Unexpected error invoking Bedrock with PDF: {e}",
                extra={'pdf_size_bytes': len(pdf_bytes)},
                exc_info=True
            )
            raise BedrockOperationError(
                f"Unexpected error invoking Bedrock with PDF"
            ) from e


# --- Utility Functions ---

def extract_json_from_codeblock(text: str) -> str:
    """
    Extract JSON from markdown code block if present.

    Handles responses like:
    ```json
    {"key": "value"}
    ```

    Args:
        text: Response text potentially containing markdown code blocks

    Returns:
        Extracted JSON string (or original text if no code block)
    """
    if not isinstance(text, str):
        return text

    if '```' not in text:
        return text

    # Find code block boundaries
    start = text.find("```")
    if start == -1:
        return text

    # Skip language identifier if present (e.g., ```json)
    content_start = text.find("\n", start)
    if content_start == -1:
        content_start = start + 3
    else:
        content_start += 1

    # Find closing ```
    end = text.rfind("```")
    if end > content_start:
        return text[content_start:end].strip()

    return text


def remove_blank_fields(parsed_json: Any) -> Any:
    """
    Recursively remove empty fields from JSON structure.

    Removes fields with values: "", None, [], {}

    Args:
        parsed_json: Parsed JSON object (dict, list, or primitive)

    Returns:
        Cleaned JSON structure with blank fields removed
    """
    if isinstance(parsed_json, dict):
        return {
            key: remove_blank_fields(value)
            for key, value in parsed_json.items()
            if value not in ("", None, [], {})
        }
    elif isinstance(parsed_json, list):
        return [
            remove_blank_fields(item)
            for item in parsed_json
            if item not in ("", None, [], {})
        ]
    else:
        return parsed_json
