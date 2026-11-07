"\nRuntime module exception hierarchy.\n\nThis module defines a \
comprehensive exception hierarchy for the runtime module,\nproviding \
clear, specific exceptions for different error scenarios in \
both\nsupervisor and tools runtime components.\n\nException \
Hierarchy:\n    RuntimeError (base)\n    ├── ConfigurationError\n    │  \
 ├── InvalidConfigurationError\n    │   └── MissingConfigurationError\n \
   ├── ExecutionError\n    │   ├── AgentExecutionError\n    │   ├── \
TimeoutError\n    │   └── StreamingError\n    ├── TransformationError\n \
   │   ├── DataMappingError\n    │   └── ValidationError\n    └── \
ToolCreationError\n        ├── RAGToolError\n        └── MCPToolError\n \
           ├── MCPAuthenticationError\n            └── \
MCPConnectionError\n\nUsage:\n    from app.runtime.core.exceptions \
import RAGToolError\n    \n    try:\n        tool = await \
create_rag_tool(url, collection)\n    except RAGToolError as e:\n       \
 logger.error(\"Failed to create RAG tool\", error=str(e), \
exc_info=True)\n        raise\n"

from typing import Any, Dict


class RuntimeError(Exception):
    (
        '\n'
        '    Base exception for all runtime module errors.\n'
        '    \n'
        '    This is the root exception class for the runtime module. All '
        'other\n'
        '    runtime exceptions should inherit from this class to allow for\n'
        '    broad exception handling when needed.\n'
        '    \n'
        '    Attributes:\n'
        '        message: Human-readable error message\n'
        '        context: Additional context information about the error\n'
        '    \n'
        '    Usage:\n'
        '        raise RuntimeError("Something went wrong in runtime")\n'
        '    '
    )

    def __init__(self, message: str, **context: Any) -> None:
        (
            '\n'
            '        Initialize runtime error with message and optional '
            'context.\n'
            '        \n'
            '        Args:\n'
            '            message: Human-readable error message\n'
            '            **context: Additional context fields (e.g., '
            'agent_id, config_key)\n'
            '        '
        )
        self.message = message
        self.context = context
        super().__init__(message)

    def __str__(self) -> str:
        """Return string representation with context."""
        if self.context:
            context_str = ", ".join(
                f"{k}={v}" for k, v in self.context.items()
            )
            return f"{self.message} ({context_str})"
        return self.message

    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for logging/serialization."""
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "context": self.context,
        }


# ============================================================================
# Configuration Errors
# ============================================================================


class ConfigurationError(RuntimeError):
    (
        '\n'
        '    Exception raised for configuration-related errors.\n'
        '    \n'
        '    This exception should be raised when there are issues with '
        'configuration\n'
        '    loading, validation, or usage. It indicates that the runtime '
        'cannot\n'
        '    proceed due to invalid or missing configuration.\n'
        '    \n'
        '    Usage:\n'
        '        raise ConfigurationError(\n'
        '            "Invalid model configuration",\n'
        '            model_name=config.model_name,\n'
        '            expected_format="provider:model"\n'
        '        )\n'
        '    '
    )

    pass


class InvalidConfigurationError(ConfigurationError):
    (
        '\n'
        '    Exception raised when configuration values are invalid.\n'
        '    \n'
        '    This exception should be raised when configuration values exist '
        'but\n'
        '    are invalid (e.g., wrong type, out of range, invalid format).\n'
        '    \n'
        '    Usage:\n'
        '        raise InvalidConfigurationError(\n'
        '            "Temperature must be between 0 and 2",\n'
        '            temperature=config.temperature,\n'
        '            valid_range="0-2"\n'
        '        )\n'
        '    '
    )

    pass


class MissingConfigurationError(ConfigurationError):
    (
        '\n'
        '    Exception raised when required configuration is missing.\n'
        '    \n'
        '    This exception should be raised when required configuration '
        'values\n'
        "    are not provided or are None when they shouldn't be.\n"
        '    \n'
        '    Usage:\n'
        '        raise MissingConfigurationError(\n'
        '            "Model name is required",\n'
        '            config_key="model_name"\n'
        '        )\n'
        '    '
    )

    pass


# ============================================================================
# Execution Errors
# ============================================================================


class ExecutionError(RuntimeError):
    (
        '\n'
        '    Exception raised for agent execution errors.\n'
        '    \n'
        '    This exception should be raised when there are errors during '
        'agent\n'
        '    execution, including failures in the execution pipeline, agent '
        'errors,\n'
        '    or coordination failures.\n'
        '    \n'
        '    Usage:\n'
        '        raise ExecutionError(\n'
        '            "Agent execution failed",\n'
        '            agent_id=agent.id,\n'
        '            agent_name=agent.name,\n'
        '            error=str(original_error)\n'
        '        )\n'
        '    '
    )

    pass


class AgentExecutionError(ExecutionError):
    (
        '\n'
        '    Exception raised when a specific agent fails to execute.\n'
        '    \n'
        '    This exception should be raised when an individual agent '
        'encounters\n'
        '    an error during execution. It provides context about which '
        'agent\n'
        '    failed and why.\n'
        '    \n'
        '    Usage:\n'
        '        raise AgentExecutionError(\n'
        '            "Agent failed to process request",\n'
        '            agent_id=agent.id,\n'
        '            agent_name=agent.name,\n'
        '            execution_id=execution_id,\n'
        '            error_details=error_message\n'
        '        )\n'
        '    '
    )

    pass


class TimeoutError(ExecutionError):
    (
        '\n'
        '    Exception raised when agent execution times out.\n'
        '    \n'
        '    This exception should be raised when an agent or workflow '
        'execution\n'
        '    exceeds the configured timeout period.\n'
        '    \n'
        '    Usage:\n'
        '        raise TimeoutError(\n'
        '            "Agent execution timed out",\n'
        '            agent_id=agent.id,\n'
        '            timeout_seconds=timeout,\n'
        '            elapsed_seconds=elapsed\n'
        '        )\n'
        '    '
    )

    pass


class StreamingError(ExecutionError):
    (
        '\n'
        '    Exception raised for streaming execution errors.\n'
        '    \n'
        '    This exception should be raised when there are errors specific '
        'to\n'
        '    streaming execution, such as connection issues, stream '
        'interruption,\n'
        '    or event emission failures.\n'
        '    \n'
        '    Usage:\n'
        '        raise StreamingError(\n'
        '            "Stream connection lost",\n'
        '            agent_id=agent.id,\n'
        '            event_type=event.type,\n'
        '            error=str(original_error)\n'
        '        )\n'
        '    '
    )

    pass


# ============================================================================
# Transformation Errors
# ============================================================================


class TransformationError(RuntimeError):
    (
        '\n'
        '    Exception raised for data transformation errors.\n'
        '    \n'
        '    This exception should be raised when there are errors '
        'transforming\n'
        '    data between different formats or models (e.g., DB model to '
        'internal\n'
        '    model, internal model to API response).\n'
        '    \n'
        '    Usage:\n'
        '        raise TransformationError(\n'
        '            "Failed to transform agent data",\n'
        '            source_type="DBAgent",\n'
        '            target_type="InternalAgent",\n'
        '            error=str(original_error)\n'
        '        )\n'
        '    '
    )

    pass


class DataMappingError(TransformationError):
    (
        '\n'
        '    Exception raised when data mapping fails.\n'
        '    \n'
        '    This exception should be raised when there are errors mapping '
        'fields\n'
        '    between different data structures, such as missing required '
        'fields\n'
        '    or incompatible types.\n'
        '    \n'
        '    Usage:\n'
        '        raise DataMappingError(\n'
        '            "Required field missing in source data",\n'
        '            source_model="DBAgent",\n'
        '            target_model="InternalAgent",\n'
        '            missing_field="model_name"\n'
        '        )\n'
        '    '
    )

    pass


class ValidationError(TransformationError):
    (
        '\n'
        '    Exception raised when data validation fails.\n'
        '    \n'
        '    This exception should be raised when data fails validation '
        'checks\n'
        '    during transformation or processing.\n'
        '    \n'
        '    Usage:\n'
        '        raise ValidationError(\n'
        '            "Invalid agent configuration",\n'
        '            field="temperature",\n'
        '            value=config.temperature,\n'
        '            constraint="must be between 0 and 2"\n'
        '        )\n'
        '    '
    )

    pass


# ============================================================================
# Tool Creation Errors
# ============================================================================


class ToolCreationError(RuntimeError):
    (
        '\n'
        '    Exception raised for tool creation errors.\n'
        '    \n'
        '    This exception should be raised when there are errors creating '
        'or\n'
        '    initializing tools for agents (RAG tools, MCP tools, etc.).\n'
        '    \n'
        '    Usage:\n'
        '        raise ToolCreationError(\n'
        '            "Failed to create tool",\n'
        '            tool_type="RAG",\n'
        '            tool_name=tool_name,\n'
        '            error=str(original_error)\n'
        '        )\n'
        '    '
    )

    pass


class RAGToolError(ToolCreationError):
    (
        '\n'
        '    Exception raised for RAG tool creation/operation errors.\n'
        '    \n'
        '    This exception should be raised when there are errors specific '
        'to\n'
        '    RAG (Retrieval-Augmented Generation) tools, such as connection\n'
        '    failures, collection not found, or query errors.\n'
        '    \n'
        '    Usage:\n'
        '        raise RAGToolError(\n'
        '            "Failed to create RAG tool for collection",\n'
        '            collection=collection_name,\n'
        '            rag_url=rag_url,\n'
        '            error=str(original_error)\n'
        '        )\n'
        '    '
    )

    pass


class MCPToolError(ToolCreationError):
    (
        '\n'
        '    Exception raised for MCP tool creation/operation errors.\n'
        '    \n'
        '    This exception should be raised when there are errors specific '
        'to\n'
        '    MCP (Model Context Protocol) tools, such as connection '
        'failures,\n'
        '    authentication errors, or tool fetching errors.\n'
        '    \n'
        '    Usage:\n'
        '        raise MCPToolError(\n'
        '            "Failed to create MCP tool",\n'
        '            mcp_url=mcp_url,\n'
        '            tool_name=tool_name,\n'
        '            error=str(original_error)\n'
        '        )\n'
        '    '
    )

    pass


class MCPAuthenticationError(MCPToolError):
    (
        '\n'
        '    Exception raised for MCP authentication errors.\n'
        '    \n'
        '    This exception should be raised when MCP authentication fails,\n'
        '    such as invalid credentials, expired tokens, or missing auth.\n'
        '    \n'
        '    Usage:\n'
        '        raise MCPAuthenticationError(\n'
        '            "MCP authentication failed",\n'
        '            mcp_url=mcp_url,\n'
        '            auth_method="bearer_token",\n'
        '            error=str(original_error)\n'
        '        )\n'
        '    '
    )

    pass


class MCPConnectionError(MCPToolError):
    (
        '\n'
        '    Exception raised for MCP connection errors.\n'
        '    \n'
        '    This exception should be raised when there are network or '
        'connection\n'
        '    issues with the MCP service, such as timeouts, unreachable '
        'host,\n'
        '    or connection refused.\n'
        '    \n'
        '    Usage:\n'
        '        raise MCPConnectionError(\n'
        '            "Failed to connect to MCP service",\n'
        '            mcp_url=mcp_url,\n'
        '            timeout_seconds=timeout,\n'
        '            error=str(original_error)\n'
        '        )\n'
        '    '
    )

    pass


# ============================================================================
# Utility Functions
# ============================================================================


def wrap_exception(
    original_error: Exception,
    runtime_error_class: type[RuntimeError],
    message: str,
    **context: Any,
) -> RuntimeError:
    (
        '\n'
        '    Wrap an original exception in a runtime exception with context.\n'
        '    \n'
        '    This utility function helps convert generic exceptions into '
        'specific\n'
        '    runtime exceptions while preserving the original error '
        'information.\n'
        '    \n'
        '    Args:\n'
        '        original_error: The original exception that was caught\n'
        '        runtime_error_class: The runtime exception class to wrap '
        'with\n'
        '        message: Human-readable error message\n'
        '        **context: Additional context fields\n'
        '    \n'
        '    Returns:\n'
        '        RuntimeError: The wrapped exception with context\n'
        '    \n'
        '    Usage:\n'
        '        try:\n'
        '            result = await some_operation()\n'
        '        except Exception as e:\n'
        '            raise wrap_exception(\n'
        '                e,\n'
        '                RAGToolError,\n'
        '                "Failed to create RAG tool",\n'
        '                collection=collection_name,\n'
        '                rag_url=rag_url\n'
        '            ) from e\n'
        '    '
    )
    context["original_error"] = str(original_error)
    context["original_error_type"] = type(original_error).__name__
    return runtime_error_class(message, **context)
