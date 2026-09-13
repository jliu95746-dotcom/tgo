from typing import Any, Dict, Optional, Tuple
from app.engine.nodes.base import BaseNodeExecutor
from app.engine.context import ExecutionContext
from app.integrations.llm_provider import LLMProvider
from simpleeval import simple_eval
from app.core.logging import logger
from app.engine.nodes.registry import register_node

@register_node("condition")
class ConditionNodeExecutor(BaseNodeExecutor):
    async def execute(self, context: ExecutionContext) -> Tuple[Dict[str, Any], Optional[str]]:
        condition_type = self.config.get("condition_type", "variable")
        result = False
        
        if condition_type == "variable":
            var_val = context.get_variable(self.config.get("variable", ""))
            operator = self.config.get("operator", "equals")
            compare_val = context.resolve_variables(self.config.get("compare_value"))
            
            if operator == "equals":
                result = str(var_val) == str(compare_val)
            elif operator == "notEquals":
                result = str(var_val) != str(compare_val)
            elif operator == "contains":
                result = str(compare_val) in str(var_val)
            elif operator == "greaterThan":
                result = float(var_val) > float(compare_val)
            elif operator == "lessThan":
                result = float(var_val) < float(compare_val)
            elif operator == "isEmpty":
                result = not var_val
            elif operator == "isNotEmpty":
                result = bool(var_val)
                
        elif condition_type == "expression":
            expression = self.config.get("expression", "")
            resolved_expr = context.resolve_template(expression)
            try:
                # Use simple_eval instead of eval for safety
                result = simple_eval(resolved_expr, names=context.get_expression_context())
            except Exception as e:
                logger.error("Condition expression failed for node %s: %s",
                             self.node_id, type(e).__name__)
                raise ValueError("条件表达式无法计算，请检查变量和表达式。") from None
                
        elif condition_type == "llm":
            prompt = self.config.get("llm_prompt", "")
            model = self.config.get("model_id", "gpt-4o")
            provider = self.config.get("provider_id", "openai")
            
            # Resolve prompt with variables
            full_prompt = f"Given the context, determine if this condition is true: {prompt}. Return only 'true' or 'false'."
            response = await LLMProvider.chat_completion(
                provider_id=provider,
                model=model,
                user_prompt=context.resolve_template(full_prompt),
                project_id=context.project_id
            )
            result = "true" in response.lower()
            
        handle_id = "true" if result else "false"
        return {"result": result}, handle_id

