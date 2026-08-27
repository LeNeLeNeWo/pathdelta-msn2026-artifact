"""Instrumented OpenAI-compatible DeepSeek client for reproducible v8 runs."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence


@dataclass
class TokenUsage:
    prompt: int = 0
    completion: int = 0
    total: int = 0


@dataclass
class LLMCallRecord:
    logical_call_index: int
    backend_attempts: int
    retries: int
    request_id: Optional[str]
    configured_model: str
    response_model: Optional[str]
    finish_reason: Optional[str]
    usage: TokenUsage
    latency_ms: float
    errors: List[str] = field(default_factory=list)


@dataclass
class LLMMetrics:
    provider: str
    backend: str
    base_url: str
    configured_model: str
    thinking_mode: str = "disabled"
    logical_llm_calls: int = 0
    backend_attempts: int = 0
    retry_count: int = 0
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: float = 0.0
    calls: List[LLMCallRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class InstrumentedDeepSeekClient:
    def __init__(
        self,
        *,
        timeout_s: int = 120,
        max_retries: int = 2,
        thinking_mode: str = "disabled",
    ) -> None:
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.base_url = os.getenv("DEEPSEEK_BASE_URL", "").strip().rstrip("/")
        self.model = os.getenv("DEEPSEEK_MODEL", "").strip()
        missing = [
            name
            for name, value in (
                ("DEEPSEEK_API_KEY", self.api_key),
                ("DEEPSEEK_BASE_URL", self.base_url),
                ("DEEPSEEK_MODEL", self.model),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"missing required environment variables: {', '.join(missing)}")
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        if thinking_mode not in {"enabled", "disabled"}:
            raise ValueError("thinking_mode must be 'enabled' or 'disabled'")
        self.thinking_mode = thinking_mode
        self.metrics = LLMMetrics(
            provider="deepseek",
            backend="openai_compatible_chat_completions",
            base_url=self.base_url,
            configured_model=self.model,
            thinking_mode=self.thinking_mode,
        )

    def _endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float,
        max_completion_tokens: int,
    ) -> str:
        """Perform one logical model call, possibly with backend retries.

        `logical_llm_calls` is incremented once; transport attempts and retries
        are separately recorded. Token usage counts only successful backend
        responses because failed HTTP attempts expose no authoritative usage.
        """

        self.metrics.logical_llm_calls += 1
        logical_index = self.metrics.logical_llm_calls
        payload = json.dumps(
            {
                "model": self.model,
                "messages": list(messages),
                "temperature": temperature,
                "max_tokens": max_completion_tokens,
                "thinking": {"type": self.thinking_mode},
                "response_format": {"type": "json_object"},
            }
        ).encode("utf-8")
        errors: List[str] = []
        logical_started = time.perf_counter()
        for attempt in range(self.max_retries + 1):
            self.metrics.backend_attempts += 1
            request = urllib.request.Request(
                self._endpoint(),
                data=payload,
                method="POST",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    parsed = json.loads(response.read().decode("utf-8"))
                usage_raw = parsed.get("usage") or {}
                usage = TokenUsage(
                    int(usage_raw.get("prompt_tokens") or 0),
                    int(usage_raw.get("completion_tokens") or 0),
                    int(usage_raw.get("total_tokens") or 0),
                )
                if usage.total == 0:
                    usage.total = usage.prompt + usage.completion
                elapsed = (time.perf_counter() - logical_started) * 1000
                choice = (parsed.get("choices") or [{}])[0]
                record = LLMCallRecord(
                    logical_call_index=logical_index,
                    backend_attempts=attempt + 1,
                    retries=attempt,
                    request_id=parsed.get("id"),
                    configured_model=self.model,
                    response_model=parsed.get("model"),
                    finish_reason=choice.get("finish_reason"),
                    usage=usage,
                    latency_ms=elapsed,
                    errors=errors,
                )
                self.metrics.calls.append(record)
                self.metrics.retry_count += attempt
                self.metrics.token_usage.prompt += usage.prompt
                self.metrics.token_usage.completion += usage.completion
                self.metrics.token_usage.total += usage.total
                self.metrics.latency_ms += elapsed
                return choice["message"]["content"]
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, KeyError, ValueError) as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                if attempt >= self.max_retries:
                    elapsed = (time.perf_counter() - logical_started) * 1000
                    self.metrics.retry_count += attempt
                    self.metrics.latency_ms += elapsed
                    self.metrics.calls.append(
                        LLMCallRecord(
                            logical_call_index=logical_index,
                            backend_attempts=attempt + 1,
                            retries=attempt,
                            request_id=None,
                            configured_model=self.model,
                            response_model=None,
                            finish_reason=None,
                            usage=TokenUsage(),
                            latency_ms=elapsed,
                            errors=errors,
                        )
                    )
                    raise RuntimeError(f"DeepSeek logical call {logical_index} failed: {errors[-1]}") from exc
                time.sleep(1.0 * (2**attempt))
        raise AssertionError("unreachable")
