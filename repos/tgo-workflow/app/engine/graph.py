from typing import List, Dict, Any, Optional
from collections import deque


class WorkflowGraph:
    def __init__(
        self, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]
    ):
        self.nodes = {n["id"]: n for n in nodes}
        self.edges = edges
        self.adj: Dict[str, List[str]] = {n["id"]: [] for n in nodes}
        self.rev_adj: Dict[str, List[str]] = {n["id"]: [] for n in nodes}
        self.out_edges: Dict[str, List[Dict[str, Any]]] = {
            n["id"]: [] for n in nodes
        }

        for edge in edges:
            u, v = edge["source"], edge["target"]
            if u in self.adj and v in self.adj:
                self.adj[u].append(v)
                self.rev_adj[v].append(u)
                self.out_edges[u].append(edge)

    def get_topo_sort(self) -> List[str]:
        """
        Get topological sort of node IDs
        """
        in_degree = {
            n_id: len(parents) for n_id, parents in self.rev_adj.items()
        }
        queue = deque(
            [node_id for node_id, degree in in_degree.items() if degree == 0]
        )
        result = []

        while queue:
            curr = queue.popleft()
            result.append(curr)
            for neighbor in self.adj[curr]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return result

    def get_next_nodes(
        self, node_id: str, handle_id: Optional[str] = None
    ) -> List[str]:
        """
        Get next nodes after a node execution, optionally filtered by handle
        (for condition/classifier)
        """
        if handle_id:
            return [
                edge["target"]
                for edge in self.out_edges[node_id]
                if edge.get("sourceHandle") == handle_id
            ]
        return self.adj.get(node_id, [])

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        return self.nodes.get(node_id)
