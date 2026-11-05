"""Remote employee identity and guarded tool continuation."""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Coroutine,
    Mapping,
    Sequence,
)
from typing import Literal, cast, overload

from agno.agent import RemoteAgent
from agno.models.response import ToolExecution
from agno.os.routers.agents.schema import AgentResponse
from agno.os.schema import ConfigResponse
from agno.run.agent import RunOutput, RunOutputEvent
from agno.tools.function import Function

from app.config import settings
from app.runtime.tools.remote_run import RemoteRun
from app.runtime.tools.remote_tool_execution import (
    RemoteToolError,
    execute_remote_tools,
    normalize_tools,
    pending_tools,
)


class StoreRemoteAgent(RemoteAgent):
    def __init__(
        self,
        base_url: str,
        agent_id: str,
        timeout: float = 60.0,
        protocol: Literal["agentos", "a2a"] = "agentos",
        a2a_protocol: Literal["json-rpc", "rest"] = "rest",
        config_ttl: float = 300.0,
        *,
        override_id: str | None = None,
        override_name: str | None = None,
        override_metadata: dict[str, object] | None = None,
        api_key: str | None = None,
        knowledge_filters: dict[str, object] | None = None,
        tools: Sequence[object] | None = None,
    ) -> None:
        self._override_id = override_id
        self._override_name = override_name
        self._override_metadata = dict(override_metadata or {})
        self._api_key = api_key
        self.knowledge_filters = knowledge_filters
        self.local_tools = list(tools or [])
        self._tool_map: dict[str, Function] = {}
        self._build_tool_map()
        super().__init__(
            base_url, agent_id, timeout, protocol, a2a_protocol, config_ttl
        )

    def _build_tool_map(self) -> None:
        self._tool_map = normalize_tools(self.local_tools)

    def _build_tools_schema(self) -> list[dict[str, object]]:
        return [
            {
                "name": function.name,
                "description": function.description or "",
                "parameters": function.parameters,
            }
            for function in self._tool_map.values()
        ]

    async def _execute_tools_locally(
        self,
        tool_calls: Sequence[ToolExecution | Mapping[str, object]],
    ) -> list[ToolExecution]:
        return await execute_remote_tools(self._tool_map, tool_calls)

    def bind_runtime_identity(
        self,
        identifier: str,
        name: str,
        metadata: dict[str, object],
    ) -> None:
        self._override_id = identifier
        self._override_name = name
        self._override_metadata = dict(metadata)

    def _has_pending_tool_calls(self, result: object) -> bool:
        values: object = (
            result.get("tools", [])
            if isinstance(result, dict)
            else getattr(result, "tools", [])
        )
        if values is None:
            return False
        if not isinstance(values, Sequence) or isinstance(
            values, (str, bytes)
        ):
            raise RemoteToolError()
        return bool(
            pending_tools(
                cast(Sequence[ToolExecution | Mapping[str, object]], values)
            )
        )

    @property
    def id(self) -> str:
        return self._override_id or self.agent_id

    @property
    def name(self) -> str | None:
        return self._override_name or super().name

    @property
    def metadata(self) -> dict[str, object]:
        return dict(self._override_metadata)

    def get_auth_headers(
        self, auth_token: str | None = None
    ) -> dict[str, str] | None:
        return self._get_auth_headers(auth_token)

    def _get_auth_headers(
        self, auth_token: str | None = None
    ) -> dict[str, str] | None:
        headers = super()._get_auth_headers(auth_token) or {}
        api_key = self._api_key or settings.store_api_key
        if api_key:
            headers["X-API-Key"] = api_key
            headers.setdefault("Authorization", f"Bearer {api_key}")
        return headers or None

    @property
    def _agent_config(self) -> AgentResponse | None:
        now = time.time()
        if self._cached_agent_config is not None:
            config, cached_at = self._cached_agent_config
            if now - cached_at < self.config_ttl:
                return config
        if self.agentos_client is None:
            return super()._agent_config
        try:
            config = self.agentos_client.get_agent(
                self.agent_id, headers=self._get_auth_headers()
            )
            self._cached_agent_config = (config, now)
            return config
        except Exception:
            return None

    @property
    def _config(self) -> ConfigResponse | None:
        now = time.time()
        if self._cached_config is not None:
            config, cached_at = self._cached_config
            if now - cached_at < self.config_ttl:
                return config
        if self.agentos_client is None:
            return super()._config
        try:
            config = self.agentos_client.get_config(
                headers=self._get_auth_headers()
            )
            self._cached_config = (config, now)
            return config
        except Exception:
            return None

    async def get_agent_config(self) -> AgentResponse:
        if self.agentos_client is None:
            return await super().get_agent_config()
        return await self.agentos_client.aget_agent(
            self.agent_id, headers=self._get_auth_headers()
        )

    async def refresh_config(self) -> AgentResponse | None:
        if self.agentos_client is None:
            return await super().refresh_config()
        config = await self.get_agent_config()
        self._cached_agent_config = (config, time.time())
        return config

    # Agno 2.3.23 BaseRemote types non-stream arun as RunOutput, but its actual
    # RemoteAgent returns a coroutine. Keep the verified runtime signature.
    @overload  # type: ignore[override]
    async def arun(
        self,
        input: object,
        *,
        stream: Literal[False] | None = False,
        **kwargs: object,
    ) -> RunOutput:
        ...

    @overload
    def arun(
        self, input: object, *, stream: Literal[True] = True, **kwargs: object
    ) -> AsyncIterator[RunOutputEvent]:
        ...

    @overload
    def arun(
        self, input: object, *, stream: bool | None, **kwargs: object
    ) -> Coroutine[object, object, RunOutput] | AsyncIterator[RunOutputEvent]:
        ...

    def arun(
        self,
        input: object,
        *,
        stream: bool | None = False,
        **kwargs: object,
    ) -> Coroutine[object, object, RunOutput] | AsyncIterator[RunOutputEvent]:
        run = self._remote_run(kwargs)
        if self.local_tools:
            kwargs["tools"] = json.dumps(
                self._build_tools_schema(), allow_nan=False
            )
        # Open kwargs are SDK extensions; RemoteRun checks every output/event.
        native = cast(Callable[..., object], super().arun)
        if stream:
            result = native(input, stream=True, **kwargs)
            if not isinstance(result, AsyncIterator):
                if asyncio.iscoroutine(result):
                    result.close()
                raise RemoteToolError()
            return run.stream(cast(AsyncIterator[RunOutputEvent], result))
        result = native(input, stream=False, **kwargs)
        if not isinstance(result, Awaitable):
            raise RemoteToolError()
        return run.complete(cast(Awaitable[RunOutput], result))

    def _remote_run(self, values: Mapping[str, object]) -> RemoteRun:
        session_id = values.get("session_id")
        user_id = values.get("user_id")
        auth_token = values.get("auth_token")
        for value in (session_id, user_id, auth_token):
            if value is not None and not isinstance(value, str):
                raise RemoteToolError()
        # Keep credentials in this run's closure, never on a shared client.
        # The native continuation API accepts the original token and user.
        token = auth_token if isinstance(auth_token, str) else None
        owner = user_id if isinstance(user_id, str) else None
        session = session_id if isinstance(session_id, str) else None
        client = self.agentos_client

        def resume(
            *,
            run_id: str,
            updated_tools: list[ToolExecution],
            session_id: str | None,
            stream: bool,
        ) -> object:
            if client is None:
                raise RemoteToolError()
            if stream:
                return self.acontinue_run(
                    run_id=run_id,
                    updated_tools=updated_tools,
                    stream=True,
                    session_id=session_id,
                    user_id=owner,
                    auth_token=token,
                )
            return self.acontinue_run(
                run_id=run_id,
                updated_tools=updated_tools,
                stream=False,
                session_id=session_id,
                user_id=owner,
                auth_token=token,
            )

        return RemoteRun(
            self._execute_tools_locally,
            resume,
            self._override_id,
            self._override_name,
            session,
            owner,
            can_resume=client is not None,
        )

    async def _wrap_coro_with_tool_execution(
        self,
        coro: Awaitable[RunOutput],
        **kwargs: object,
    ) -> RunOutput:
        return await self._remote_run(kwargs).complete(coro)

    async def _wrap_stream_with_tool_execution(
        self,
        stream_result: AsyncIterator[RunOutputEvent],
        **kwargs: object,
    ) -> AsyncGenerator[RunOutputEvent, None]:
        source = self._remote_run(kwargs).stream(stream_result)
        try:
            async for event in source:
                yield event
        finally:
            await source.aclose()

    def _map_ids(self, result: object) -> None:
        # Compatibility for callers retaining the old helper.
        if self._override_id and hasattr(result, "agent_id"):
            result.agent_id = self._override_id
        if self._override_name and hasattr(result, "agent_name"):
            result.agent_name = self._override_name
