"""
Configuration settings for coordination system v2.

This module provides comprehensive configuration management with
validation, defaults, and environment variable support.
"""

from dataclasses import dataclass, field
import os


@dataclass
class QueryAnalysisConfig:
    """Configuration for LLM-powered query analysis."""
    
    model_name: str = "anthropic:claude-3-sonnet-20240229"
    temperature: float = 0.2
    max_tokens: int = 2000
    timeout: int = 30
    max_retries: int = 3
    retry_delay: float = 1.0
    
    # Prompt configuration
    system_prompt: str = "You are an expert AI coordination system. Always respond with valid JSON only."
    prompt_template: str = "unified_coordination"
    
    # Validation settings
    validate_response: bool = True
    require_all_fields: bool = True
    confidence_threshold: float = 0.5
    
    def __post_init__(self) -> None:
        """Validate configuration values."""
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("Temperature must be between 0.0 and 2.0")
        if self.max_tokens < 100:
            raise ValueError("Max tokens must be at least 100")
        if self.timeout <= 0:
            raise ValueError("Timeout must be positive")
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError("Confidence threshold must be between 0.0 and 1.0")


@dataclass
class WorkflowPlanningConfig:
    """Configuration for workflow planning."""
    
    max_parallel_agents: int = 10
    max_sequential_depth: int = 5
    max_hierarchical_levels: int = 3
    default_timeout: int = 300
    
    # Optimization settings
    enable_optimization: bool = True
    prefer_parallel: bool = True
    balance_load: bool = True
    
    # Dependency resolution
    max_dependency_depth: int = 10
    detect_cycles: bool = True
    resolve_conflicts: bool = True
    
    def __post_init__(self) -> None:
        """Validate configuration values."""
        if self.max_parallel_agents <= 0:
            raise ValueError("Max parallel agents must be positive")
        if self.max_sequential_depth <= 0:
            raise ValueError("Max sequential depth must be positive")
        if self.default_timeout <= 0:
            raise ValueError("Default timeout must be positive")


@dataclass
class ExecutionConfig:
    """Configuration for workflow execution."""
    
    default_timeout: int = 300
    agent_timeout: int = 60
    max_concurrent_executions: int = 20
    
    # Retry and error handling
    max_retries: int = 2
    retry_delay: float = 2.0
    exponential_backoff: bool = True
    
    # Monitoring and logging
    enable_progress_monitoring: bool = True
    log_execution_details: bool = True
    collect_metrics: bool = True
    
    # Resource management
    memory_limit_mb: int = 1024
    cpu_limit_percent: float = 80.0
    
    def __post_init__(self) -> None:
        """Validate configuration values."""
        if self.default_timeout <= 0:
            raise ValueError("Default timeout must be positive")
        if self.agent_timeout <= 0:
            raise ValueError("Agent timeout must be positive")
        if self.max_concurrent_executions <= 0:
            raise ValueError("Max concurrent executions must be positive")
        if not 0.0 <= self.cpu_limit_percent <= 100.0:
            raise ValueError("CPU limit must be between 0.0 and 100.0")


@dataclass
class ConsolidationConfig:
    """Configuration for result consolidation."""
    
    model_name: str = "anthropic:claude-3-sonnet-20240229"
    temperature: float = 0.3
    max_tokens: int = 3000
    timeout: int = 45
    
    # Consolidation strategies
    default_strategy: str = "synthesis"
    enable_conflict_detection: bool = True
    enable_consensus_building: bool = True
    
    # Quality thresholds
    confidence_threshold: float = 0.7
    consensus_threshold: float = 0.8
    max_conflicts: int = 5
    
    # Response formatting
    max_response_length: int = 2000
    include_sources: bool = True
    include_confidence: bool = True
    
    def __post_init__(self) -> None:
        """Validate configuration values."""
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("Temperature must be between 0.0 and 2.0")
        if self.max_tokens < 100:
            raise ValueError("Max tokens must be at least 100")
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError("Confidence threshold must be between 0.0 and 1.0")
        if not 0.0 <= self.consensus_threshold <= 1.0:
            raise ValueError("Consensus threshold must be between 0.0 and 1.0")


@dataclass
class CoordinationConfig:
    """Main configuration for coordination system v2."""
    
    # Component configurations
    query_analysis: QueryAnalysisConfig = field(default_factory=QueryAnalysisConfig)
    workflow_planning: WorkflowPlanningConfig = field(default_factory=WorkflowPlanningConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    consolidation: ConsolidationConfig = field(default_factory=ConsolidationConfig)
    
    # Global settings
    enable_caching: bool = True
    cache_ttl: int = 3600
    enable_metrics: bool = True
    log_level: str = "INFO"
    
    # Performance settings
    max_concurrent_requests: int = 50
    request_timeout: int = 600
    enable_rate_limiting: bool = True
    
    def __post_init__(self) -> None:
        """Validate global configuration."""
        if self.cache_ttl <= 0:
            raise ValueError("Cache TTL must be positive")
        if self.max_concurrent_requests <= 0:
            raise ValueError("Max concurrent requests must be positive")
        if self.request_timeout <= 0:
            raise ValueError("Request timeout must be positive")
        
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if self.log_level not in valid_log_levels:
            raise ValueError(f"Log level must be one of: {valid_log_levels}")


def get_coordination_config() -> CoordinationConfig:
    """
    Get coordination configuration with environment variable overrides.
    
    Environment variables can override configuration values using the pattern:
    COORDINATION_V2_<SECTION>_<SETTING>
    
    Examples:
    - COORDINATION_V2_QUERY_ANALYSIS_MODEL_NAME
    - COORDINATION_V2_EXECUTION_DEFAULT_TIMEOUT
    - COORDINATION_V2_CONSOLIDATION_TEMPERATURE
    
    Returns:
        CoordinationConfig: Configuration with environment overrides
    """
    config = CoordinationConfig()
    
    # Override query analysis settings
    if model_name := os.getenv("COORDINATION_V2_QUERY_ANALYSIS_MODEL_NAME"):
        config.query_analysis.model_name = model_name
    if temperature := os.getenv("COORDINATION_V2_QUERY_ANALYSIS_TEMPERATURE"):
        config.query_analysis.temperature = float(temperature)
    if max_tokens := os.getenv("COORDINATION_V2_QUERY_ANALYSIS_MAX_TOKENS"):
        config.query_analysis.max_tokens = int(max_tokens)
    
    # Override execution settings
    if timeout := os.getenv("COORDINATION_V2_EXECUTION_DEFAULT_TIMEOUT"):
        config.execution.default_timeout = int(timeout)
    if max_concurrent := os.getenv("COORDINATION_V2_EXECUTION_MAX_CONCURRENT"):
        config.execution.max_concurrent_executions = int(max_concurrent)
    
    # Override consolidation settings
    if cons_model := os.getenv("COORDINATION_V2_CONSOLIDATION_MODEL_NAME"):
        config.consolidation.model_name = cons_model
    if cons_temp := os.getenv("COORDINATION_V2_CONSOLIDATION_TEMPERATURE"):
        config.consolidation.temperature = float(cons_temp)
    
    # Override global settings
    if log_level := os.getenv("COORDINATION_V2_LOG_LEVEL"):
        config.log_level = log_level.upper()
    if enable_caching := os.getenv("COORDINATION_V2_ENABLE_CACHING"):
        config.enable_caching = enable_caching.lower() == "true"
    
    return config
