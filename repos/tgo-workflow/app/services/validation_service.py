from typing import List, Dict, Any, Set
from app.services.node_readiness import node_configuration_errors
from app.services.workflow_inputs import input_configuration_errors
from collections import deque

class ValidationService:
    @staticmethod
    def validate_workflow(
        definition: Dict[str, Any], *, allow_timer_preparation: bool = False,
        allow_webhook_preparation: bool = False,
    ) -> List[str]:
        nodes = definition.get("nodes", [])
        edges = definition.get("edges", [])
        errors = node_configuration_errors(
            nodes, edges, allow_timer_preparation=allow_timer_preparation,
            allow_webhook_preparation=allow_webhook_preparation,
        )
        errors.extend(input_configuration_errors(definition))
        if len({node.get("id") for node in nodes}) != len(nodes) or any(not node.get("id") for node in nodes):
            return errors
        
        # 1. Trigger node check (formerly Start node)
        trigger_types = {"input", "timer", "webhook", "event"}
        trigger_nodes = [n for n in nodes if n.get("type") in trigger_types]
        if len(trigger_nodes) > 1:
            errors.append("Workflow must have exactly one trigger node")
        if len(trigger_nodes) == 0:
            errors.append("Workflow must have at least one trigger node (input, timer, webhook, or event)")
            
        # 2. Answer node check (formerly End node)
        answer_nodes = [n for n in nodes if n.get("type") == "answer"]
        if len(answer_nodes) == 0:
            errors.append("Workflow must have at least one answer node")
            
        if not trigger_nodes:
            return errors

        # Build adjacency list
        adj: dict[str, list[str]] = {n["id"]: [] for n in nodes}
        rev_adj: dict[str, list[str]] = {n["id"]: [] for n in nodes}
        for edge in edges:
            u, v = edge["source"], edge["target"]
            if u in adj and v in adj:
                adj[u].append(v)
                rev_adj[v].append(u)
            else:
                errors.append(f"Edge references non-existent node: {u} -> {v}")

        # 3. Reachability from trigger nodes
        visited = set()
        queue = deque([n["id"] for n in trigger_nodes])
        while queue:
            curr = queue.popleft()
            if curr not in visited:
                visited.add(curr)
                for neighbor in adj.get(curr, []):
                    queue.append(neighbor)
        
        for n in nodes:
            if n["id"] not in visited:
                errors.append(f"Node '{n.get('data', {}).get('label', n['id'])}' is not reachable from any trigger node")

        # 4. Reachability to answer nodes
        visited_to_answer = set()
        queue = deque([n["id"] for n in answer_nodes])
        while queue:
            curr = queue.popleft()
            if curr not in visited_to_answer:
                visited_to_answer.add(curr)
                for neighbor in rev_adj.get(curr, []):
                    queue.append(neighbor)
                    
        for n in nodes:
            if n["id"] not in visited_to_answer:
                errors.append(f"Node '{n.get('data', {}).get('label', n['id'])}' cannot reach any answer node")

        # 5. Circular dependency check (using Kahn's algorithm for DAG)
        in_degree = {n["id"]: 0 for n in nodes}
        for edge in edges:
            v = edge["target"]
            if v in in_degree:
                in_degree[v] += 1
        
        queue = deque([node_id for node_id, degree in in_degree.items() if degree == 0])
        topo_visited = 0
        while queue:
            curr = queue.popleft()
            topo_visited += 1
            for neighbor in adj.get(curr, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
                    
        if topo_visited < len(nodes):
            errors.append("Workflow contains circular dependencies")

        return errors

