from typing import Any, Dict, Optional, Tuple
from app.engine.nodes.base import BaseNodeExecutor
from app.engine.context import ExecutionContext
from app.integrations.llm_provider import LLMProvider
import json
from app.core.logging import logger
from app.engine.nodes.registry import register_node


@register_node("classifier")
class ClassifierNodeExecutor(BaseNodeExecutor):
    async def execute(
        self, context: ExecutionContext
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        input_text = context.resolve_template(
            self.config.get("input_variable", "")
        )
        categories = self.config.get("categories", [])

        if not categories:
            return {"category_id": None, "category_name": None}, None

        categories_desc = "\n".join(
            [
                f"- {c['id']}: {c['name']} ({c['description']})"
                for c in categories
            ]
        )
        prompt = f"""
Classify the following input into one of these categories:
{categories_desc}

Input: {input_text}

Return the category ID in JSON format: {{"category_id": "..."}}
"""

        response = await LLMProvider.chat_completion(
            provider_id=self.config.get("provider_id", "openai"),
            model=self.config.get("model_id", "gpt-4o"),
            user_prompt=prompt,
            project_id=context.project_id,
        )

        # Simple extraction logic
        try:
            # Look for JSON in response
            import re

            match = re.search(r"\{.*\}", response, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                category_id = data.get("category_id")
            else:
                # Fallback to simple matching if JSON not found
                category_id = categories[0]["id"]
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.error(
                "Error parsing classifier response: %s", type(e).__name__
            )
            category_id = categories[0]["id"]

        matched_category = next(
            (c for c in categories if c["id"] == category_id), categories[0]
        )

        outputs = {
            "category_id": matched_category["id"],
            "category_name": matched_category["name"],
        }

        return outputs, matched_category["id"]
