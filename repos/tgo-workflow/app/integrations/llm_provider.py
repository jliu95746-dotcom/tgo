from typing import Optional, List
from app.core.logging import logger
from app.integrations.ai_client import AIClient


class LLMProvider:
    @staticmethod
    async def chat_completion(
        provider_id: str,
        model: str,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        project_id: Optional[str] = None,
        tool_ids: Optional[List[str]] = None,
        collection_ids: Optional[List[str]] = None,
        max_tool_rounds: int = 5,
    ) -> str:
        if tool_ids:
            raise ValueError(
                "工作流模型目前不能直接调用工具；请使用已核验的只读工具节点。"
            )
        logger.info(
            "LLM Chat Completion via AI Service:"
            f" provider_id={provider_id} model={model}"
        )

        # Use a default project_id if not provided
        active_project_id = (
            project_id or "00000000-0000-0000-0000-000000000000"
        )

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        payload = {
            "provider_id": provider_id,
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "max_tool_rounds": max_tool_rounds,
        }

        if tool_ids:
            payload["tool_ids"] = tool_ids
        if collection_ids:
            payload["collection_ids"] = collection_ids

        try:
            data = await AIClient.chat_completions(active_project_id, payload)
            # OpenAI compatible response format: choices[0].message.content
            return data["choices"][0]["message"]["content"]

        except Exception as e:
            logger.error("LLM Chat Completion failed: %s", type(e).__name__)
            raise
