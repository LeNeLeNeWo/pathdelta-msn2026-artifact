"""A small but genuine tool-using network configuration agent.

The model owns exploration and the concrete text edit.  The runtime exposes
read-only inspection tools plus one submit operation; acceptance is controlled
by the intent-relative ChangeEnvelope verifier.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from experiments.msn2026_v8_day2_agent.change_envelope import (
    CandidateVerdict,
    ChangeEnvelope,
    Day2Intent,
    SearchReplaceEdit,
    derive_change_envelope,
    evaluate_candidate,
    infer_style,
    parse_config,
)


SYSTEM_PROMPT = """You are a network operations agent performing one brownfield Day-2 change.
You must explore the existing network with tools and then directly edit the existing text configuration.
Return exactly one JSON object per turn, with no markdown.

Available actions:
1. {"action":"list_devices"}
2. {"action":"grep_config","device":"...","query":"..."}
3. {"action":"show_dependencies","device":"..."}
4. {"action":"inspect_style","device":"..."}
5. {"action":"submit_patch","edits":[{"device":"...","old_text":"exact unique text","new_text":"replacement text"}]}

The submitted edits are always applied to the original baseline, not to a prior rejected candidate.
Use exact unique search text. Preserve all behavior outside the requested intent. Be especially careful with
shared route-maps and prefix-lists. Prefer a local, convention-preserving patch over modifying shared objects.
Do not ask the user questions. Continue until the verifier accepts the patch or the step budget ends.
"""


@dataclass
class ApiMetrics:
    provider: str
    base_url: str
    model: str
    llm_calls: int = 0
    api_retry_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    backend_responses: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentRun:
    status: str
    accepted_candidate: Optional[Dict[str, Any]]
    steps: int
    tool_calls: Dict[str, int]
    patch_submissions: int
    agent_revision_count: int
    api_metrics: ApiMetrics
    transcript: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "accepted_candidate": self.accepted_candidate,
            "steps": self.steps,
            "tool_calls": self.tool_calls,
            "patch_submissions": self.patch_submissions,
            "agent_revision_count": self.agent_revision_count,
            "api_metrics": self.api_metrics.to_dict(),
            "transcript": self.transcript,
        }


class DeepSeekChatClient:
    def __init__(self, timeout_s: int = 90, max_retries: int = 2) -> None:
        self.api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        self.base_url = os.environ.get("DEEPSEEK_BASE_URL", "").strip().rstrip("/")
        self.model = os.environ.get("DEEPSEEK_MODEL", "").strip()
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
        self.metrics = ApiMetrics("deepseek_openai_compatible", self.base_url, self.model)

    def _endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return self.base_url + "/chat/completions"
        return self.base_url + "/chat/completions"

    def chat(self, messages: Sequence[Mapping[str, str]]) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": list(messages),
                "temperature": 0,
                "response_format": {"type": "json_object"},
            }
        ).encode("utf-8")
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            self.metrics.llm_calls += 1
            request = urllib.request.Request(
                self._endpoint(),
                data=payload,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            started = time.perf_counter()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    raw = response.read().decode("utf-8")
                self.metrics.latency_ms += (time.perf_counter() - started) * 1000
                parsed = json.loads(raw)
                usage = parsed.get("usage") or {}
                prompt = int(usage.get("prompt_tokens") or 0)
                completion = int(usage.get("completion_tokens") or 0)
                total = int(usage.get("total_tokens") or prompt + completion)
                self.metrics.prompt_tokens += prompt
                self.metrics.completion_tokens += completion
                self.metrics.total_tokens += total
                self.metrics.backend_responses.append(
                    {
                        "id": parsed.get("id"),
                        "model": parsed.get("model"),
                        "finish_reason": ((parsed.get("choices") or [{}])[0]).get("finish_reason"),
                        "usage": {"prompt": prompt, "completion": completion, "total": total},
                    }
                )
                return parsed["choices"][0]["message"]["content"]
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, KeyError, ValueError) as exc:
                self.metrics.latency_ms += (time.perf_counter() - started) * 1000
                last_error = exc
                if attempt >= self.max_retries:
                    break
                self.metrics.api_retry_count += 1
                time.sleep(1.5 * (2**attempt))
        raise RuntimeError(f"DeepSeek request failed after {self.max_retries + 1} attempts: {last_error}")


def _parse_action(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("action"), str):
        raise ValueError("response must be a JSON object with string field 'action'")
    return parsed


class Day2ConfigAgent:
    def __init__(
        self,
        baseline_configs: Mapping[str, str],
        envelope: ChangeEnvelope,
        client: DeepSeekChatClient,
        max_steps: int = 10,
    ) -> None:
        self.baseline_configs = dict(baseline_configs)
        self.envelope = envelope
        self.client = client
        self.max_steps = max_steps
        self.tool_counts: Dict[str, int] = {}
        self.patch_submissions = 0
        self.agent_revision_count = 0
        self.transcript: List[Dict[str, Any]] = []

    def _count(self, action: str) -> None:
        self.tool_counts[action] = self.tool_counts.get(action, 0) + 1

    def _grep(self, device: str, query: str) -> Dict[str, Any]:
        if device not in self.baseline_configs:
            return {"error": f"unknown device {device}"}
        lines = self.baseline_configs[device].splitlines()
        hits = []
        needle = query.lower()
        for index, line in enumerate(lines):
            if needle in line.lower():
                lo, hi = max(0, index - 2), min(len(lines), index + 3)
                hits.append(
                    {
                        "line": index + 1,
                        "context": [f"{offset + 1}: {lines[offset]}" for offset in range(lo, hi)],
                    }
                )
        return {"device": device, "query": query, "hits": hits[:20], "truncated": len(hits) > 20}

    def _dependencies(self, device: str) -> Dict[str, Any]:
        if device not in self.baseline_configs:
            return {"error": f"unknown device {device}"}
        model = parse_config(self.baseline_configs[device])
        route_map_to_neighbors: Dict[str, List[str]] = {}
        for neighbor, route_map in model.neighbor_bindings.items():
            route_map_to_neighbors.setdefault(route_map, []).append(neighbor)
        route_map_to_prefix_lists = {
            name: sorted(
                {
                    prefix_list
                    for clause in clauses
                    for prefix_list in clause.match_prefix_lists
                }
            )
            for name, clauses in model.route_maps.items()
        }
        return {
            "neighbor_bindings": model.neighbor_bindings,
            "route_map_to_neighbors": route_map_to_neighbors,
            "route_map_to_prefix_lists": route_map_to_prefix_lists,
            "shared_route_maps": sorted(
                name for name, neighbors in route_map_to_neighbors.items() if len(neighbors) > 1
            ),
        }

    def _style(self, device: str) -> Dict[str, Any]:
        if device not in self.baseline_configs:
            return {"error": f"unknown device {device}"}
        text = self.baseline_configs[device]
        return asdict(infer_style(text, parse_config(text)))

    def _execute(self, action: Dict[str, Any]) -> Dict[str, Any]:
        name = action["action"]
        self._count(name)
        if name == "list_devices":
            return {"devices": sorted(self.baseline_configs)}
        if name == "grep_config":
            return self._grep(str(action.get("device", "")), str(action.get("query", "")))
        if name == "show_dependencies":
            return self._dependencies(str(action.get("device", "")))
        if name == "inspect_style":
            return self._style(str(action.get("device", "")))
        if name == "submit_patch":
            self.patch_submissions += 1
            edits = [
                SearchReplaceEdit(
                    device=str(item["device"]),
                    old_text=str(item["old_text"]),
                    new_text=str(item["new_text"]),
                )
                for item in action.get("edits", [])
            ]
            verdict = evaluate_candidate(
                f"agent_submission_{self.patch_submissions}",
                self.baseline_configs,
                edits,
                self.envelope,
            )
            if not verdict.accepted:
                self.agent_revision_count += 1
            return {"verdict": verdict.to_dict(), "accepted": verdict.accepted, "submitted_edits": [asdict(e) for e in edits]}
        return {"error": f"unknown action {name!r}"}

    def run(self) -> AgentRun:
        intent = asdict(self.envelope.intent)
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "Implement this Day-2 change on the running brownfield network.",
                        "intent": intent,
                        "known_devices": sorted(self.baseline_configs),
                        "instruction": "Choose inspection actions as needed, then submit a direct text patch.",
                    },
                    sort_keys=True,
                ),
            },
        ]
        for step in range(1, self.max_steps + 1):
            try:
                raw = self.client.chat(messages)
                action = _parse_action(raw)
            except Exception as exc:
                self.transcript.append({"step": step, "type": "model_error", "error": str(exc)})
                messages.append(
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"tool_result": {"error": str(exc)}, "instruction": "Return one valid JSON action."}
                        ),
                    }
                )
                continue
            result = self._execute(action)
            self.transcript.append({"step": step, "action": action, "result": result})
            if action["action"] == "submit_patch" and result.get("accepted"):
                return AgentRun(
                    "accepted",
                    result,
                    step,
                    dict(self.tool_counts),
                    self.patch_submissions,
                    self.agent_revision_count,
                    self.client.metrics,
                    self.transcript,
                )
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "tool_result": result,
                            "instruction": "Continue. If rejected, use the reasons to revise the full baseline-relative patch.",
                        },
                        sort_keys=True,
                    ),
                }
            )
        return AgentRun(
            "step_budget_exhausted",
            None,
            self.max_steps,
            dict(self.tool_counts),
            self.patch_submissions,
            self.agent_revision_count,
            self.client.metrics,
            self.transcript,
        )


def run_from_dataset(data_root: Path, output_path: Path, max_steps: int = 10) -> AgentRun:
    scenario_root = data_root / "scenario_shared_policy"
    scenario = json.loads((scenario_root / "scenario.json").read_text(encoding="utf-8"))
    baseline = (scenario_root / "configs" / "edge-1.conf").read_text(encoding="utf-8")
    intent = Day2Intent(**scenario["intent"])
    configs = {intent.target_device: baseline}
    envelope = derive_change_envelope(configs, intent, scenario["probe_prefixes"])
    agent = Day2ConfigAgent(configs, envelope, DeepSeekChatClient(), max_steps=max_steps)
    result = agent.run()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result
