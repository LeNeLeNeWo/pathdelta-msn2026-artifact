"""
OSPF steering 解析器（轻量级）：基于偏好层级（tiers）和罚分（penalty）
选择少量需要上调 cost 的边，便于后续合成层生成配置。
仅依赖标准库 heapq 实现 Dijkstra，不引入第三方。
"""

from __future__ import annotations

import heapq
from typing import Dict, List, Tuple, Set, Any


def _normalize_edge(u: str, v: str) -> str:
    a, b = sorted([u, v])
    return f"{a}--{b}"


def _build_graph(topology: Dict[str, Any], base_cost: int = 10) -> Dict[str, List[Tuple[str, int]]]:
    """从 topology.yaml 的 dict 结构构造无向图，边权默认 base_cost。"""
    graph: Dict[str, List[Tuple[str, int]]] = {}

    def add_edge(a: str, b: str, w: int) -> None:
        graph.setdefault(a, []).append((b, w))
        graph.setdefault(b, []).append((a, w))

    # LAN fan-out -> 完全连接
    for lan in (topology.get("lans") or {}).values():
        members = [m.get("device") for m in lan.get("members", []) if m.get("device")]
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                add_edge(str(members[i]), str(members[j]), base_cost)

    # 显式 links
    for link in topology.get("links", []) or []:
        if isinstance(link, dict):
            endpoints = link.get("endpoints") or link.get("nodes") or []
            if len(endpoints) == 2:
                a, b = str(endpoints[0]), str(endpoints[1])
                add_edge(a, b, base_cost)
        elif isinstance(link, (list, tuple)) and len(link) == 2:
            a, b = str(link[0]), str(link[1])
            add_edge(a, b, base_cost)
    return graph


def _dijkstra(graph: Dict[str, List[Tuple[str, int]]], src: str) -> Tuple[Dict[str, int], Dict[str, str]]:
    dist: Dict[str, int] = {src: 0}
    prev: Dict[str, str] = {}
    pq: List[Tuple[int, str]] = [(0, src)]
    visited: Set[str] = set()
    while pq:
        d, u = heapq.heappop(pq)
        if u in visited:
            continue
        visited.add(u)
        for v, w in graph.get(u, []):
            nd = d + w
            if nd < dist.get(v, 10**9):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    return dist, prev


def _shortest_path(prev: Dict[str, str], src: str, dst: str) -> List[str]:
    path = []
    cur = dst
    while cur in prev:
        path.append(cur)
        cur = prev[cur]
        if cur == src:
            path.append(cur)
            break
    if path and path[-1] == src:
        return list(reversed(path))
    return []


def resolve_ospf_steering_scope(
    topology: Dict[str, Any],
    sources: List[str],
    exits: List[str],
    tiers: Dict[str, int],
    base_cost: int = 10,
    penalty: int = 20,
    budget: int = 5,
) -> Dict[str, Any]:
    """
    根据 tiers 计算需要上调 cost 的边集合，输出可序列化的结构：
    {
      "touched_edges": [ [u,v], ... ],
      "cost_overrides": { "a--b": new_cost, ... },
      "meta": { "penalty": ..., "budget": ..., "base_cost": ... },
      "sources": [...],
      "exits": [...]
    }
    """
    graph = _build_graph(topology, base_cost=base_cost)

    def all_dists():
        dists = {}
        prevs = {}
        for ex in exits:
            dist_ex, prev_ex = _dijkstra(graph, ex)
            dists[ex] = dist_ex
            prevs[ex] = prev_ex
        return dists, prevs

    dists, prevs = all_dists()

    def best_exit(v: str) -> Tuple[str, int]:
        best = None
        best_d = 10**9
        for ex in exits:
            dv = dists.get(ex, {}).get(v, 10**9)
            if dv < best_d:
                best_d = dv
                best = ex
        return best or "", best_d

    touched_edges: List[List[str]] = []
    cost_overrides: Dict[str, int] = {}
    touched_set: Set[str] = set()
    preferred_exit = max(exits, key=lambda e: tiers.get(e, 0)) if exits else None
    initial_violators = 0

    for _ in range(budget):
        violators: List[Tuple[str, str]] = []
        for s in sources:
            be, _ = best_exit(s)
            if not be:
                continue
            pref_exit = preferred_exit or max(exits, key=lambda e: tiers.get(e, 0))
            if tiers.get(be, 0) < tiers.get(pref_exit, 0):
                violators.append((s, be))
        if not initial_violators:
            initial_violators = len(violators)
        if not violators:
            break

        edge_score: Dict[str, int] = {}
        for s, be in violators:
            path = _shortest_path(prevs.get(be, {}), be, s)
            if len(path) < 2:
                continue
            # 取靠近 best_exit 的最后一条边
            u, v = path[0], path[1]
            key = _normalize_edge(u, v)
            edge_score[key] = edge_score.get(key, 0) + 1

        if not edge_score:
            break
        # 取得分最高的边
        target_edge = max(edge_score.items(), key=lambda x: x[1])[0]
        a, b = target_edge.split("--")
        # 读取当前权重并增加 penalty，保持输出与内部图一致
        w_current = base_cost
        for nbr, w in graph.get(a, []):
            if nbr == b:
                w_current = w
                break
        w_new = w_current + penalty
        # 更新图
        for idx, (nbr, w) in enumerate(graph.get(a, [])):
            if nbr == b:
                graph[a][idx] = (nbr, w_new)
        for idx, (nbr, w) in enumerate(graph.get(b, [])):
            if nbr == a:
                graph[b][idx] = (nbr, w_new)
        cost_overrides[target_edge] = w_new
        if target_edge not in touched_set:
            touched_edges.append([a, b])
            touched_set.add(target_edge)
        # 重算 dijkstra
        dists, prevs = all_dists()

    return {
        "touched_edges": touched_edges,
        "cost_overrides": cost_overrides,
        "meta": {
            "penalty": penalty,
            "budget": budget,
            "base_cost": base_cost,
            "preferred_exit": preferred_exit,
            "violators_count": initial_violators,
        },
        "sources": sources,
        "exits": exits,
    }
