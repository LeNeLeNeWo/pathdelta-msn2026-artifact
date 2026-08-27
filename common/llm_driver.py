"""
LLM Driver - Centralized LLM client wrappers for PathDelta.

This module provides centralized API clients for all LLM providers used in PathDelta.
ALL LLM calls should go through this module to ensure consistency and maintainability.

**Supported Providers:**
- DeepSeek (via OpenAI-compatible API)
- Qwen/DashScope (via dashscope SDK)

**Usage:**
- For neural synthesis: use get_driver() -> DeepSeekDriver
- For style evaluation: use get_qwen_driver() -> QwenDriver

**Retry Policy:**
- Retries are ONLY for network errors: timeout, 429 (rate limit), 5xx (server errors)
- NO retry for semantic errors or invalid responses
- NO dummy fallback behavior - errors propagate to caller
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Global counters for API call tracking
_api_call_counter = 0
_total_api_calls = 0

# Per-intent timeout control
_intent_deadline: Optional[float] = None  # Unix timestamp
_intent_start_time: Optional[float] = None
_intent_llm_calls: int = 0
_max_llm_calls_per_intent: Optional[int] = None


@dataclass
class TokenUsage:
    """Provider-reported token usage for the current intent.

    Values are never estimated.  ``provider_reported`` is false when the API
    response does not expose a usage object, so a genuine zero can be
    distinguished from unavailable accounting.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_prompt_tokens: int = 0
    cache_hit_prompt_tokens: int = 0
    cache_miss_prompt_tokens: int = 0
    reasoning_tokens: int = 0
    provider_reported: bool = False


@dataclass
class LLMUsageSnapshot:
    """Unambiguous LLM counters for one intent/case.

    ``logical_requests`` counts calls to ``chat_completion``. ``llm_calls``
    counts actual HTTP/API attempts, including attempts caused by transient
    API retries.  Repair retries belong to the synthesis runner and are
    recorded separately by the MSN2026 metrics layer.
    """

    logical_requests: int = 0
    llm_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    api_retries: int = 0
    llm_latency_seconds: float = 0.0
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    backend: Dict[str, Any] = field(default_factory=dict)
    provider_calls: list[Dict[str, Any]] = field(default_factory=list)


_intent_llm_usage = LLMUsageSnapshot()


def _reset_intent_llm_usage() -> None:
    """Reset per-intent usage without changing timeout/budget settings."""
    global _intent_llm_calls, _intent_llm_usage
    _intent_llm_calls = 0
    _intent_llm_usage = LLMUsageSnapshot()


def get_intent_llm_usage() -> Dict[str, Any]:
    """Return a detached, JSON-serializable usage snapshot for this intent."""
    return asdict(_intent_llm_usage)


def _read_usage_value(obj: Any, name: str) -> int:
    """Read an integer field from SDK objects or dictionaries."""
    if obj is None:
        return 0
    value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _record_response_usage(response: Any) -> None:
    """Accumulate provider-reported OpenAI-compatible token usage."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return

    prompt_tokens = _read_usage_value(usage, "prompt_tokens")
    completion_tokens = _read_usage_value(usage, "completion_tokens")
    total_tokens = _read_usage_value(usage, "total_tokens")
    if total_tokens == 0 and (prompt_tokens or completion_tokens):
        total_tokens = prompt_tokens + completion_tokens

    prompt_details = (
        usage.get("prompt_tokens_details")
        if isinstance(usage, dict)
        else getattr(usage, "prompt_tokens_details", None)
    )
    completion_details = (
        usage.get("completion_tokens_details")
        if isinstance(usage, dict)
        else getattr(usage, "completion_tokens_details", None)
    )

    tokens = _intent_llm_usage.token_usage
    tokens.prompt_tokens += prompt_tokens
    tokens.completion_tokens += completion_tokens
    tokens.total_tokens += total_tokens
    cache_hit = _read_usage_value(usage, "prompt_cache_hit_tokens")
    cache_miss = _read_usage_value(usage, "prompt_cache_miss_tokens")
    if cache_hit == 0:
        cache_hit = _read_usage_value(prompt_details, "cached_tokens")
    if cache_miss == 0 and prompt_tokens >= cache_hit:
        cache_miss = prompt_tokens - cache_hit
    tokens.cached_prompt_tokens += cache_hit
    tokens.cache_hit_prompt_tokens += cache_hit
    tokens.cache_miss_prompt_tokens += cache_miss
    tokens.reasoning_tokens += _read_usage_value(completion_details, "reasoning_tokens")
    tokens.provider_reported = True


def _json_safe_response(response: Any) -> Any:
    """Return the complete provider response in a JSON-serializable form."""
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        try:
            return response.model_dump(mode="json")
        except TypeError:
            return response.model_dump()
    return {"repr": repr(response)}


def _record_provider_call(
    *, response: Any | None, logical_request_id: str, attempt: int,
    request: Dict[str, Any], driver: "DeepSeekDriver", latency: float,
    error: Exception | None = None,
) -> None:
    """Append one physical API attempt, including failures and provider IDs."""
    choice = None
    if response is not None:
        choices = response.get("choices", []) if isinstance(response, dict) else getattr(response, "choices", [])
        choice = choices[0] if choices else None
    finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else getattr(choice, "finish_reason", None)
    request_id = None
    if response is not None:
        request_id = (response.get("id") if isinstance(response, dict) else getattr(response, "id", None)) or getattr(response, "_request_id", None)
    _intent_llm_usage.provider_calls.append({
        "logical_request_id": logical_request_id,
        "attempt_index": attempt,
        "status": "error" if error else "success",
        "request_id": request_id,
        "finish_reason": finish_reason,
        "provider": "deepseek",
        "model_requested": driver.model,
        "model_reported": (response.get("model") if isinstance(response, dict) else getattr(response, "model", None)) if response is not None else None,
        "base_url": driver.base_url,
        "latency_seconds": latency,
        "request": request,
        "raw_response": _json_safe_response(response) if response is not None else None,
        "error": {"type": type(error).__name__, "message": str(error)} if error else None,
    })


def _require_api_key(
    explicit_key: Optional[str],
    env_var: str,
    provider_name: str,
) -> str:
    """
    Resolve an API key from an explicit argument or environment variable.

    We intentionally do not keep repository-default fallback secrets here.
    Demo and production runs should inject credentials through the process
    environment so the key is not persisted to source control or artifacts.
    """
    api_key = explicit_key or os.environ.get(env_var)
    if api_key:
        return api_key

    raise ValueError(
        f"{provider_name} API key is not configured. "
        f"Set the environment variable `{env_var}` before running PathDelta."
    )


class LLMTimeoutError(Exception):
    """Raised when LLM call exceeds intent deadline or max calls."""
    def __init__(self, message: str, llm_calls: int, elapsed: float, reason: str):
        super().__init__(message)
        self.llm_calls = llm_calls
        self.elapsed = elapsed
        self.reason = reason  # "deadline" or "max_calls"


def set_intent_deadline(timeout_sec: float) -> None:
    """Set deadline for current intent (timeout_sec from now)."""
    global _intent_deadline, _intent_start_time
    _intent_start_time = time.time()
    _intent_deadline = _intent_start_time + timeout_sec
    _reset_intent_llm_usage()


def clear_intent_deadline() -> None:
    """Clear intent deadline."""
    global _intent_deadline, _intent_start_time
    _intent_deadline = None
    _intent_start_time = None
    _reset_intent_llm_usage()


def set_max_llm_calls(max_calls: int) -> None:
    """Set max LLM calls per intent."""
    global _max_llm_calls_per_intent
    _max_llm_calls_per_intent = max_calls


def clear_max_llm_calls() -> None:
    """Clear max LLM calls limit."""
    global _max_llm_calls_per_intent
    _max_llm_calls_per_intent = None


def get_intent_llm_calls() -> int:
    """Get current intent's LLM call count."""
    return _intent_llm_calls


def _check_intent_limits() -> None:
    """Check if intent limits are exceeded before LLM call. Raises LLMTimeoutError if so."""
    global _intent_llm_calls
    now = time.time()
    elapsed = now - _intent_start_time if _intent_start_time else 0
    
    if _intent_deadline is not None and now >= _intent_deadline:
        raise LLMTimeoutError(
            f"Intent deadline exceeded (elapsed={elapsed:.1f}s, llm_calls={_intent_llm_calls})",
            llm_calls=_intent_llm_calls,
            elapsed=elapsed,
            reason="deadline",
        )
    
    if _max_llm_calls_per_intent is not None and _intent_llm_calls >= _max_llm_calls_per_intent:
        raise LLMTimeoutError(
            f"Max LLM calls exceeded (limit={_max_llm_calls_per_intent}, calls={_intent_llm_calls})",
            llm_calls=_intent_llm_calls,
            elapsed=elapsed,
            reason="max_calls",
        )


# =============================================================================
# Retry Configuration
# =============================================================================

# Default retry settings for network errors
DEFAULT_MAX_RETRIES = 3
DEFAULT_INITIAL_BACKOFF = 1.0  # seconds
DEFAULT_MAX_BACKOFF = 30.0  # seconds
DEFAULT_BACKOFF_MULTIPLIER = 2.0

# HTTP status codes that trigger retry
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_retryable_error(error: Exception) -> bool:
    """
    Check if an error is retryable (network error, timeout, rate limit, server error).
    
    Args:
        error: The exception to check
        
    Returns:
        True if the error should trigger a retry, False otherwise
    """
    error_str = str(error).lower()
    
    # Check for timeout errors
    if "timeout" in error_str or "timed out" in error_str:
        return True
    
    # Check for connection errors
    if any(term in error_str for term in ["connection", "network", "socket", "refused"]):
        return True
    
    # Check for rate limit (429)
    if "429" in error_str or "rate limit" in error_str or "too many requests" in error_str:
        return True
    
    # Check for server errors (5xx)
    for code in [500, 502, 503, 504]:
        if str(code) in error_str:
            return True
    
    # Check for OpenAI-specific error types
    try:
        from openai import APITimeoutError, APIConnectionError, RateLimitError, InternalServerError
        if isinstance(error, (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)):
            return True
    except ImportError:
        pass
    
    return False


def _calculate_backoff(
    attempt: int,
    initial_backoff: float = DEFAULT_INITIAL_BACKOFF,
    max_backoff: float = DEFAULT_MAX_BACKOFF,
    multiplier: float = DEFAULT_BACKOFF_MULTIPLIER,
) -> float:
    """
    Calculate exponential backoff delay for a retry attempt.
    
    Args:
        attempt: The current attempt number (0-indexed)
        initial_backoff: Initial backoff delay in seconds
        max_backoff: Maximum backoff delay in seconds
        multiplier: Backoff multiplier
        
    Returns:
        Backoff delay in seconds
    """
    delay = initial_backoff * (multiplier ** attempt)
    return min(delay, max_backoff)

try:
    from openai import OpenAI
except ImportError:
    raise ImportError(
        "openai package is required for DeepSeek integration. "
        "Install with: pip install openai"
    )


class DeepSeekDriverError(Exception):
    """Raised when the DeepSeek API call fails."""


class DeepSeekDriver:
    """
    Centralized DeepSeek API client for PathDelta.
    
    Uses the OpenAI-compatible interface provided by DeepSeek.
    All LLM interactions should go through this driver.
    """
    
    # API Configuration
    BASE_URL = "https://api.deepseek.com"
    DEFAULT_MODEL = "deepseek-chat"
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        """
        Initialize the DeepSeek driver.
        
        Args:
            api_key: API key (defaults to DEEPSEEK_API_KEY from environment)
            base_url: Base URL (defaults to https://api.deepseek.com)
            model: Model name (defaults to deepseek-chat)
        """
        self._api_key = _require_api_key(api_key, "DEEPSEEK_API_KEY", "DeepSeek")
        self._base_url = base_url or os.environ.get("DEEPSEEK_BASE_URL") or self.BASE_URL
        self._model = model or os.environ.get("DEEPSEEK_MODEL") or self.DEFAULT_MODEL
        
        # Task C: Initialize OpenAI client with DeepSeek configuration and timeout
        # timeout parameter: (connect_timeout, read_timeout) in seconds
        self._client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=120.0,  # 120 seconds total timeout for LLM calls
        )
    
    @property
    def model(self) -> str:
        """Get the current model name."""
        return self._model

    @property
    def base_url(self) -> str:
        """Get the configured API base URL (safe to record in a manifest)."""
        return self._base_url
    
    def chat_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> str:
        """
        Execute a chat completion request with retry for network errors.
        
        Args:
            system_prompt: System message content
            user_prompt: User message content
            json_mode: If True, request JSON response format
            temperature: Sampling temperature (0.0 for deterministic)
            max_tokens: Maximum tokens in response (None for default)
            max_retries: Maximum number of retries for network errors (default: 3)
        
        Returns:
            The assistant's response content as a string
        
        Raises:
            DeepSeekDriverError: If the API call fails after all retries
            LLMTimeoutError: If intent deadline exceeded or max LLM calls reached
            
        Note:
            Retries are ONLY for network errors (timeout, 429, 5xx).
            Semantic errors or invalid responses are NOT retried.
        """
        global _intent_llm_calls
        
        # Check intent limits BEFORE making the call
        _check_intent_limits()
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        
        kwargs: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            # MSN2026 freezes non-thinking mode for every comparable method.
            # DeepSeek V4 defaults to thinking mode, which can spend the whole
            # output budget before emitting a short configuration patch.
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        
        last_error: Optional[Exception] = None
        _intent_llm_usage.logical_requests += 1
        logical_request_id = str(uuid.uuid4())
        _intent_llm_usage.backend = {
            "provider": "deepseek", "base_url": self._base_url,
            "model": self._model, "api_mode": "openai_compatible",
            "thinking": "disabled", "temperature": temperature,
        }
        
        for attempt in range(max_retries + 1):
            # Check limits before each retry attempt
            _check_intent_limits()
            
            attempt_started = time.monotonic()
            try:
                _intent_llm_calls += 1
                _intent_llm_usage.llm_calls += 1
                response = self._client.chat.completions.create(**kwargs)
                _record_response_usage(response)
                content = response.choices[0].message.content
                if content is None:
                    raise DeepSeekDriverError("Empty response from DeepSeek API")
                latency = time.monotonic() - attempt_started
                _record_provider_call(
                    response=response, logical_request_id=logical_request_id,
                    attempt=attempt + 1, request=kwargs, driver=self,
                    latency=latency,
                )
                _intent_llm_usage.successful_calls += 1
                return content
            except LLMTimeoutError:
                raise  # Re-raise timeout errors immediately
            except Exception as e:
                last_error = e
                _intent_llm_usage.failed_calls += 1
                _record_provider_call(
                    response=None, logical_request_id=logical_request_id,
                    attempt=attempt + 1, request=kwargs, driver=self,
                    latency=time.monotonic() - attempt_started, error=e,
                )
                
                # Check if this is a retryable error
                if _is_retryable_error(e) and attempt < max_retries:
                    _intent_llm_usage.api_retries += 1
                    backoff = _calculate_backoff(attempt)
                    logger.warning(
                        f"DeepSeek API network error (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                        f"Retrying in {backoff:.1f}s..."
                    )
                    time.sleep(backoff)
                    continue
                
                # Non-retryable error or max retries exceeded
                break
            finally:
                _intent_llm_usage.llm_latency_seconds += (
                    time.monotonic() - attempt_started
                )
        
        # All retries exhausted or non-retryable error
        raise DeepSeekDriverError(f"DeepSeek API call failed: {last_error}") from last_error
    
    def extract_code_block(self, response: str, language: str = "") -> str:
        """
        Extract code block from a response that may contain markdown.
        
        Args:
            response: The raw response string
            language: Optional language hint (e.g., 'frr', 'json')
        
        Returns:
            Extracted code content, or original response if no code block found
        """
        # Try to match code blocks with optional language specifier
        patterns = [
            rf"```{language}\s*\n(.*?)```",  # With specific language
            r"```\w*\s*\n(.*?)```",           # With any language
            r"```(.*?)```",                    # Basic code block
        ]
        
        for pattern in patterns:
            match = re.search(pattern, response, re.DOTALL)
            if match:
                return match.group(1).strip()
        
        # No code block found, return original (stripped)
        return response.strip()


# Singleton instance for convenience
_default_driver: Optional[DeepSeekDriver] = None


def get_driver() -> DeepSeekDriver:
    """
    Get the default DeepSeek driver instance.
    
    Returns:
        Singleton DeepSeekDriver instance
    """
    global _default_driver
    if _default_driver is None:
        _default_driver = DeepSeekDriver()
    return _default_driver


# =============================================================================
# Qwen/DashScope Driver
# =============================================================================

class QwenDriverError(Exception):
    """Raised when the Qwen/DashScope API call fails."""


class QwenDriver:
    """
    Centralized Qwen API client for PathDelta via DashScope.
    
    Used primarily for LLM-as-a-Judge style evaluation.
    All Qwen/DashScope interactions should go through this driver.
    """
    
    # API Configuration
    # Default judge model: qwen-plus (cost-effective for style evaluation)
    # All candidates (qwen-max, qwen-plus, qwen-long-latest) passed smoke test
    DEFAULT_MODEL = "qwen-plus"
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        """
        Initialize the Qwen driver.
        
        Args:
            api_key: DashScope API key (defaults to DASHSCOPE_API_KEY from environment)
            model: Model name (defaults to qwen-max)
        """
        self._api_key = _require_api_key(api_key, "DASHSCOPE_API_KEY", "DashScope/Qwen")
        self._model = model or self.DEFAULT_MODEL
        self._dashscope_available = False
        
        # Try to import dashscope
        try:
            import dashscope
            self._dashscope = dashscope
            self._dashscope_available = True
        except ImportError:
            logger.warning("dashscope not installed - Qwen driver will return defaults")
            self._dashscope = None
    
    @property
    def has_api_key(self) -> bool:
        """Check if an API key is configured."""
        return bool(self._api_key)
    
    @property
    def model(self) -> str:
        """Get the current model name."""
        return self._model
    
    @property
    def is_available(self) -> bool:
        """Check if dashscope is available."""
        return self._dashscope_available
    
    def chat_completion(
        self,
        prompt: str,
        result_format: str = 'message',
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> str:
        """
        Execute a chat completion request via DashScope with retry for network errors.
        
        Args:
            prompt: The prompt to send
            result_format: Response format (default 'message')
            max_retries: Maximum number of retries for network errors (default: 3)
        
        Returns:
            The response content as a string
        
        Raises:
            QwenDriverError: If the API call fails after all retries, dashscope not available, or no API key
            
        Note:
            Retries are ONLY for network errors (timeout, 429, 5xx).
            Semantic errors or invalid responses are NOT retried.
        """
        if not self._dashscope_available:
            raise QwenDriverError("dashscope library not installed")
        
        if not self._api_key:
            raise QwenDriverError(
                "DashScope API key not configured. "
                "Pass api_key parameter to QwenDriver."
            )
        
        last_error: Optional[Exception] = None
        
        for attempt in range(max_retries + 1):
            try:
                from dashscope import Generation
                
                # Set API key
                self._dashscope.api_key = self._api_key
                
                # Call the API
                response = Generation.call(
                    model=self._model,
                    prompt=prompt,
                    result_format=result_format,
                )
                
                if response.status_code == 200:
                    return response.output.choices[0].message.content
                else:
                    # Check if this is a retryable status code
                    if response.status_code in RETRYABLE_STATUS_CODES and attempt < max_retries:
                        backoff = _calculate_backoff(attempt)
                        logger.warning(
                            f"DashScope API error {response.status_code} (attempt {attempt + 1}/{max_retries + 1}): "
                            f"{response.message}. Retrying in {backoff:.1f}s..."
                        )
                        time.sleep(backoff)
                        continue
                    
                    raise QwenDriverError(
                        f"DashScope API error: {response.code} - {response.message}"
                    )
            except ImportError:
                raise QwenDriverError("dashscope Generation module not available")
            except QwenDriverError:
                raise  # Re-raise QwenDriverError without retry
            except Exception as e:
                last_error = e
                
                # Check if this is a retryable error
                if _is_retryable_error(e) and attempt < max_retries:
                    backoff = _calculate_backoff(attempt)
                    logger.warning(
                        f"DashScope API network error (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                        f"Retrying in {backoff:.1f}s..."
                    )
                    time.sleep(backoff)
                    continue
                
                # Non-retryable error or max retries exceeded
                break
        
        # All retries exhausted or non-retryable error
        raise QwenDriverError(f"Qwen API call failed: {last_error}") from last_error
    
    def extract_json_response(self, response: str) -> Dict[str, Any]:
        """
        Extract JSON from a response that may contain markdown.
        
        Args:
            response: The raw response string
        
        Returns:
            Parsed JSON as a dictionary
        
        Raises:
            QwenDriverError: If JSON parsing fails
        """
        text = response.strip()
        
        # Handle markdown code blocks
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise QwenDriverError(f"Failed to parse JSON response: {e}") from e


# Singleton instance for Qwen driver
_default_qwen_driver: Optional[QwenDriver] = None


def get_qwen_driver() -> QwenDriver:
    """
    Get the default Qwen driver instance.
    
    Returns:
        Singleton QwenDriver instance
    """
    global _default_qwen_driver
    if _default_qwen_driver is None:
        _default_qwen_driver = QwenDriver()
    return _default_qwen_driver


# =============================================================================
# Style Judge API (Centralized LLM-as-a-Judge)
# =============================================================================

# Prompt template for style-only evaluation
# Prompt template for style-only evaluation
STYLE_JUDGE_PROMPT = """You are evaluating the STYLE SIMILARITY between two FRR router configurations.

Reference Configuration (existing brownfield style):
```
{reference}
```

Generated Patch:
```
{generated}
```

Evaluate ONLY the following style aspects:
1. Naming conventions (CRITICAL): Do route-map/prefix-list names follow the same pattern (e.g., uppercase vs lowercase, underscores vs hyphens)?
2. Formatting: Indentation and spacing consistency.
3. Overall Vibe: Does it look like it belongs in the same network?

SCORING GUIDELINES:
- If Naming Conventions match, the score MUST be above 0.8.
- Do NOT penalize for being more verbose or chemically correct than the reference.
- Do NOT penalize for standard formatting if the reference is messy.
- Focus on "Intent of Style" rather than character-for-character matching.

Return a JSON object with:
- score: float between 0.0 (different) and 1.0 (identical/compatible style)
- explanation: brief explanation of the score

Response (JSON only):"""


def call_qwen_style_judge(
    c_old: str,
    patch: str,
    api_key: Optional[str] = None,
    model: str = "qwen-long-latest",  # Updated to latest model
) -> Tuple[float, str]:
    """
    Use Qwen via DashScope to evaluate style similarity.
    
    Centralized LLM-as-a-Judge function for style evaluation.
    The prompt instructs the LLM to:
    - Focus ONLY on style (naming, formatting)
    - Ignore logical correctness
    - Return a score 0.0-1.0
    
    Args:
        c_old: Brownfield configuration (reference style)
        patch: Generated patch content
        api_key: DashScope API key (optional, uses hardcoded default if not provided)
        model: Qwen model to use (default: qwen-max)
        
    Returns:
        Tuple of (score, explanation)
    """
    # Handle empty inputs
    if not c_old or not c_old.strip():
        return (1.0, "Empty reference config - no style to compare")
    
    if not patch or not patch.strip():
        return (1.0, "Empty patch - no style violations possible")
    
    # Get or create driver with specified API key
    if api_key:
        driver = QwenDriver(api_key=api_key, model=model)
    else:
        driver = get_qwen_driver()
    
    # Check if dashscope is available
    if not driver.is_available:
        logger.warning("dashscope not installed - returning default score")
        return (0.5, "dashscope library not installed")
    
    try:
        # Build prompt
        prompt = STYLE_JUDGE_PROMPT.format(
            reference=c_old[:2000],  # Truncate to avoid token limits
            generated=patch[:2000],
        )
        
        # Call the API
        response_text = driver.chat_completion(prompt)
        
        # Parse response
        return _parse_style_judge_response(response_text)
        
    except QwenDriverError as e:
        logger.error(f"Qwen style judge error: {e}")
        return (0.5, f"API error: {str(e)}")
    except Exception as e:
        logger.error(f"LLM style judge error: {e}")
        return (0.5, f"Error: {str(e)}")


def _parse_style_judge_response(response_text: str) -> Tuple[float, str]:
    """
    Parse the LLM response to extract score and explanation.
    
    Args:
        response_text: Raw response text from the LLM
        
    Returns:
        Tuple of (score, explanation)
    """
    try:
        # Try to parse as JSON
        # Handle potential markdown code blocks
        text = response_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        
        data = json.loads(text)
        score = float(data.get("score", 0.5))
        explanation = str(data.get("explanation", "No explanation provided"))
        
        # Clamp score to valid range
        score = max(0.0, min(1.0, score))
        
        return (score, explanation)
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.warning(f"Failed to parse LLM response: {e}")
        # Try to extract score from text
        score_match = re.search(r'score["\s:]+([0-9.]+)', response_text, re.IGNORECASE)
        if score_match:
            try:
                score = float(score_match.group(1))
                score = max(0.0, min(1.0, score))
                return (score, f"Extracted from malformed response: {response_text[:100]}")
            except ValueError:
                pass
        
        return (0.5, f"Failed to parse response: {response_text[:100]}")
