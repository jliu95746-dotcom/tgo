import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from pydantic import JsonValue
import time

from app.engine.context import ExecutionContext
from app.schemas.business_query import BusinessExecutionContext
from app.engine.graph import WorkflowGraph
from app.engine.nodes.registry import get_executor_class
import app.engine.nodes  # noqa: F401 - register executors before dispatch
from app.core.logging import logger
from app.services.workflow_inputs import validate_inputs
from app.services.execution_receipts import node_receipt_output
from app.services.validation_service import ValidationService
from app.services.parallel_topology import ParallelPlan, parallel_plan_for
from app.integrations.ai_errors import WorkflowAIError
from app.services.execution_snapshot import copy_json_object, definition_digest
from app.engine.checkpoint import ExecutionCheckpoint, WorkflowPaused
from app.engine.parallel_control import await_parallel_branches


class WorkflowNodeFailure(RuntimeError):
    """An executor-generated node error that contains no raw exception text."""


class _ParallelCallbackError(Exception):
    """Keep API/worker control failures separate from node execution errors."""

    def __init__(self, cause: Exception):
        self.cause = cause
        super().__init__(type(cause).__name__)


def public_execution_error(error: Exception) -> str:
    if isinstance(error, WorkflowNodeFailure):
        return str(error)
    return f"工作流执行失败（{type(error).__name__}），请重试。"


class WorkflowExecutor:
    def __init__(
        self,
        workflow_definition: Dict[str, Any],
        project_id: Optional[str] = None,
        *,
        execution_id: str | None = None,
        workflow_lineage: tuple[str, ...] = (),
        employee_agent_id: str | None = None,
        business_context: BusinessExecutionContext | None = None,
        check_active: Callable[[], Awaitable[None]] | None = None,
    ):
        self._definition = workflow_definition
        self.graph = WorkflowGraph(
            nodes=workflow_definition.get("nodes", []),
            edges=workflow_definition.get("edges", []),
        )
        self.execution_results: dict[str, JsonValue] = {}  # node_id -> output
        self.project_id = project_id
        self.execution_id = execution_id
        self.workflow_lineage = workflow_lineage
        self.employee_agent_id = employee_agent_id
        self.business_context = business_context
        self.check_active = check_active

    async def run(
        self,
        inputs: Dict[str, Any],
        on_node_start=None,
        on_node_complete=None,
        *,
        checkpoint: ExecutionCheckpoint | None = None,
        on_checkpoint: (
            Callable[[str, str, ExecutionCheckpoint], Awaitable[bool]] | None
        ) = None,
    ) -> JsonValue:
        """
        Run the workflow with given inputs.
        on_node_start: callback(node_id, node_type, node_data, index)
        on_node_complete: callback(node_id, node_type, status, input, output,
        error, duration)
        """
        governed_query = any(
            node["type"] == "business_query"
            for node in self.graph.nodes.values()
        )
        has_parallel = any(
            node["type"] == "parallel" for node in self.graph.nodes.values()
        )
        if governed_query:
            if checkpoint is not None or on_checkpoint is not None:
                raise ValueError(
                    "受控业务查询不得保存包含原始订单号的检查点。"
                )
        if governed_query or has_parallel:
            errors = ValidationService.validate_workflow(self._definition)
            if errors:
                raise ValueError(errors[0])
        inputs = validate_inputs(self._definition, inputs)
        fixed_definition_digest = (
            definition_digest(self._definition)
            if checkpoint is not None or on_checkpoint is not None
            else None
        )
        accepted_input_digest = (
            definition_digest(inputs)
            if checkpoint is not None or on_checkpoint is not None
            else None
        )
        # 1. Initialize context
        # Find trigger nodes
        trigger_types = {"input", "timer", "webhook", "event"}
        trigger_node = next(
            (
                n
                for n in self.graph.nodes.values()
                if n["type"] in trigger_types
            ),
            None,
        )

        if not trigger_node:
            # Fallback to any node with 0 in-degree if no explicit trigger node
            # found
            in_degree = {
                n_id: len(parents)
                for n_id, parents in self.graph.rev_adj.items()
            }
            start_node_id = next(
                (
                    node_id
                    for node_id, degree in in_degree.items()
                    if degree == 0
                ),
                None,
            )
            if start_node_id:
                trigger_node = self.graph.get_node(start_node_id)

        if not trigger_node:
            raise ValueError("No entry point (trigger node) found in workflow")

        ref_key = trigger_node["data"].get(
            "reference_key", trigger_node["type"]
        )
        mapped_inputs = {f"{ref_key}.{k}": v for k, v in inputs.items()}

        # Project scope must come from the trusted execution entry, not inputs.
        context = ExecutionContext(
            mapped_inputs,
            project_id=self.project_id,
            execution_id=self.execution_id,
            workflow_lineage=self.workflow_lineage,
            employee_agent_id=self.employee_agent_id,
            business_context=self.business_context,
        )

        # 2. Get execution order
        topo_order = self.graph.get_topo_sort()
        if len(topo_order) != len(self.graph.nodes):
            raise ValueError("工作流包含循环依赖，请检查节点连线。")

        # 3. Execute nodes in order
        executed_nodes: set[str] = set()
        completed_nodes: list[str] = []
        node_index = 1

        # Simple execution loop following edges
        curr_node_ids = [trigger_node["id"]]
        next_node_ids: list[str] = []

        final_output = None

        if checkpoint is not None:
            saved = ExecutionCheckpoint.model_validate(checkpoint)
            known_nodes = set(self.graph.nodes)
            positions = saved.current_nodes + saved.next_nodes
            if (
                saved.project_id != self.project_id
                or saved.execution_id != self.execution_id
                or saved.definition_digest != fixed_definition_digest
                or saved.input_digest != accepted_input_digest
                or saved.workflow_lineage != list(self.workflow_lineage)
                or len(saved.completed_nodes)
                != len(set(saved.completed_nodes))
                or not set(saved.completed_nodes).issubset(known_nodes)
                or not set(positions).issubset(known_nodes)
                or set(positions).intersection(saved.completed_nodes)
                or saved.node_index <= len(saved.completed_nodes)
            ):
                raise ValueError(
                    "工作流检查点与当前运行、输入或发布定义不一致。"
                )
            context.data = copy_json_object(saved.context_data)
            curr_node_ids = list(saved.current_nodes)
            next_node_ids = list(saved.next_nodes)
            completed_nodes = list(saved.completed_nodes)
            executed_nodes = set(completed_nodes)
            node_index = saved.node_index
            final_output = saved.final_output

        while curr_node_ids or next_node_ids:
            pending = list(
                dict.fromkeys(
                    node_id
                    for node_id in [*curr_node_ids, *next_node_ids]
                    if node_id not in executed_nodes
                )
            )
            if not pending:
                break
            if any(node_id not in self.graph.nodes for node_id in pending):
                raise ValueError("工作流节点不存在，请检查节点连线。")
            pending_set = set(pending)
            active_parent_cache: dict[str, bool] = {}

            def has_pending_ancestor(parent_id: str) -> bool:
                if parent_id in active_parent_cache:
                    return active_parent_cache[parent_id]
                frontier = [parent_id]
                visited: set[str] = set()
                while frontier:
                    upstream = frontier.pop()
                    if upstream in pending_set:
                        active_parent_cache[parent_id] = True
                        return True
                    if upstream not in visited:
                        visited.add(upstream)
                        frontier.extend(self.graph.rev_adj[upstream])
                active_parent_cache[parent_id] = False
                return False

            curr_node_ids = []
            next_node_ids = []
            for node_id in pending:
                # A skipped conditional branch is not in pending. An unfinished
                # branch remains reachable from a pending node and must settle
                # before a shared downstream node can read its output.
                unfinished_parent = any(
                    parent not in executed_nodes
                    and has_pending_ancestor(parent)
                    for parent in self.graph.rev_adj[node_id]
                )
                if unfinished_parent:
                    next_node_ids.append(node_id)
                else:
                    curr_node_ids.append(node_id)
            if not curr_node_ids:
                raise ValueError("工作流汇合节点等待未完成的上游路径。")
            for position, node_id in enumerate(curr_node_ids):
                if node_id in executed_nodes:
                    continue

                node = self.graph.get_node(node_id)
                if node is None:
                    raise ValueError("工作流节点不存在，请检查节点连线。")

                if node["type"] == "approval" and on_checkpoint is None:
                    raise RuntimeError(
                        "审批节点没有持久化检查点，禁止继续执行。"
                    )

                if on_node_start:
                    await on_node_start(
                        node_id=node_id,
                        node_type=node["type"],
                        node_data=node.get("data", {}),
                        index=node_index,
                    )
                node_index += 1

                executor_cls = get_executor_class(node["type"])

                start_time = time.time()
                status = "completed"
                error = None
                outputs = {}
                next_handle = None
                parallel_plan: ParallelPlan | None = None

                try:
                    if executor_cls is None:
                        raise NotImplementedError(
                            "Node executor is unavailable"
                        )
                    executor = executor_cls(node_id, node)
                    outputs, next_handle = await executor.execute_with_timeout(
                        context
                    )
                    context.set_node_outputs(executor.reference_key, outputs)
                    if node["type"] == "parallel":
                        parallel_plan = await self._run_parallel_branches(
                            node_id,
                            context,
                            node_index,
                            on_node_start,
                            on_node_complete,
                        )

                    if node["type"] == "answer":
                        final_output = outputs.get("result")

                except _ParallelCallbackError as callback_error:
                    raise callback_error.cause from callback_error
                except Exception as e:
                    status = "failed"
                    label = node.get("data", {}).get("label") or node_id
                    if executor_cls is None:
                        error = f"节点「{label}」的类型 {node['type']} 暂不支持执行。"
                    elif isinstance(e, WorkflowAIError):
                        error = f"节点「{label}」：{e.public_message}"
                    else:
                        error = (
                            f"节点「{label}」（{node['type']}）执行失败"
                            f"（{type(e).__name__}），请检查节点配置。"
                        )
                    # Exception text may contain credentials or resolved
                    # inputs.
                    logger.error(
                        "Workflow node %s (%s) failed: %s",
                        node_id,
                        node["type"],
                        type(e).__name__,
                    )

                duration = int((time.time() - start_time) * 1000)

                if on_node_complete:
                    await on_node_complete(
                        node_id=node_id,
                        node_type=node["type"],
                        status=status,
                        # Node configuration can contain API credentials and is
                        # not the actual runtime input. Keep it out of
                        # receipts.
                        input={"capture_status": "redacted"},
                        output=node_receipt_output(
                            node["type"],
                            outputs,
                            governed_query=governed_query,
                        ),
                        error=error,
                        duration=duration,
                    )

                executed_nodes.add(node_id)
                completed_nodes.append(node_id)
                if parallel_plan is not None and status == "completed":
                    executed_nodes.update(parallel_plan.branch_ids)
                    completed_nodes.extend(parallel_plan.branch_ids)
                    node_index += len(parallel_plan.branch_ids)

                if status == "failed":
                    # All three callers already turn an exception into a failed
                    # run. Persist/emit the failed node first, then stop the
                    # run.
                    raise WorkflowNodeFailure(error) from None

                if status == "completed":
                    # Get next nodes based on handle (for branching)
                    targets = (
                        [parallel_plan.join_id]
                        if parallel_plan is not None
                        else self.graph.get_next_nodes(node_id, next_handle)
                    )
                    next_node_ids.extend(targets)
                    if on_checkpoint is not None:
                        saved = ExecutionCheckpoint(
                            project_id=self.project_id,
                            execution_id=self.execution_id,
                            definition_digest=fixed_definition_digest or "",
                            input_digest=accepted_input_digest or "",
                            workflow_lineage=list(self.workflow_lineage),
                            context_data=copy_json_object(context.data),
                            current_nodes=curr_node_ids[position + 1:],
                            next_nodes=next_node_ids,
                            completed_nodes=completed_nodes,
                            node_index=node_index,
                            final_output=final_output,
                        )
                        persisted = await on_checkpoint(
                            node_id, node["type"], saved
                        )
                        if node["type"] == "approval" and not persisted:
                            raise RuntimeError(
                                "审批节点未建立等待记录，禁止继续执行。"
                            )
                        if persisted:
                            raise WorkflowPaused(saved)

            curr_node_ids = []

        return final_output

    async def _run_parallel_branches(
        self,
        fork_id: str,
        context: ExecutionContext,
        first_index: int,
        on_node_start,
        on_node_complete,
    ) -> ParallelPlan:
        """
        Run isolated read-only branches, then merge outputs in handle order.
        """
        plan = parallel_plan_for(
            fork_id,
            list(self.graph.nodes.values()),
            self.graph.edges,
        )
        callback_lock = asyncio.Lock()

        async def run_branch(path: tuple[str, ...], first_branch_index: int):
            branch_context = ExecutionContext(
                deepcopy(context.data),
                project_id=self.project_id,
                execution_id=self.execution_id,
                workflow_lineage=self.workflow_lineage,
                employee_agent_id=self.employee_agent_id,
                business_context=self.business_context,
            )
            branch_results: list[tuple[str, dict[str, JsonValue]]] = []
            for position, branch_id in enumerate(path):
                node = self.graph.get_node(branch_id)
                if node is None:
                    raise WorkflowNodeFailure("并行分支节点不存在。")
                executor_cls = get_executor_class(node["type"])
                if on_node_start:
                    try:
                        async with callback_lock:
                            await on_node_start(
                                node_id=branch_id,
                                node_type=node["type"],
                                node_data=node.get("data", {}),
                                index=first_branch_index + position,
                            )
                    except Exception as callback_error:
                        raise _ParallelCallbackError(
                            callback_error
                        ) from callback_error
                started_at = datetime.now(timezone.utc)
                start_time = time.monotonic()
                status = "completed"
                error = None
                outputs = {}
                try:
                    if executor_cls is None:
                        raise NotImplementedError(
                            "Node executor is unavailable"
                        )
                    executor = executor_cls(branch_id, node)
                    outputs, _ = await executor.execute_with_timeout(
                        branch_context
                    )
                    branch_context.set_node_outputs(
                        executor.reference_key, outputs
                    )
                    branch_results.append((executor.reference_key, outputs))
                except asyncio.CancelledError:
                    status = "cancelled"
                    error = f"节点「{branch_id}」已取消。"
                    raise
                except Exception as exc:
                    status = "failed"
                    error = (
                        f"节点「{branch_id}」：{exc.public_message}"
                        if isinstance(exc, WorkflowAIError)
                        else (
                            f"节点「{branch_id}」（{node['type']}）执行失败"
                            f"（{type(exc).__name__}），请检查节点配置。"
                        )
                    )
                    logger.error(
                        "Parallel branch %s (%s) failed: %s",
                        branch_id,
                        node["type"],
                        type(exc).__name__,
                    )
                    raise WorkflowNodeFailure(error) from None
                finally:
                    duration_ms = int((time.monotonic() - start_time) * 1000)
                    completed_at = datetime.now(timezone.utc)
                    if on_node_complete:
                        try:
                            async with callback_lock:
                                await on_node_complete(
                                    node_id=branch_id,
                                    node_type=node["type"],
                                    status=status,
                                    input={"capture_status": "redacted"},
                                    output=node_receipt_output(
                                        node["type"],
                                        outputs,
                                        governed_query=False,
                                    ),
                                    error=error,
                                    duration=duration_ms,
                                    started_at=started_at,
                                    completed_at=completed_at,
                                )
                        except Exception as callback_error:
                            if status != "cancelled":
                                raise _ParallelCallbackError(
                                    callback_error
                                ) from callback_error
                            # A user cancellation can make the shared DB
                            # callback
                            # refuse new writes. Preserve cancellation
                            # semantics.
                            logger.warning(
                                "Cancelled branch %s receipt skipped: %s",
                                branch_id,
                                type(callback_error).__name__,
                            )
            return branch_results

        tasks = []
        offset = 0
        for path in plan.branch_paths:
            tasks.append(
                asyncio.create_task(run_branch(path, first_index + offset))
            )
            offset += len(path)

        async def check_active() -> None:
            try:
                async with callback_lock:
                    if self.check_active is not None:
                        await self.check_active()
            except Exception as callback_error:
                raise _ParallelCallbackError(
                    callback_error
                ) from callback_error

        results = await await_parallel_branches(
            tasks,
            plan.timeout_seconds,
            check_active if self.check_active is not None else None,
        )

        for branch_results in results:
            for reference_key, outputs in branch_results:
                context.set_node_outputs(reference_key, outputs)
        return plan
