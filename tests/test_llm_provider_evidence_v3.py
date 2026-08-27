from types import SimpleNamespace

import common.llm_driver as module


class _Completions:
    def create(self, **kwargs):
        usage = SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10,
                                prompt_tokens_details=None, completion_tokens_details=None)
        message = SimpleNamespace(content='{"ok":true}')
        choice = SimpleNamespace(message=message, finish_reason="stop")
        response = SimpleNamespace(id="req-test-1", model="deepseek-test", usage=usage, choices=[choice])
        response.model_dump = lambda mode="json": {"id": response.id, "model": response.model,
            "choices": [{"finish_reason": "stop", "message": {"content": message.content}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}}
        return response


def test_provider_evidence_contains_raw_id_and_finish_reason(monkeypatch):
    driver = module.DeepSeekDriver(api_key="x", base_url="https://unit.invalid/v1", model="deepseek-test")
    driver._client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    module.set_intent_deadline(10); module.set_max_llm_calls(1)
    try:
        assert driver.chat_completion("system", "user", json_mode=True) == '{"ok":true}'
        usage = module.get_intent_llm_usage()
    finally:
        module.clear_max_llm_calls(); module.clear_intent_deadline()
    call = usage["provider_calls"][0]
    assert call["request_id"] == "req-test-1"
    assert call["finish_reason"] == "stop"
    assert call["raw_response"]["usage"]["total_tokens"] == 10
    assert usage["backend"]["model"] == "deepseek-test"
