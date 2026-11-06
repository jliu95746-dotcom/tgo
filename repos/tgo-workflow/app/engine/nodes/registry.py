from typing import Dict, Type, Any
from app.core.logging import logger

_NODE_EXECUTOR_REGISTRY: Dict[str, Type[Any]] = {}


def register_node(node_type: str):
    """Decorator to register node executors"""

    def decorator(cls):
        _NODE_EXECUTOR_REGISTRY[node_type] = cls
        logger.info(f"Registered node executor: {node_type} -> {cls.__name__}")
        return cls

    return decorator


def get_executor_class(node_type: str):
    return _NODE_EXECUTOR_REGISTRY.get(node_type)


def get_all_executors():
    return _NODE_EXECUTOR_REGISTRY
