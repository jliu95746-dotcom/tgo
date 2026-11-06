from typing import Any, Dict, Optional, Tuple
from app.engine.nodes.base import BaseNodeExecutor
from app.engine.context import ExecutionContext
from app.integrations.llm_provider import LLMProvider
from app.engine.nodes.registry import register_node


@register_node("llm")
class LLMNodeExecutor(BaseNodeExecutor):
    async def execute(
        self, context: ExecutionContext
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        if self.config.get("tool_ids"):
            raise ValueError("LLM 节点目前不能直接调用工具；请使用已核验的只读工具节点。")
        user_prompt = context.resolve_template(
            self.config.get("user_prompt", "")
        )
        system_prompt = context.resolve_template(
            self.config.get("system_prompt", "")
        )

        provider = self.config.get("provider_id", "openai")
        model = self.config.get("model_id", "gpt-4o")

        tool_ids = self.config.get("tool_ids", [])
        collection_ids = self.config.get("collection_ids", [])
        max_tool_rounds = self.config.get("max_tool_rounds", 5)

        response = await LLMProvider.chat_completion(
            provider_id=provider,
            model=model,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=self.config.get("temperature", 0.7),
            max_tokens=self.config.get("max_tokens", 2000),
            project_id=context.project_id,
            tool_ids=tool_ids,
            collection_ids=collection_ids,
            max_tool_rounds=max_tool_rounds,
        )

        return {"text": response}, None
