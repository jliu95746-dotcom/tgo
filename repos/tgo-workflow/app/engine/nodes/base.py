from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple
import asyncio
from app.engine.context import ExecutionContext


class BaseNodeExecutor(ABC):
    DEFAULT_TIMEOUT = 60  # Default 60 seconds

    def __init__(self, node_id: str, node_data: Dict[str, Any]):
        self.node_id = node_id
        self.node_data = node_data
        self.config = node_data.get("data", {})
        self.reference_key = self.config.get("reference_key")

    @abstractmethod
    async def execute(
        self, context: ExecutionContext
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        """
        Execute node logic.
        Returns: (outputs, next_handle_id)
        """
        pass

    async def execute_with_timeout(
        self, context: ExecutionContext
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        """
        Execute node logic with timeout.
        """
        timeout = self.config.get("timeout", self.DEFAULT_TIMEOUT)
        try:
            return await asyncio.wait_for(
                self.execute(context), timeout=float(timeout)
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Node {self.node_id} execution timed out after {timeout}s"
            )
