"""
Configuration management for coordination system v2.

This module provides centralized configuration with validation,
environment variable support, and type safety.
"""

from .settings import (
    CoordinationConfig,
    QueryAnalysisConfig,
    WorkflowPlanningConfig,
    ExecutionConfig,
    ConsolidationConfig,
    get_coordination_config,
)

__all__ = [
    "CoordinationConfig",
    "QueryAnalysisConfig",
    "WorkflowPlanningConfig",
    "ExecutionConfig",
    "ConsolidationConfig",
    "get_coordination_config",
]
