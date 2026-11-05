"""Optional stateless provider context; never a credential or server cache."""

from __future__ import annotations

from typing import Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    GetJsonSchemaHandler,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema


class ProviderMessageState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["anthropic", "google"]
    payload: str = Field(max_length=524288, repr=False)
    call_ids: tuple[str, ...] = Field(default=(), max_length=256)

    @field_validator("call_ids")
    @classmethod
    def unique_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(
            not item.strip() or len(item) > 512 for item in value
        ):
            raise ValueError("供应商上下文的调用编号无效")
        return value


class ProviderStateCarrier(BaseModel):
    provider_state: ProviderMessageState | None = Field(
        default=None,
        repr=False,
        description="模型返回的可选上下文；作为助手历史时需原样回传",
    )

    @model_serializer(mode="wrap")
    def omit_missing_context(
        self,
        handler: SerializerFunctionWrapHandler,
    ) -> object:
        result: object = handler(self)
        if isinstance(result, dict) and self.provider_state is None:
            result.pop("provider_state", None)
        return result

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        schema: CoreSchema,
        handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        # The conditional serializer still emits the declared model fields.
        # Preserve those fields in OpenAPI instead of an opaque object schema.
        declared = cast(
            CoreSchema,
            {
                name: value
                for name, value in schema.items()
                if name != "serialization"
            },
        )
        return handler(declared)
