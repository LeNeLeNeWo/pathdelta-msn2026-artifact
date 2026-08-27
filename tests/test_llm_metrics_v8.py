from dataclasses import asdict

from experiments.msn2026_v8_day2_agent.llm_client_v2 import LLMMetrics, LLMCallRecord, TokenUsage


def test_metrics_distinguish_logical_calls_attempts_and_retries():
    metrics = LLMMetrics("deepseek", "chat", "https://example/v1", "model")
    metrics.logical_llm_calls = 2
    metrics.backend_attempts = 3
    metrics.retry_count = 1
    metrics.token_usage = TokenUsage(100, 20, 120)
    payload = metrics.to_dict()
    assert payload["logical_llm_calls"] == 2
    assert payload["backend_attempts"] == 3
    assert payload["retry_count"] == 1
    assert payload["token_usage"] == {"prompt": 100, "completion": 20, "total": 120}

