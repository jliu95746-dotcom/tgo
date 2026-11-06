import httpx
from pydantic import JsonValue
from typing import Optional, Dict, Any
from uuid import UUID
from app.config import settings
from app.core.logging import logger
from app.integrations.http_client import HttpClient
from app.schemas.agent_runtime import AgentRuntimeRequest, AgentRuntimeResult
from app.schemas.ai_usage import WorkflowAICommand
from app.integrations.ai_errors import WorkflowAIError
from app.integrations.workflow_ai_recovery import run_with_recovery
from app.schemas.tool import (
    ToolAvailability,
    ToolExecuteRequest,
    ToolExecuteResponse,
)
from app.schemas.employee_workflow import (
    EmployeeWorkflowBindingReceipt,
    EmployeeWorkflowBindingReference,
)
from app.schemas.business_query import (
    BusinessQueryCommand,
    BusinessQueryResult,
)


class AIClient:
    @staticmethod
    async def query_business(
        command: BusinessQueryCommand,
    ) -> BusinessQueryResult:
        """
        Send a customer-scoped read; never log the private request payload.
        """
        key = settings.SECRET_KEY
        if key is None or not key.get_secret_value():
            raise RuntimeError("工作流业务查询内部身份未配置")
        try:
            client = await HttpClient.get_client()
            response = await client.post(
                (
                    f"{settings.AI_SERVICE_URL}"
                    "/api/v1/agents/business-execution/workflow-query"
                ),
                json=command.private_payload(),
                headers={
                    "X-Internal-API-Key": key.get_secret_value(),
                    "X-Project-Id": str(command.project_id),
                    "X-Execution-Channel": command.context.channel,
                },
                timeout=20.0,
            )
        except (httpx.HTTPError, OSError, TimeoutError):
            raise RuntimeError("业务查询暂时不可用，请转人工处理") from None
        if response.status_code == 403:
            raise PermissionError("客户会话或员工业务查询授权已失效")
        if response.status_code != 200:
            raise RuntimeError("业务查询暂时不可用，请转人工处理")
        try:
            result = BusinessQueryResult.model_validate(response.json())
        except ValueError:
            raise RuntimeError("业务查询回执无效，请转人工处理") from None
        if (
            result.project_id != command.project_id
            or result.workflow_execution_id != command.workflow_execution_id
            or result.connection_id != command.connection_id
            or result.operation != command.operation
            or (result.outcome == "passed") != (result.summary is not None)
            or (result.outcome == "failed") != (result.error_code is not None)
        ):
            raise RuntimeError("业务查询回执范围不一致，请转人工处理")
        return result

    @staticmethod
    async def verify_tool_available(project_id: str, tool_id: str) -> None:
        """
        Confirm a tool currently belongs to the project without fetching
        secrets.
        """
        key = settings.SECRET_KEY
        if key is None or not key.get_secret_value():
            raise RuntimeError("工作流工具依赖的内部身份尚未配置")
        try:
            project_uuid, tool_uuid = UUID(project_id), UUID(tool_id)
        except ValueError:
            raise PermissionError("工作流工具或企业编号无效") from None
        try:
            client = await HttpClient.get_client()
            response = await client.get(
                (
                    f"{settings.AI_SERVICE_URL}/api/v1/tools/"
                    f"{tool_id}/availability"
                ),
                headers={
                    "X-Internal-API-Key": key.get_secret_value(),
                    "X-Project-Id": project_id,
                },
                timeout=5.0,
            )
        except (httpx.HTTPError, OSError, TimeoutError):
            raise RuntimeError("工作流工具依赖暂时无法核验") from None
        if response.status_code in (403, 404):
            raise PermissionError("工作流工具不存在或不属于当前企业")
        if response.status_code != 200:
            raise RuntimeError("工作流工具依赖暂时无法核验")
        try:
            receipt = ToolAvailability.model_validate(response.json())
        except ValueError:
            raise RuntimeError("工作流工具依赖回执无效") from None
        if (
            receipt.project_id != project_uuid
            or receipt.tool_id != tool_uuid
            or receipt.available is not True
            or receipt.workflow_read_only is not True
        ):
            raise PermissionError("工作流工具依赖已变更")

    @staticmethod
    async def verify_employee_workflow_binding(
        project_id: str,
        agent_id: str,
        workflow_id: str,
        release_version: int,
        release_id: str,
        release_digest: str,
    ) -> None:
        """
        Fail closed when AI cannot confirm the exact current employee binding.
        """
        key = settings.SECRET_KEY
        if key is None or not key.get_secret_value():
            raise RuntimeError("员工工作流内部核验未配置")
        try:
            reference = EmployeeWorkflowBindingReference(
                project_id=UUID(project_id),
                agent_id=UUID(agent_id),
                workflow_id=workflow_id,
                release_version=release_version,
                release_id=release_id,
                release_digest=release_digest,
            )
        except ValueError:
            raise PermissionError("员工工作流身份或固定发布版本无效") from None
        try:
            client = await HttpClient.get_client()
            response = await client.post(
                (
                    f"{settings.AI_SERVICE_URL}"
                    "/api/v1/agents/workflow-binding-check"
                ),
                json=reference.model_dump(mode="json"),
                headers={
                    "X-Internal-API-Key": key.get_secret_value(),
                    "X-Project-Id": project_id,
                },
                timeout=5.0,
            )
        except (httpx.HTTPError, OSError, TimeoutError):
            raise RuntimeError("员工工作流授权暂时无法核验") from None
        if response.status_code in (403, 409):
            raise PermissionError("员工工作流授权已变更")
        if response.status_code != 200:
            raise RuntimeError("员工工作流授权暂时无法核验")
        try:
            EmployeeWorkflowBindingReceipt.model_validate(response.json())
        except ValueError:
            raise RuntimeError("员工工作流授权回执无效") from None

    @staticmethod
    async def chat_completions(
        project_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Call AI service chat completions endpoint.
        """
        try:
            client = await HttpClient.get_client()
            url = f"{settings.AI_SERVICE_URL}/api/v1/chat/completions"
            params = {"project_id": project_id}

            # Internal call, no Authorization header needed
            response = await client.post(
                url, json=payload, params=params, timeout=60.0
            )

            if response.status_code != 200:
                logger.error(
                    "AI Service chat completions error: HTTP %s",
                    response.status_code,
                )
                raise Exception(
                    "AI Service chat completions failed with status"
                    f" {response.status_code}"
                )

            return response.json()
        except Exception as e:
            logger.error(
                "AI Service chat completions failed: %s", type(e).__name__
            )
            raise

    @staticmethod
    async def run_agent(
        project_id: str,
        payload: AgentRuntimeRequest,
        *,
        parent_execution_id: str | None = None,
        workflow_lineage: tuple[str, ...] = (),
        workflow_node_id: str | None = None,
    ) -> AgentRuntimeResult:
        """
        Call AI service agents run endpoint.
        """
        key = settings.SECRET_KEY
        if key is None or not key.get_secret_value():
            raise RuntimeError("工作流调用 AI 员工的内部身份尚未配置")
        try:
            command = WorkflowAICommand.model_validate({
                "project_id": project_id,
                "execution_id": parent_execution_id,
                "node_id": workflow_node_id,
                "payload": payload,
            })
        except ValueError:
            raise WorkflowAIError(403) from None
        try:
            client = await HttpClient.get_client()
        except (httpx.HTTPError, OSError, TimeoutError):
            raise WorkflowAIError(503) from None
        return await run_with_recovery(
            client,
            settings.API_INTERNAL_SERVICE_URL,
            {
                "X-Internal-API-Key": key.get_secret_value(),
                "X-Project-Id": project_id,
                "X-Execution-Channel": "internal",
            },
            command,
        )

    @staticmethod
    async def cancel_agent_run(
        project_id: str, run_id: str, payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Call AI service agents cancel endpoint.
        """
        try:
            client = await HttpClient.get_client()
            url = (
                f"{settings.AI_SERVICE_URL}/api/v1/agents/run/{run_id}/cancel"
            )
            params = {"project_id": project_id}

            # Internal call, no Authorization header needed
            response = await client.post(
                url, json=payload or {}, params=params
            )

            if response.status_code != 202:
                logger.error(
                    "AI Service Agent Cancel error: HTTP %s",
                    response.status_code,
                )
                raise Exception(
                    "AI Service Agent Cancel failed with status"
                    f" {response.status_code}"
                )

            return response.json()
        except Exception as e:
            logger.error(
                "AI Service agent cancel failed: %s", type(e).__name__
            )
            raise

    @staticmethod
    async def get_tool(project_id: str, tool_id: str) -> Dict[str, Any]:
        """
        Get tool details from AI service.
        """
        try:
            client = await HttpClient.get_client()
            url = f"{settings.AI_SERVICE_URL}/api/v1/tools/{tool_id}"
            params = {"project_id": project_id}

            response = await client.get(url, params=params)

            if response.status_code != 200:
                logger.error(
                    "AI Service Get Tool error: HTTP %s", response.status_code
                )
                raise Exception(
                    "AI Service Get Tool failed with status"
                    f" {response.status_code}"
                )

            return response.json()
        except Exception as e:
            logger.error("AI Service get tool failed: %s", type(e).__name__)
            raise

    @staticmethod
    async def execute_tool(
        project_id: str, tool_id: str, inputs: dict[str, JsonValue]
    ) -> ToolExecuteResponse:
        """
        Execute tool via AI service.
        """
        key = settings.SECRET_KEY
        if key is None or not key.get_secret_value():
            raise RuntimeError("工作流工具调用的内部身份尚未配置")
        try:
            UUID(project_id)
            UUID(tool_id)
        except ValueError:
            raise RuntimeError("工作流工具的企业或工具编号无效") from None
        request = ToolExecuteRequest(input_data=inputs)
        try:
            client = await HttpClient.get_client()
            url = f"{settings.AI_SERVICE_URL}/api/v1/tools/{tool_id}/execute"
            response = await client.post(
                url,
                json=request.model_dump(mode="json"),
                headers={
                    "X-Internal-API-Key": key.get_secret_value(),
                    "X-Project-Id": project_id,
                    "X-Workflow-Tool-Read-Only": "1",
                },
                timeout=60.0,
            )

            if response.status_code != 200:
                logger.warning(
                    "AI Service Execute Tool HTTP status: %s",
                    response.status_code,
                )
                raise RuntimeError("业务工具服务暂时不可用，请稍后重试。")

            return ToolExecuteResponse.model_validate(response.json())
        except Exception as error:
            logger.warning(
                "AI Service execute tool failed: %s", type(error).__name__
            )
            raise RuntimeError(
                "业务工具服务暂时不可用，请稍后重试。"
            ) from None
