"""
Batfish-based static semantic validation for PathDelta.

This module adds a light-weight static validation stage between patch rendering
and heavy Kathara execution.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


SUPPORTED_CONFIG_EXTENSIONS = {".cfg", ".conf", ".frr", ".txt", ""}


def _normalize_column_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)

    text = _as_text(value).strip()
    if not text:
        return None

    try:
        return int(float(text))
    except ValueError:
        return None


def _mkdir_clean(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _normalize_frr_for_batfish(config_text: str) -> str:
    """
    Normalize FRR config into a Batfish-friendlier form.

    Batfish currently parses these configs as Cisco-like syntax. To maximize
    compatibility, we:
    1. drop renderer error comments
    2. deduplicate identical prefix-list lines
    3. hoist `neighbor ... route-map ... in/out` lines into the first
       `address-family ipv4 unicast` block under the first `router bgp`

    This keeps the semantic intent while presenting the import policy in a
    structure Batfish recognizes more reliably.
    """
    raw_lines = config_text.splitlines()
    filtered_lines: List[str] = []
    seen_prefix_lines: set[str] = set()
    neighbor_route_map_lines: List[str] = []

    neighbor_route_map_re = re.compile(
        r"^\s*neighbor\s+\S+\s+route-map\s+\S+\s+(?:in|out)\s*$",
        re.IGNORECASE,
    )

    for line in raw_lines:
        stripped = line.strip()
        if stripped.startswith("! ERROR rendering operation:"):
            continue

        if stripped.startswith("ip prefix-list "):
            if stripped in seen_prefix_lines:
                continue
            seen_prefix_lines.add(stripped)

        if neighbor_route_map_re.match(stripped):
            normalized = f"  {stripped}"
            if normalized not in neighbor_route_map_lines:
                neighbor_route_map_lines.append(normalized)
            # Drop standalone copies; we will reinsert them inside AF.
            continue

        filtered_lines.append(line)

    output_lines: List[str] = []
    inserted_neighbor_policies = False
    seen_first_bgp = False
    inside_first_bgp = False
    inside_ipv4_af = False

    for line in filtered_lines:
        stripped = line.strip()

        if stripped.startswith("router bgp "):
            if not seen_first_bgp:
                seen_first_bgp = True
                inside_first_bgp = True
            else:
                inside_first_bgp = False

        if inside_first_bgp and stripped.lower() == "address-family ipv4 unicast":
            inside_ipv4_af = True
            output_lines.append(line)
            continue

        if inside_first_bgp and inside_ipv4_af and stripped.lower() == "exit-address-family":
            if neighbor_route_map_lines and not inserted_neighbor_policies:
                output_lines.extend(neighbor_route_map_lines)
                inserted_neighbor_policies = True
            output_lines.append(line)
            inside_ipv4_af = False
            continue

        output_lines.append(line)

    if neighbor_route_map_lines and not inserted_neighbor_policies:
        # If we failed to find an AF block, append a fallback BGP stanza.
        output_lines.extend(["!", "router bgp 65000", " address-family ipv4 unicast"])
        output_lines.extend(neighbor_route_map_lines)
        output_lines.extend([" exit-address-family"])

    return "\n".join(output_lines).rstrip() + "\n"


def compose_effective_config_dir(
    *,
    baseline_configs: Dict[str, str],
    rendered_patches: Dict[str, str],
    output_dir: Union[str, Path],
) -> Path:
    """
    Build a directory of effective FRR configs by appending rendered patches.

    This mirrors the append-only semantics used in the current dynamic path:
    preserve the baseline config and append the generated patch below it.
    """
    output_dir = Path(output_dir)
    _mkdir_clean(output_dir)

    all_devices = sorted(set(baseline_configs) | set(rendered_patches))
    for device in all_devices:
        baseline = (baseline_configs.get(device) or "").rstrip()
        patch = (rendered_patches.get(device) or "").strip()

        if baseline and patch:
            merged = (
                f"{baseline}\n\n"
                f"! === PathDelta patch for {device} ===\n"
                f"{patch}\n"
            )
        elif patch:
            merged = patch + ("\n" if not patch.endswith("\n") else "")
        else:
            merged = baseline + ("\n" if baseline else "")

        normalized = _normalize_frr_for_batfish(merged)
        (output_dir / f"{device}.cfg").write_text(normalized, encoding="utf-8")

    return output_dir


def build_batfish_snapshot(
    *,
    config_dir: Union[str, Path],
    snapshot_root: Union[str, Path],
    snapshot_name: str = "pathdelta_snapshot",
) -> Path:
    """
    Reorganize a flat config directory into Batfish snapshot structure.

    Output:
        <snapshot_root>/<snapshot_name>/configs/*.cfg
    """
    config_dir = Path(config_dir)
    snapshot_root = Path(snapshot_root)

    if not config_dir.exists() or not config_dir.is_dir():
        raise FileNotFoundError(f"Config directory does not exist: {config_dir}")

    snapshot_dir = _mkdir_clean(snapshot_root / snapshot_name)
    configs_dir = _mkdir_clean(snapshot_dir / "configs")

    seen_output_names: set[str] = set()
    copied = 0
    for src in sorted(config_dir.iterdir()):
        if not src.is_file():
            continue

        suffix = src.suffix.lower()
        if suffix not in SUPPORTED_CONFIG_EXTENSIONS:
            continue

        normalized_name = f"{src.stem}.cfg"
        if normalized_name in seen_output_names:
            raise ValueError(
                f"Snapshot name collision after normalization: {src.name} -> {normalized_name}"
            )

        shutil.copy2(src, configs_dir / normalized_name)
        seen_output_names.add(normalized_name)
        copied += 1

    if copied == 0:
        raise ValueError(
            f"No supported config files found in {config_dir}. "
            f"Expected extensions: {sorted(SUPPORTED_CONFIG_EXTENSIONS)}"
        )

    return snapshot_dir


@dataclass
class BatfishAssertionResult:
    name: str
    passed: bool
    error_message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "error_message": self.error_message,
            "details": self.details,
        }


@dataclass
class BatfishIntentValidationSpec:
    target_node: str
    target_prefix: str
    expected_local_pref: int
    source_node: str
    destination_ip: str
    expected_path_nodes: List[str] = field(default_factory=list)


@dataclass
class BatfishValidationResult:
    passed: bool
    network_name: str
    snapshot_name: str
    snapshot_path: str
    assertions: Dict[str, BatfishAssertionResult] = field(default_factory=dict)
    logs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "network_name": self.network_name,
            "snapshot_name": self.snapshot_name,
            "snapshot_path": self.snapshot_path,
            "assertions": {k: v.to_dict() for k, v in self.assertions.items()},
            "logs": self.logs,
        }


class BatfishSemanticValidator:
    """
    Thin wrapper around pybatfish with PathDelta-specific assertions.

    We import pybatfish lazily so the rest of the repo can still be imported on
    machines where Batfish is not installed.
    """

    def __init__(
        self,
        *,
        bf_host: str = "localhost",
        network_name: str = "pathdelta-network",
        snapshot_name: str = "candidate",
    ) -> None:
        self.bf_host = bf_host
        self.network_name = network_name
        self.snapshot_name = snapshot_name
        self._bf: Any = None
        self.logs: List[str] = []

    def _log(self, message: str) -> None:
        self.logs.append(message)

    def _load_pybatfish(self) -> Tuple[Any, Any]:
        try:
            from pybatfish.client.session import Session
            from pybatfish.datamodel.flow import HeaderConstraints
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "pybatfish is not installed. Install with `pip install pybatfish pandas` "
                "and make sure a local Batfish service is running."
            ) from exc
        return Session, HeaderConstraints

    def connect(self) -> None:
        Session, _ = self._load_pybatfish()
        self._bf = Session(host=self.bf_host)
        self._bf.set_network(self.network_name)
        self._log(f"Connected to Batfish at host={self.bf_host}, network={self.network_name}")

    def init_snapshot(self, snapshot_dir: Union[str, Path], overwrite: bool = True) -> None:
        if self._bf is None:
            self.connect()

        snapshot_dir = Path(snapshot_dir)
        self._bf.init_snapshot(str(snapshot_dir), name=self.snapshot_name, overwrite=overwrite)
        self._log(f"Initialized snapshot `{self.snapshot_name}` from {snapshot_dir}")

    def _frame_from_answer(self, answer: Any, question_name: str) -> Any:
        """
        Convert a Batfish answer to a DataFrame.

        Batfish question results are usually exposed as Pandas DataFrames. We
        always call `.frame()` and parse by column name rather than column
        position, because pybatfish versions can reorder columns.
        """
        try:
            frame = answer.frame()
        except Exception as exc:
            raise RuntimeError(f"Failed to materialize DataFrame for {question_name}: {exc}") from exc

        if frame is None:
            raise RuntimeError(f"{question_name} returned no DataFrame")

        if not hasattr(frame, "columns"):
            raise RuntimeError(f"{question_name} did not return a Pandas-like DataFrame")

        return frame

    def _find_column(self, frame: Any, candidates: Sequence[str]) -> Optional[str]:
        normalized = {_normalize_column_name(str(col)): str(col) for col in frame.columns}
        for candidate in candidates:
            key = _normalize_column_name(candidate)
            if key in normalized:
                return normalized[key]
        return None

    def _rows_to_preview(self, frame: Any, limit: int = 5) -> List[Dict[str, str]]:
        preview: List[Dict[str, str]] = []
        try:
            for _, row in frame.head(limit).iterrows():
                preview.append({str(k): _as_text(v) for k, v in row.items()})
        except Exception:
            preview.append({"warning": "Failed to serialize DataFrame preview"})
        return preview

    def _extract_trace_nodes(self, trace_obj: Any) -> List[str]:
        """
        Best-effort extraction of node names from Batfish traceroute objects.

        The `Trace` column contains nested Python objects rather than a flat
        string. The exact schema may vary across versions, so we traverse
        recursively and collect any `node` / `hostname`-like fields we can find.
        """
        nodes: List[str] = []

        def visit(obj: Any) -> None:
            if obj is None:
                return
            if isinstance(obj, str):
                return
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if str(key).lower() in {"node", "hostname", "host"}:
                        text = _as_text(value).strip()
                        if text:
                            nodes.append(text)
                    else:
                        visit(value)
                return
            if isinstance(obj, (list, tuple, set)):
                for item in obj:
                    visit(item)
                return
            for attr in ("node", "hostname", "host"):
                if hasattr(obj, attr):
                    text = _as_text(getattr(obj, attr)).strip()
                    if text:
                        nodes.append(text)
            for attr in ("hops", "steps", "trace_hops", "details"):
                if hasattr(obj, attr):
                    visit(getattr(obj, attr))

        visit(trace_obj)
        return nodes

    def assert_bgp_sessions_established(self) -> BatfishAssertionResult:
        try:
            answer = self._bf.q.bgpSessionStatus().answer()
            frame = self._frame_from_answer(answer, "bgpSessionStatus")
            if frame.empty:
                return BatfishAssertionResult(
                    name="bgp_sessions_established",
                    passed=False,
                    error_message="bgpSessionStatus returned an empty DataFrame",
                    details={"preview": []},
                )

            status_col = self._find_column(
                frame,
                ["Established_Status", "Session_Status", "Status", "Configured_Status"],
            )
            node_col = self._find_column(frame, ["Node", "Hostname"])
            remote_node_col = self._find_column(frame, ["Remote_Node", "RemoteNode", "Remote_Hostname"])
            remote_ip_col = self._find_column(frame, ["Remote_IP", "RemoteIp", "Remote_Address"])

            if not status_col:
                return BatfishAssertionResult(
                    name="bgp_sessions_established",
                    passed=False,
                    error_message=(
                        "Could not find a BGP status column in bgpSessionStatus DataFrame. "
                        f"Columns were: {list(frame.columns)}"
                    ),
                    details={"preview": self._rows_to_preview(frame)},
                )

            failures: List[Dict[str, str]] = []
            for _, row in frame.iterrows():
                raw_status = row[status_col]
                status_text = _as_text(raw_status).strip()
                status_lower = status_text.lower()

                is_established = False
                if isinstance(raw_status, bool):
                    is_established = raw_status
                elif status_lower in {"established", "true"}:
                    is_established = True
                elif "established" in status_lower and "not" not in status_lower:
                    is_established = True

                if not is_established:
                    failures.append(
                        {
                            "node": _as_text(row[node_col]) if node_col else "",
                            "remote_node": _as_text(row[remote_node_col]) if remote_node_col else "",
                            "remote_ip": _as_text(row[remote_ip_col]) if remote_ip_col else "",
                            "status": status_text,
                        }
                    )

            if failures:
                return BatfishAssertionResult(
                    name="bgp_sessions_established",
                    passed=False,
                    error_message=f"Found {len(failures)} non-established BGP session(s)",
                    details={
                        "failures": failures,
                        "preview": self._rows_to_preview(frame),
                    },
                )

            return BatfishAssertionResult(
                name="bgp_sessions_established",
                passed=True,
                details={
                    "session_count": int(len(frame)),
                    "preview": self._rows_to_preview(frame),
                },
            )
        except Exception as exc:
            return BatfishAssertionResult(
                name="bgp_sessions_established",
                passed=False,
                error_message=f"BGP session assertion failed: {exc}",
            )

    def assert_bgp_local_preference(
        self,
        *,
        node: str,
        prefix: str,
        expected_local_pref: int,
    ) -> BatfishAssertionResult:
        try:
            # Some pybatfish versions do not support a `prefixes=` selector on
            # bgpRib. We query the node-level BGP RIB and then filter the
            # DataFrame rows by prefix ourselves for compatibility.
            answer = self._bf.q.bgpRib(nodes=node).answer()
            frame = self._frame_from_answer(answer, "bgpRib")
            if frame.empty:
                return BatfishAssertionResult(
                    name="bgp_local_preference",
                    passed=False,
                    error_message=f"bgpRib returned no rows for node={node}, prefix={prefix}",
                    details={"preview": []},
                )

            node_col = self._find_column(frame, ["Node", "Hostname"])
            network_col = self._find_column(frame, ["Network", "Prefix"])
            local_pref_col = self._find_column(frame, ["Local_Pref", "LocalPref", "LocalPreference"])
            status_col = self._find_column(frame, ["Status", "Route_Status", "Best", "Is_Best"])

            missing = [
                name
                for name, col in (
                    ("Node", node_col),
                    ("Network", network_col),
                    ("Local_Pref", local_pref_col),
                )
                if not col
            ]
            if missing:
                return BatfishAssertionResult(
                    name="bgp_local_preference",
                    passed=False,
                    error_message=f"Missing required BGP RIB columns: {missing}",
                    details={"columns": [str(c) for c in frame.columns]},
                )

            node_series = frame[node_col].map(lambda value: _as_text(value).lower())
            network_series = frame[network_col].map(lambda value: _as_text(value))
            filtered = frame[(node_series == node.lower()) & (network_series == prefix)]
            if filtered.empty:
                return BatfishAssertionResult(
                    name="bgp_local_preference",
                    passed=False,
                    error_message=f"No BGP RIB rows matched node={node}, prefix={prefix}",
                    details={"preview": self._rows_to_preview(frame)},
                )

            preferred = filtered
            if status_col:
                status_series = filtered[status_col].map(lambda value: _as_text(value).lower())
                best_mask = status_series.str.contains("best|active|true", regex=True)
                if best_mask.any():
                    preferred = filtered[best_mask]

            actual_local_prefs = []
            matching_rows = []
            for _, row in preferred.iterrows():
                lp = _as_int(row[local_pref_col])
                actual_local_prefs.append(lp)
                if lp == expected_local_pref:
                    matching_rows.append({str(k): _as_text(v) for k, v in row.items()})

            if matching_rows:
                return BatfishAssertionResult(
                    name="bgp_local_preference",
                    passed=True,
                    details={
                        "node": node,
                        "prefix": prefix,
                        "expected_local_pref": expected_local_pref,
                        "actual_local_prefs": actual_local_prefs,
                        "matched_rows": matching_rows,
                    },
                )

            return BatfishAssertionResult(
                name="bgp_local_preference",
                passed=False,
                error_message=(
                    f"No matching BGP RIB row had Local_Pref={expected_local_pref} "
                    f"for node={node}, prefix={prefix}"
                ),
                details={
                    "actual_local_prefs": actual_local_prefs,
                    "preview": self._rows_to_preview(preferred),
                },
            )
        except Exception as exc:
            return BatfishAssertionResult(
                name="bgp_local_preference",
                passed=False,
                error_message=f"Local-preference assertion failed: {exc}",
            )

    def assert_traceroute_passes_through(
        self,
        *,
        source_node: str,
        destination_ip: str,
        expected_path_nodes: Sequence[str],
    ) -> BatfishAssertionResult:
        _, HeaderConstraints = self._load_pybatfish()

        try:
            answer = self._bf.q.traceroute(
                startLocation=source_node,
                headers=HeaderConstraints(dstIps=destination_ip),
            ).answer()
            frame = self._frame_from_answer(answer, "traceroute")
            if frame.empty:
                return BatfishAssertionResult(
                    name="traceroute_path_membership",
                    passed=False,
                    error_message=f"traceroute returned no traces for source={source_node}, dst={destination_ip}",
                    details={"preview": []},
                )

            trace_col = self._find_column(frame, ["Trace", "Traces", "Forward_Traces"])
            if not trace_col:
                return BatfishAssertionResult(
                    name="traceroute_path_membership",
                    passed=False,
                    error_message=f"Could not find trace column in traceroute DataFrame: {list(frame.columns)}",
                    details={"preview": self._rows_to_preview(frame)},
                )

            expected_lower = [node.lower() for node in expected_path_nodes]
            extracted_paths: List[List[str]] = []
            matched = False

            for _, row in frame.iterrows():
                trace_obj = row[trace_col]
                hop_nodes = self._extract_trace_nodes(trace_obj)
                trace_text = _as_text(trace_obj).lower()
                extracted_paths.append(hop_nodes)

                if hop_nodes:
                    lower_hops = [hop.lower() for hop in hop_nodes]
                    if all(expected in lower_hops for expected in expected_lower):
                        matched = True
                        break
                else:
                    if all(expected in trace_text for expected in expected_lower):
                        matched = True
                        break

            if matched:
                return BatfishAssertionResult(
                    name="traceroute_path_membership",
                    passed=True,
                    details={
                        "source_node": source_node,
                        "destination_ip": destination_ip,
                        "expected_path_nodes": list(expected_path_nodes),
                        "extracted_paths": extracted_paths,
                    },
                )

            return BatfishAssertionResult(
                name="traceroute_path_membership",
                passed=False,
                error_message=(
                    f"No computed traceroute from {source_node} to {destination_ip} "
                    f"passed through all expected nodes: {list(expected_path_nodes)}"
                ),
                details={
                    "extracted_paths": extracted_paths,
                    "preview": self._rows_to_preview(frame),
                },
            )
        except Exception as exc:
            return BatfishAssertionResult(
                name="traceroute_path_membership",
                passed=False,
                error_message=f"Traceroute assertion failed: {exc}",
            )

    def validate_snapshot(
        self,
        *,
        snapshot_dir: Union[str, Path],
        spec: BatfishIntentValidationSpec,
        overwrite_snapshot: bool = True,
    ) -> BatfishValidationResult:
        self.connect()
        self.init_snapshot(snapshot_dir, overwrite=overwrite_snapshot)

        assertions = {
            "bgp_sessions_established": self.assert_bgp_sessions_established(),
            "bgp_local_preference": self.assert_bgp_local_preference(
                node=spec.target_node,
                prefix=spec.target_prefix,
                expected_local_pref=spec.expected_local_pref,
            ),
            "traceroute_path_membership": self.assert_traceroute_passes_through(
                source_node=spec.source_node,
                destination_ip=spec.destination_ip,
                expected_path_nodes=spec.expected_path_nodes,
            ),
        }

        passed = all(result.passed for result in assertions.values())
        return BatfishValidationResult(
            passed=passed,
            network_name=self.network_name,
            snapshot_name=self.snapshot_name,
            snapshot_path=str(Path(snapshot_dir)),
            assertions=assertions,
            logs=list(self.logs),
        )


def validate_rendered_patch_with_batfish(
    *,
    baseline_configs: Dict[str, str],
    rendered_patches: Dict[str, str],
    spec: BatfishIntentValidationSpec,
    artifact_root: Union[str, Path],
    bf_host: str = "localhost",
    network_name: str = "pathdelta-network",
    snapshot_name: str = "candidate",
) -> BatfishValidationResult:
    """
    Convenience wrapper for the main PathDelta pipeline.

    Typical placement:
        render patch -> guard -> syntax check -> Batfish semantic check -> Kathara
    """
    artifact_root = Path(artifact_root)
    effective_dir = compose_effective_config_dir(
        baseline_configs=baseline_configs,
        rendered_patches=rendered_patches,
        output_dir=artifact_root / "effective_configs",
    )
    snapshot_dir = build_batfish_snapshot(
        config_dir=effective_dir,
        snapshot_root=artifact_root / "batfish_snapshot",
        snapshot_name=snapshot_name,
    )

    validator = BatfishSemanticValidator(
        bf_host=bf_host,
        network_name=network_name,
        snapshot_name=snapshot_name,
    )
    return validator.validate_snapshot(snapshot_dir=snapshot_dir, spec=spec)
