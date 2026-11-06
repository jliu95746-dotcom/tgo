from pydantic import (
    BaseModel,
    Field,
    FiniteFloat,
    JsonValue,
    StrictBool,
    model_validator,
)
from typing import List, Optional, Literal, Dict, Union, Annotated
from app.services.schedule_rules import parse_schedule


class BaseNodeData(BaseModel):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(
        ...,
        description=(
            "The unique reference key of the node, used to identify it in"
            " variable references"
        ),
    )


class InputVariable(BaseModel):
    name: str = Field(..., description="The name of the variable")
    type: Literal["string", "number", "boolean"] = Field(
        ..., description="The type of the variable"
    )
    description: Optional[str] = Field(
        None, description="The description of the variable"
    )
    required: StrictBool = Field(
        False, description="Missing legacy fields remain optional"
    )
    min_length: int | None = Field(default=None, ge=0, le=65535, strict=True)
    max_length: int | None = Field(default=None, ge=0, le=65535, strict=True)
    minimum: FiniteFloat | None = None
    maximum: FiniteFloat | None = None
    allowed_values: list[str] | None = Field(
        default=None, min_length=1, max_length=30
    )

    @model_validator(mode="after")
    def validate_constraints(self) -> "InputVariable":
        has_length = self.min_length is not None or self.max_length is not None
        has_range = self.minimum is not None or self.maximum is not None
        if (
            (has_length or self.allowed_values is not None)
            and self.type != "string"
        ) or (has_range and self.type != "number"):
            raise ValueError("Input constraints do not match variable type")
        if (
            self.min_length is not None
            and self.max_length is not None
            and self.min_length > self.max_length
        ):
            raise ValueError("Minimum text length exceeds maximum")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("Minimum number exceeds maximum")
        if self.allowed_values is not None and (
            len(set(self.allowed_values)) != len(self.allowed_values)
            or any(
                not value
                or value != value.strip()
                or len(value) > 100
                or (
                    self.min_length is not None
                    and len(value) < self.min_length
                )
                or (
                    self.max_length is not None
                    and len(value) > self.max_length
                )
                for value in self.allowed_values
            )
        ):
            raise ValueError(
                "Allowed text values must be unique, nonblank, and meet length"
                " limits"
            )
        return self


class InputNodeData(BaseNodeData):
    type: Literal["input"] = Field("input", description="Node type: input")
    input_variables: List[InputVariable] = Field(
        [], description="Input variables defined for the conversation"
    )


class TimerNodeData(BaseNodeData):
    type: Literal["timer"] = Field("timer", description="Node type: timer")
    cron_expression: str = Field(
        ...,
        min_length=1,
        max_length=512,
        strict=True,
        description="Numeric five-field schedule expression",
    )
    timezone: str = Field(
        "UTC",
        min_length=1,
        max_length=100,
        strict=True,
        description="IANA timezone; legacy definitions use UTC",
    )

    @model_validator(mode="after")
    def validate_schedule(self) -> "TimerNodeData":
        rule = parse_schedule(self.cron_expression, self.timezone)
        self.cron_expression = rule.expression
        return self


class WebhookNodeData(BaseNodeData):
    type: Literal["webhook"] = Field(
        "webhook", description="Node type: webhook"
    )
    path: Optional[str] = Field(
        None, description="Optional custom endpoint path suffix"
    )
    method: Literal["GET", "POST"] = Field(
        "POST", description="HTTP method for the webhook"
    )


class EventNodeData(BaseNodeData):
    type: Literal["event"] = Field("event", description="Node type: event")
    event_type: str = Field(
        ..., description="Internal system event type identifier"
    )


class OutputField(BaseModel):
    key: str = Field(..., description="Key name of the output field")
    value: str = Field(
        ..., description="Value or variable reference of the output field"
    )


class AnswerNodeData(BaseNodeData):
    type: Literal["answer"] = Field("answer", description="Node type: answer")
    output_type: Literal["variable", "template", "structured"] = Field(
        ..., description="Output configuration type"
    )
    output_variable: Optional[str] = Field(
        None,
        description="Selected output variable path when type is 'variable'",
    )
    output_template: Optional[str] = Field(
        None, description="Jinja2 template content when type is 'template'"
    )
    output_structure: Optional[List[OutputField]] = Field(
        None, description="Structured output fields when type is 'structured'"
    )


class LLMNodeData(BaseNodeData):
    type: Literal["llm"] = Field("llm", description="Node type: llm")
    provider_id: Optional[str] = Field(None, description="LLM provider ID")
    model_id: Optional[str] = Field(None, description="Model ID")
    model_name: Optional[str] = Field(None, description="Model display name")
    system_prompt: Optional[str] = Field(None, description="System prompt")
    user_prompt: str = Field(
        ...,
        description=(
            "User prompt, supports {{node_key.var}} variable references"
        ),
    )
    temperature: float = Field(0.7, description="Sampling temperature")
    max_tokens: int = Field(2000, description="Maximum generation tokens")
    tool_ids: List[str] = Field(
        [], description="List of tool IDs that can be called by this node"
    )
    collection_ids: List[str] = Field(
        [], description="List of associated knowledge base (collection) IDs"
    )


class AgentNodeData(BaseNodeData):
    type: Literal["agent"] = Field("agent", description="Node type: agent")
    agent_id: str = Field(..., description="Agent ID")
    agent_name: Optional[str] = Field(None, description="Agent name")
    input_mapping: Optional[Dict[str, str]] = Field(
        None, description="Input parameters mapping"
    )


class ApprovalNodeData(BaseNodeData):
    type: Literal["approval"] = Field(
        "approval", description="Human approval gate"
    )
    approver_staff_id: str = Field(..., description="Assigned staff ID")
    summary: str = Field(..., min_length=1, max_length=500)
    expires_in_minutes: int = Field(1440, ge=1, le=10080)
    after_approval_action: Optional[Literal["create_aftersales_review"]] = None
    case_category: Optional[
        Literal[
            "return_request",
            "exchange_request",
            "quality_issue",
        ]
    ] = None


class ToolNodeData(BaseNodeData):
    type: Literal["tool"] = Field("tool", description="Node type: tool")
    tool_id: str = Field(..., description="Tool ID")
    tool_name: Optional[str] = Field(None, description="Tool name")
    config: Optional[Dict[str, JsonValue]] = Field(
        None, description="Tool configuration parameters"
    )
    input_mapping: Optional[Dict[str, str]] = Field(
        None, description="Input parameters mapping"
    )


class BusinessQueryNodeData(BaseNodeData):
    type: Literal["business_query"] = "business_query"
    connection_id: str = Field(
        ..., description="Owned business connection UUID"
    )
    operation: Literal["order_query", "logistics_query"]
    order_variable: Literal["input.order_no"] = "input.order_no"


class KeyValue(BaseModel):
    key: str = Field(..., description="Key")
    value: str = Field(..., description="Value")


class FormField(BaseModel):
    key: str = Field(..., description="Field name")
    value: str = Field(..., description="Field value")
    type: Literal["text", "file"] = Field("text", description="Field type")


class APINodeData(BaseNodeData):
    type: Literal["api"] = Field("api", description="Node type: api")
    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"] = Field(
        ..., description="HTTP method"
    )
    url: str = Field(..., description="Request URL")
    headers: List[KeyValue] = Field([], description="HTTP headers")
    params: List[KeyValue] = Field([], description="Query parameters")
    body_type: Literal[
        "none", "json", "form-data", "x-www-form-urlencoded", "raw"
    ] = Field(..., description="Request body type")
    body: Optional[str] = Field(None, description="Request body content")
    form_data: List[FormField] = Field([], description="Form data")
    form_url_encoded: List[KeyValue] = Field(
        [], description="URL-encoded form data"
    )
    raw_type: Optional[Literal["text", "html", "xml", "javascript"]] = Field(
        None, description="Raw text type"
    )


class ConditionNodeData(BaseNodeData):
    type: Literal["condition"] = Field(
        "condition", description="Node type: condition"
    )
    condition_type: Literal["expression", "variable", "llm"] = Field(
        ..., description="Condition evaluation type"
    )
    expression: Optional[str] = Field(None, description="Python expression")
    variable: Optional[str] = Field(
        None, description="Referenced variable path"
    )
    operator: Optional[
        Literal[
            "equals",
            "notEquals",
            "contains",
            "greaterThan",
            "lessThan",
            "isEmpty",
            "isNotEmpty",
        ]
    ] = Field(None, description="Comparison operator")
    compare_value: Optional[str] = Field(None, description="Comparison value")
    llm_prompt: Optional[str] = Field(
        None, description="LLM evaluation prompt"
    )
    provider_id: Optional[str] = Field(None, description="LLM provider ID")
    model_id: Optional[str] = Field(None, description="Model ID")


class Category(BaseModel):
    id: str = Field(..., description="Category ID")
    name: str = Field(..., description="Category name")
    description: str = Field(..., description="Category description")


class ClassifierNodeData(BaseNodeData):
    type: Literal["classifier"] = Field(
        "classifier", description="Node type: classifier"
    )
    input_variable: str = Field(
        ..., description="Input variable path to classify"
    )
    provider_id: Optional[str] = Field(None, description="LLM provider ID")
    model_id: Optional[str] = Field(None, description="Model ID")
    categories: List[Category] = Field(
        ..., description="Defined classification rules"
    )


class ParallelNodeData(BaseNodeData):
    type: Literal["parallel"] = Field(
        "parallel", description="Node type: parallel"
    )
    branches: int = Field(..., description="Number of branches")
    wait_for_all: bool = Field(
        True, description="Whether to wait for all branches to complete"
    )
    timeout: Optional[int] = Field(
        None, description="Execution timeout in seconds"
    )


# Union for validation with discriminator for better OpenAPI docs and
# validation
WorkflowNodeData = Annotated[
    Union[
        InputNodeData,
        TimerNodeData,
        WebhookNodeData,
        EventNodeData,
        AnswerNodeData,
        LLMNodeData,
        AgentNodeData,
        ApprovalNodeData,
        ToolNodeData,
        BusinessQueryNodeData,
        APINodeData,
        ConditionNodeData,
        ClassifierNodeData,
        ParallelNodeData,
    ],
    Field(discriminator="type"),
]


class Position(BaseModel):
    x: float
    y: float


class WorkflowNode(BaseModel):
    id: str
    type: str
    position: Position
    data: WorkflowNodeData
