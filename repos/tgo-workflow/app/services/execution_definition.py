"""Inspect execution graph structure before semantic checks or dispatch."""

from dataclasses import dataclass
from typing import TypedDict

from pydantic import JsonValue

from app.services.execution_snapshot import copy_json_object


class ExecutionNode(TypedDict):
    id: str
    type: str
    data: dict[str, JsonValue]


class _EdgeMetadata(TypedDict, total=False):
    id: str
    sourceHandle: str | None
    targetHandle: str | None


class ExecutionEdge(_EdgeMetadata):
    source: str
    target: str


@dataclass(frozen=True)
class ExecutionGraph:
    nodes: list[ExecutionNode]
    edges: list[ExecutionEdge]


def execution_graph(definition: dict[str, JsonValue]) -> ExecutionGraph:
    owned = copy_json_object(definition)
    raw_nodes = owned.get("nodes", [])
    raw_edges = owned.get("edges", [])
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise ValueError("Invalid graph containers")
    nodes: list[ExecutionNode] = []
    edges: list[ExecutionEdge] = []
    for node in raw_nodes:
        if not isinstance(node, dict):
            raise ValueError("Invalid graph node")
        identifier, kind, data = (
            node.get("id"),
            node.get("type"),
            node.get("data", {}),
        )
        if (
            not isinstance(identifier, str)
            or not isinstance(kind, str)
            or not isinstance(data, dict)
        ):
            raise ValueError("Invalid node identity or configuration")
        for field in ("label", "reference_key"):
            value = data.get(field)
            if value is not None and not isinstance(value, str):
                raise ValueError("Invalid node reference")
        nodes.append({"id": identifier, "type": kind, "data": data})
    for edge in raw_edges:
        if not isinstance(edge, dict):
            raise ValueError("Invalid graph edge")
        source, target = edge.get("source"), edge.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError("Invalid edge endpoints")
        checked: ExecutionEdge = {"source": source, "target": target}
        identifier = edge.get("id")
        if identifier is not None:
            if not isinstance(identifier, str):
                raise ValueError("Invalid edge identity")
            checked["id"] = identifier
        source_handle, target_handle = edge.get("sourceHandle"), edge.get(
            "targetHandle"
        )
        if (
            source_handle is not None and not isinstance(source_handle, str)
        ) or (
            target_handle is not None and not isinstance(target_handle, str)
        ):
            raise ValueError("Invalid edge handles")
        if "sourceHandle" in edge:
            checked["sourceHandle"] = source_handle
        if "targetHandle" in edge:
            checked["targetHandle"] = target_handle
        edges.append(checked)
    return ExecutionGraph(nodes, edges)
