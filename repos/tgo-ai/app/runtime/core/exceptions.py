"""
Runtime module exception hierarchy.

This module defines a comprehensive exception hierarchy for the runtime module,
providing clear, specific exceptions for different error scenarios in both
supervisor and tools runtime components.

Exception Hierarchy:
    RuntimeError (base)
    ├── ConfigurationError
    │   ├── InvalidConfigurationError
    │   └── MissingConfigurationError
    ├── ExecutionError
    │   ├── AgentExecutionError
    │   ├── TimeoutError
    │   └── StreamingError
    ├── TransformationError
    │   ├── DataMappingError
    │   └── ValidationError
    └── ToolCreationError
        ├── RAGToolError
        └── MCPToolError
            ├── MCPAuthenticationError
            └── MCPConnectionError

Usage:
    from app.runtime.core.exceptions import RAGToolError
    
    try:
        tool = await create_rag_tool(url, collection)
    except RAGToolError as e:
        logger.error("Failed to create RAG tool", error=str(e), exc_info=True)
        raise
"""

from typing import Any, Dict


class RuntimeError(Exception):
    """
    Base exception for all runtime module errors.
    
    This is the root exception class for the runtime module. All other
    runtime exceptions should inherit from this class to allow for
    broad exception handling when needed.
    
    Attributes:
        message: Human-readable error message
        context: Additional context information about the error
    
    Usage:
        raise RuntimeError("Something went wrong in runtime")
    """
    
    def __init__(self, message: str, **context: Any) -> None:
        """
        Initialize runtime error with message and optional context.
        
        Args:
            message: Human-readable error message
            **context: Additional context fields (e.g., agent_id, config_key)
        """
        self.message = message
        self.context = context
        super().__init__(message)
    
    def __str__(self) -> str:
        """Return string representation with context."""
        if self.context:
            context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{self.message} ({context_str})"
        return self.message
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for logging/serialization."""
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "context": self.context
        }


# ============================================================================
# Configuration Errors
# ============================================================================

class ConfigurationError(RuntimeError):
    """
    Exception raised for configuration-related errors.
    
    This exception should be raised when there are issues with configuration
    loading, validation, or usage. It indicates that the runtime cannot
    proceed due to invalid or missing configuration.
    
    Usage:
        raise ConfigurationError(
            "Invalid model configuration",
            model_name=config.model_name,
            expected_format="provider:model"
        )
    """
    pass


class InvalidConfigurationError(ConfigurationError):
    """
    Exception raised when configuration values are invalid.
    
    This exception should be raised when configuration values exist but
    are invalid (e.g., wrong type, out of range, invalid format).
    
    Usage:
        raise InvalidConfigurationError(
            "Temperature must be between 0 and 2",
            temperature=config.temperature,
            valid_range="0-2"
        )
    """
    pass


class MissingConfigurationError(ConfigurationError):
    """
    Exception raised when required configuration is missing.
    
    This exception should be raised when required configuration values
    are not provided or are None when they shouldn't be.
    
    Usage:
        raise MissingConfigurationError(
            "Model name is required",
            config_key="model_name"
        )
    """
    pass


# ============================================================================
# Execution Errors
# ============================================================================

class ExecutionError(RuntimeError):
    """
    Exception raised for agent execution errors.
    
    This exception should be raised when there are errors during agent
    execution, including failures in the execution pipeline, agent errors,
    or coordination failures.
    
    Usage:
        raise ExecutionError(
            "Agent execution failed",
            agent_id=agent.id,
            agent_name=agent.name,
            error=str(original_error)
        )
    """
    pass


class AgentExecutionError(ExecutionError):
    """
    Exception raised when a specific agent fails to execute.
    
    This exception should be raised when an individual agent encounters
    an error during execution. It provides context about which agent
    failed and why.
    
    Usage:
        raise AgentExecutionError(
            "Agent failed to process request",
            agent_id=agent.id,
            agent_name=agent.name,
            execution_id=execution_id,
            error_details=error_message
        )
    """
    pass


class TimeoutError(ExecutionError):
    """
    Exception raised when agent execution times out.
    
    This exception should be raised when an agent or workflow execution
    exceeds the configured timeout period.
    
    Usage:
        raise TimeoutError(
            "Agent execution timed out",
            agent_id=agent.id,
            timeout_seconds=timeout,
            elapsed_seconds=elapsed
        )
    """
    pass


class StreamingError(ExecutionError):
    """
    Exception raised for streaming execution errors.
    
    This exception should be raised when there are errors specific to
    streaming execution, such as connection issues, stream interruption,
    or event emission failures.
    
    Usage:
        raise StreamingError(
            "Stream connection lost",
            agent_id=agent.id,
            event_type=event.type,
            error=str(original_error)
        )
    """
    pass


# ============================================================================
# Transformation Errors
# ============================================================================

class TransformationError(RuntimeError):
    """
    Exception raised for data transformation errors.
    
    This exception should be raised when there are errors transforming
    data between different formats or models (e.g., DB model to internal
    model, internal model to API response).
    
    Usage:
        raise TransformationError(
            "Failed to transform agent data",
            source_type="DBAgent",
            target_type="InternalAgent",
            error=str(original_error)
        )
    """
    pass


class DataMappingError(TransformationError):
    """
    Exception raised when data mapping fails.
    
    This exception should be raised when there are errors mapping fields
    between different data structures, such as missing required fields
    or incompatible types.
    
    Usage:
        raise DataMappingError(
            "Required field missing in source data",
            source_model="DBAgent",
            target_model="InternalAgent",
            missing_field="model_name"
        )
    """
    pass


class ValidationError(TransformationError):
    """
    Exception raised when data validation fails.
    
    This exception should be raised when data fails validation checks
    during transformation or processing.
    
    Usage:
        raise ValidationError(
            "Invalid agent configuration",
            field="temperature",
            value=config.temperature,
            constraint="must be between 0 and 2"
        )
    """
    pass


# ============================================================================
# Tool Creation Errors
# ============================================================================

class ToolCreationError(RuntimeError):
    """
    Exception raised for tool creation errors.
    
    This exception should be raised when there are errors creating or
    initializing tools for agents (RAG tools, MCP tools, etc.).
    
    Usage:
        raise ToolCreationError(
            "Failed to create tool",
            tool_type="RAG",
            tool_name=tool_name,
            error=str(original_error)
        )
    """
    pass


class RAGToolError(ToolCreationError):
    """
    Exception raised for RAG tool creation/operation errors.
    
    This exception should be raised when there are errors specific to
    RAG (Retrieval-Augmented Generation) tools, such as connection
    failures, collection not found, or query errors.
    
    Usage:
        raise RAGToolError(
            "Failed to create RAG tool for collection",
            collection=collection_name,
            rag_url=rag_url,
            error=str(original_error)
        )
    """
    pass


class MCPToolError(ToolCreationError):
    """
    Exception raised for MCP tool creation/operation errors.
    
    This exception should be raised when there are errors specific to
    MCP (Model Context Protocol) tools, such as connection failures,
    authentication errors, or tool fetching errors.
    
    Usage:
        raise MCPToolError(
            "Failed to create MCP tool",
            mcp_url=mcp_url,
            tool_name=tool_name,
            error=str(original_error)
        )
    """
    pass


class MCPAuthenticationError(MCPToolError):
    """
    Exception raised for MCP authentication errors.
    
    This exception should be raised when MCP authentication fails,
    such as invalid credentials, expired tokens, or missing auth.
    
    Usage:
        raise MCPAuthenticationError(
            "MCP authentication failed",
            mcp_url=mcp_url,
            auth_method="bearer_token",
            error=str(original_error)
        )
    """
    pass


class MCPConnectionError(MCPToolError):
    """
    Exception raised for MCP connection errors.
    
    This exception should be raised when there are network or connection
    issues with the MCP service, such as timeouts, unreachable host,
    or connection refused.
    
    Usage:
        raise MCPConnectionError(
            "Failed to connect to MCP service",
            mcp_url=mcp_url,
            timeout_seconds=timeout,
            error=str(original_error)
        )
    """
    pass


# ============================================================================
# Utility Functions
# ============================================================================

def wrap_exception(
    original_error: Exception,
    runtime_error_class: type[RuntimeError],
    message: str,
    **context: Any
) -> RuntimeError:
    """
    Wrap an original exception in a runtime exception with context.
    
    This utility function helps convert generic exceptions into specific
    runtime exceptions while preserving the original error information.
    
    Args:
        original_error: The original exception that was caught
        runtime_error_class: The runtime exception class to wrap with
        message: Human-readable error message
        **context: Additional context fields
    
    Returns:
        RuntimeError: The wrapped exception with context
    
    Usage:
        try:
            result = await some_operation()
        except Exception as e:
            raise wrap_exception(
                e,
                RAGToolError,
                "Failed to create RAG tool",
                collection=collection_name,
                rag_url=rag_url
            ) from e
    """
    context["original_error"] = str(original_error)
    context["original_error_type"] = type(original_error).__name__
    return runtime_error_class(message, **context)
