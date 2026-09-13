"""One install operation for normal HTTP and streaming progress."""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.plugin import InstalledPlugin
from app.schemas.install import PluginInstallRequest
from app.schemas.plugin import InstalledPluginInfo
from app.services.installer import installer
from app.services.process_manager import process_manager
from app.services.plugin_paths import plugin_directory, validate_plugin_id

Progress = Callable[[str, str], Awaitable[None]]
_install_locks: dict[str, asyncio.Lock] = {}


def plugin_lifecycle_lock(plugin_id: str) -> asyncio.Lock:
    validate_plugin_id(plugin_id)
    return _install_locks.setdefault(plugin_id, asyncio.Lock())


def apply_plugin_config(plugin: InstalledPlugin, request: PluginInstallRequest) -> None:
    plugin.name = request.name
    plugin.version = request.version
    plugin.latest_version = request.version
    plugin.description = request.description
    plugin.author = request.author
    plugin.source_url = request.source_url
    plugin.install_type = "github" if request.source.github else "binary"
    plugin.source_config = request.source.model_dump(exclude_none=True)
    plugin.build_config = (
        request.build.model_dump(exclude_none=True) if request.build else None
    )
    plugin.runtime_config = request.runtime.model_dump(exclude_none=True)


class PluginInstallationError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


async def install_plugin_operation(
    request: PluginInstallRequest, progress: Progress | None = None
) -> InstalledPluginInfo:
    validate_plugin_id(request.id)
    project_id = UUID(request.project_id)
    lock = plugin_lifecycle_lock(request.id)
    async with lock:
        async with AsyncSessionLocal() as session:
            row = await session.execute(
                select(InstalledPlugin).where(InstalledPlugin.plugin_id == request.id)
            )
            plugin = row.scalar_one_or_none()
            if plugin is not None and plugin.project_id != project_id:
                raise PluginInstallationError(
                    "Plugin ID is not available for this project", 409
                )
            if plugin_directory(installer.base_path, request.id).exists():
                raise PluginInstallationError(
                    "Plugin already installed; use upgrade instead", 409
                )
            if plugin is None:
                plugin = InstalledPlugin(plugin_id=request.id, project_id=project_id)
                session.add(plugin)
            apply_plugin_config(plugin, request)
            plugin.status = "installing"
            plugin.last_error = None
            await session.commit()

        try:
            success, message, path = await installer.install(
                request, progress_callback=progress
            )
        except Exception as error:
            success, message, path = False, f"Installation failed: {error}", None
        async with AsyncSessionLocal() as session:
            row = await session.execute(
                select(InstalledPlugin).where(
                    InstalledPlugin.plugin_id == request.id,
                    InstalledPlugin.project_id == project_id,
                )
            )
            plugin = row.scalar_one()
            plugin.status = "starting" if success else "error"
            plugin.install_path = path
            plugin.last_error = None if success else message
            await session.commit()
        if not success:
            raise PluginInstallationError(message)

        try:
            started, message = await process_manager.start_plugin(
                request.id, request.model_dump()
            )
        except Exception as error:
            started, message = False, f"Plugin startup failed: {error}"
        if not started:
            async with AsyncSessionLocal() as session:
                row = await session.execute(
                    select(InstalledPlugin).where(
                        InstalledPlugin.plugin_id == request.id,
                        InstalledPlugin.project_id == project_id,
                    )
                )
                plugin = row.scalar_one()
                plugin.status = "error"
                plugin.last_error = message
                plugin.pid = None
                await session.commit()
            raise PluginInstallationError(message)
        async with AsyncSessionLocal() as session:
            row = await session.execute(
                select(InstalledPlugin).where(
                    InstalledPlugin.plugin_id == request.id,
                    InstalledPlugin.project_id == project_id,
                )
            )
            plugin = row.scalar_one()
            result = InstalledPluginInfo.model_validate(plugin, from_attributes=True)
        if progress:
            await progress("complete", "Installation successful; plugin connected")
        return result
