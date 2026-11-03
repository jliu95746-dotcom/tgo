"""Internal execution cursor for resuming a fixed workflow without replay."""

from pydantic import BaseModel, Field, JsonValue


class ExecutionCheckpoint(BaseModel):
    """Persist only with the owning run and its immutable definition snapshot."""

    project_id: str | None
    execution_id: str | None
    definition_digest: str
    input_digest: str
    workflow_lineage: list[str] = Field(default_factory=list)
    context_data: dict[str, JsonValue]
    current_nodes: list[str]
    next_nodes: list[str]
    completed_nodes: list[str]
    node_index: int = Field(ge=1)
    final_output: JsonValue = None


class WorkflowPaused(Exception):
    """The caller persisted a cursor and deliberately stopped this invocation."""

    def __init__(self, checkpoint: ExecutionCheckpoint):
        super().__init__("工作流已暂停，等待确认后从检查点恢复。")
        self.checkpoint = checkpoint
