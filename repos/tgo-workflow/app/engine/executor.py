from typing import Any, Dict, Optional
from pydantic import JsonValue
import time

from app.engine.context import ExecutionContext
from app.engine.graph import WorkflowGraph
from app.engine.nodes.registry import get_executor_class
import app.engine.nodes # Import to trigger registration
from app.core.logging import logger

class WorkflowExecutor:
    def __init__(self, workflow_definition: Dict[str, Any], project_id: Optional[str] = None):
        self.graph = WorkflowGraph(
            nodes=workflow_definition.get("nodes", []),
            edges=workflow_definition.get("edges", [])
        )
        self.execution_results: dict[str, JsonValue] = {}  # node_id -> output
        self.project_id = project_id

    async def run(
        self, inputs: Dict[str, Any], on_node_start=None, on_node_complete=None,
    ) -> JsonValue:
        """
        Run the workflow with given inputs.
        on_node_start: callback(node_id, node_type, node_data, index)
        on_node_complete: callback(node_id, node_type, status, input, output, error, duration)
        """
        # 1. Initialize context
        # Find trigger nodes
        trigger_types = {"input", "timer", "webhook", "event"}
        trigger_node = next((n for n in self.graph.nodes.values() if n["type"] in trigger_types), None)
        
        if not trigger_node:
            # Fallback to any node with 0 in-degree if no explicit trigger node found
            in_degree = {n_id: len(parents) for n_id, parents in self.graph.rev_adj.items()}
            start_node_id = next((node_id for node_id, degree in in_degree.items() if degree == 0), None)
            if start_node_id:
                trigger_node = self.graph.get_node(start_node_id)
        
        if not trigger_node:
            raise ValueError("No entry point (trigger node) found in workflow")
            
        ref_key = trigger_node["data"].get("reference_key", trigger_node["type"])
        mapped_inputs = {f"{ref_key}.{k}": v for k, v in inputs.items()}
        
        # Use provided project_id or extract from inputs
        project_id = self.project_id or inputs.get("project_id")
        context = ExecutionContext(mapped_inputs, project_id=project_id)
        
        # 2. Get execution order
        topo_order = self.graph.get_topo_sort()
        if len(topo_order) != len(self.graph.nodes):
            raise ValueError("工作流包含循环依赖，请检查节点连线。")
            
        # 3. Execute nodes in order
        executed_nodes = set()
        node_index = 1
        
        # Simple execution loop following edges
        curr_node_ids = [n["id"] for n in self.graph.nodes.values() if n["id"] == trigger_node["id"]]
        
        final_output = None
        
        while curr_node_ids:
            next_node_ids = []
            for node_id in curr_node_ids:
                if node_id in executed_nodes:
                    continue
                    
                node = self.graph.get_node(node_id)
                if node is None:
                    raise ValueError("工作流节点不存在，请检查节点连线。")

                if on_node_start:
                    await on_node_start(
                        node_id=node_id,
                        node_type=node["type"],
                        node_data=node.get("data", {}),
                        index=node_index
                    )
                node_index += 1

                executor_cls = get_executor_class(node["type"])
                
                start_time = time.time()
                status = "completed"
                error = None
                outputs = {}
                next_handle = None
                
                try:
                    if executor_cls is None:
                        raise NotImplementedError("Node executor is unavailable")
                    executor = executor_cls(node_id, node)
                    outputs, next_handle = await executor.execute_with_timeout(context)
                    context.set_node_outputs(executor.reference_key, outputs)
                    
                    if node["type"] == "answer":
                        final_output = outputs.get("result")
                        
                except Exception as e:
                    status = "failed"
                    label = node.get("data", {}).get("label") or node_id
                    if executor_cls is None:
                        error = f"节点「{label}」的类型 {node['type']} 暂不支持执行。"
                    else:
                        error = (
                            f"节点「{label}」（{node['type']}）执行失败"
                            f"（{type(e).__name__}），请检查节点配置。"
                        )
                    # Exception text may contain credentials or resolved inputs.
                    logger.error("Workflow node %s (%s) failed: %s",
                                 node_id, node["type"], type(e).__name__)
                
                duration = int((time.time() - start_time) * 1000)
                
                if on_node_complete:
                    await on_node_complete(
                        node_id=node_id,
                        node_type=node["type"],
                        status=status,
                        input=node["data"], # Simplified
                        output=outputs,
                        error=error,
                        duration=duration
                    )
                
                executed_nodes.add(node_id)

                if status == "failed":
                    # All three callers already turn an exception into a failed
                    # run. Persist/emit the failed node first, then stop the run.
                    raise RuntimeError(error) from None
                
                if status == "completed":
                    # Get next nodes based on handle (for branching)
                    targets = self.graph.get_next_nodes(node_id, next_handle)
                    next_node_ids.extend(targets)
            
            curr_node_ids = list(set(next_node_ids)) # De-duplicate
            
        return final_output

