"""Executable, read-only fork topology shared by validation and dispatch."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import cast


_REFERENCE = re.compile(r"\{\{\s*([A-Za-z_][\w-]*)\.[^{}]+\}\}")
_READ_ONLY_BRANCHES = frozenset({"api", "llm"})


@dataclass(frozen=True)
class ParallelPlan:
    fork_id: str
    branch_paths: tuple[tuple[str, ...], ...]
    join_id: str
    timeout_seconds: int

    @property
    def branch_ids(self) -> tuple[str, ...]:
        return tuple(node_id for path in self.branch_paths for node_id in path)


def _referenced_names(value: object) -> set[str]:
    if isinstance(value, str):
        return set(_REFERENCE.findall(value))
    if isinstance(value, Mapping):
        return set().union(
            *(_referenced_names(item) for item in value.values())
        )
    if isinstance(value, list):
        return set().union(*(_referenced_names(item) for item in value))
    return set()


def parallel_plan_for(
    fork_id: str,
    nodes: Sequence[Mapping[str, object]],
    edges: Sequence[Mapping[str, object]],
) -> ParallelPlan:
    """Require isolated read-only chains and one shared answer."""
    by_id: dict[str, Mapping[str, object]] = {
        cast(str, node["id"]): node
        for node in nodes
        if isinstance(node.get("id"), str)
    }
    fork = by_id.get(fork_id)
    if not isinstance(fork, Mapping) or fork.get("type") != "parallel":
        raise ValueError("并行节点不存在。")
    raw_data = fork.get("data")
    data = raw_data if isinstance(raw_data, Mapping) else {}
    count = data.get("branches", 2)
    timeout = data.get("timeout", 120)
    if type(count) is not int or count < 2 or count > 5:
        raise ValueError("并行分支数必须为 2 至 5。")
    if data.get("wait_for_all", True) is not True:
        raise ValueError("并行节点必须等待全部分支完成。")
    if type(timeout) is not int or timeout < 1 or timeout > 300:
        raise ValueError("并行节点总限时必须为 1 至 300 秒。")

    outgoing = [edge for edge in edges if edge.get("source") == fork_id]
    expected_handles = {f"branch-{index}" for index in range(count)}
    if (
        len(outgoing) != count
        or any(
            not isinstance(edge.get("sourceHandle"), str) for edge in outgoing
        )
        or {edge["sourceHandle"] for edge in outgoing} != expected_handles
    ):
        raise ValueError("并行节点的每个分支必须连接一个不同的出口。")
    by_handle = {edge["sourceHandle"]: edge.get("target") for edge in outgoing}
    raw_starts = tuple(by_handle[f"branch-{index}"] for index in range(count))
    if any(not isinstance(branch_id, str) for branch_id in raw_starts):
        raise ValueError("并行分支必须连接不同的已存在节点。")
    branch_starts = cast(tuple[str, ...], raw_starts)
    if len(set(branch_starts)) != count or any(
        branch_id not in by_id for branch_id in branch_starts
    ):
        raise ValueError("并行分支必须连接不同的已存在节点。")

    joins: set[str] = set()
    branch_references: dict[str, str] = {}
    branch_paths: list[tuple[str, ...]] = []
    visited: set[str] = set()
    branch_ends: list[str] = []
    for start in branch_starts:
        predecessor = fork_id
        current = start
        path: list[str] = []
        while True:
            if current in visited or current not in by_id:
                raise ValueError("并行分支不能交叉、复用节点或形成循环。")
            branch = by_id[current]
            if branch.get("type") not in _READ_ONLY_BRANCHES:
                raise ValueError("并行分支目前只允许只读 API 或不调用工具的模型节点。")
            incoming = [
                edge for edge in edges if edge.get("target") == current
            ]
            outgoing = [
                edge for edge in edges if edge.get("source") == current
            ]
            if (
                len(incoming) != 1
                or incoming[0].get("source") != predecessor
                or len(outgoing) != 1
            ):
                raise ValueError("每个并行分支必须为独立的只读顺序路径。")
            branch_data = branch.get("data")
            if not isinstance(branch_data, Mapping):
                raise ValueError("并行分支配置无效。")
            reference = branch_data.get("reference_key")
            if not isinstance(reference, str) or not reference:
                raise ValueError("并行分支缺少结果变量名。")
            branch_references[current] = reference
            visited.add(current)
            path.append(current)
            target = outgoing[0].get("target")
            if not isinstance(target, str) or target not in by_id:
                raise ValueError("并行分支的下一个节点不存在。")
            if by_id[target].get("type") == "answer":
                joins.add(target)
                branch_ends.append(current)
                break
            predecessor, current = current, target
        branch_paths.append(tuple(path))

    if len(joins) != 1:
        raise ValueError("并行分支必须汇合到同一个回答节点。")
    join_id = joins.pop()
    join = by_id.get(join_id)
    incoming_join = [
        edge.get("source") for edge in edges if edge.get("target") == join_id
    ]
    if (
        not isinstance(join, Mapping)
        or join.get("type") != "answer"
        or len(incoming_join) != count
        or any(not isinstance(source, str) for source in incoming_join)
        or set(incoming_join) != set(branch_ends)
    ):
        raise ValueError("并行分支必须汇合到专用回答节点。")

    for branch_path in branch_paths:
        other_references = {
            branch_references[node_id]
            for other_path in branch_paths
            if other_path != branch_path
            for node_id in other_path
        }
        for position, node_id in enumerate(branch_path):
            future_references = {
                branch_references[future_id]
                for future_id in branch_path[position + 1:]
            }
            if _referenced_names(by_id[node_id].get("data")) & (
                other_references | future_references
            ):
                raise ValueError("并行分支不能读取其他分支或本分支尚未产生的结果。")
    return ParallelPlan(fork_id, tuple(branch_paths), join_id, timeout)
