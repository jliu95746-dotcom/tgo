"""Application OpenAPI generation with the existing unified error contract."""

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic import JsonValue

from app.core.config import settings
from app.schemas.base import ErrorResponse


class TGOApplication(FastAPI):
    def openapi(self) -> dict[str, JsonValue]:
        """Generate OpenAPI schema with unified error responses."""
        if self.openapi_schema:
            return self.openapi_schema

        openapi_schema = get_openapi(
            title=settings.PROJECT_NAME,
            version=settings.PROJECT_VERSION,
            description=settings.PROJECT_DESCRIPTION,
            routes=self.routes,
        )

        components = openapi_schema.setdefault("components", {}).setdefault(
            "schemas", {}
        )
        error_schema = ErrorResponse.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
        defs = error_schema.pop("$defs", {})
        for name, schema in defs.items():
            components[name] = schema
        components["ErrorResponse"] = error_schema

        # Remove FastAPI's default validation schemas
        components.pop("HTTPValidationError", None)
        components.pop("ValidationError", None)

        for path_item in openapi_schema.get("paths", {}).values():
            for operation in list(path_item.values()):
                if not isinstance(operation, dict):
                    continue
                responses = operation.setdefault("responses", {})
                for status_code, response in list(responses.items()):
                    content = response.get("content")
                    if not content:
                        continue
                    for content_type, media in list(content.items()):
                        schema = media.get("schema")
                        if not schema:
                            continue
                        ref = schema.get("$ref")
                        if ref and ref.endswith("HTTPValidationError"):
                            media["schema"] = {
                                "$ref": "#/components/schemas/ErrorResponse"
                            }
                if "422" in responses:
                    responses["422"] = {
                        "description": "Validation Error",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": (
                                        '#/components/schemas/ErrorResponse'
                                    )
                                }
                            }
                        },
                    }

        self.openapi_schema = openapi_schema
        return self.openapi_schema
