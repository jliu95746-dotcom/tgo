"""Upgrade with the previous files and configuration retained until registration."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.plugin import InstalledPlugin
from app.schemas.install import (
    PluginBuildConfig,
    PluginInstallRequest,
    PluginRuntimeConfig,
    PluginSourceConfig,
    PluginUpgradeRequest,
)
from app.services.installer import installer
from app.services.plugin_installation import (
    PluginInstallationError,
    Progress,
    apply_plugin_config,
    plugin_lifecycle_lock,
)
from app.services.plugin_paths import contained_path, plugin_directory
from app.services.process_manager import process_manager


@dataclass
class PreviousVersion:
    config: PluginInstallRequest
    install_path: str | None
    latest_version: str | None
    was_running: bool


async def save_version(
    config: PluginInstallRequest,
    path: str | None,
    error: str | None = None,
    latest_version: str | None = None,
) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InstalledPlugin).where(
                InstalledPlugin.plugin_id == config.id,
                InstalledPlugin.project_id == UUID(config.project_id),
            )
        )
        plugin = result.scalar_one()
        apply_plugin_config(plugin, config)
        plugin.install_path = path
        plugin.latest_version = latest_version or config.version
        plugin.status = "stopped"
        plugin.pid = None
        plugin.last_error = error
        await session.commit()


async def restore_previous(previous: PreviousVersion, error: str) -> str:
    config = previous.config
    await save_version(config, previous.install_path, error, previous.latest_version)
    if not previous.was_running:
        return "Original files and configuration restored; plugin remains stopped"
    started, message = await process_manager.start_plugin(
        config.id, config.model_dump()
    )
    if started:
        return "Original version restored and connected"
    return f"Original files restored, but restart failed: {message}"


async def upgrade_plugin_operation(
    plugin_id: str,
    request: PluginUpgradeRequest,
    project_id: str | None,
    progress: Progress,
) -> None:
    if request.latest_config.id != plugin_id:
        raise PluginInstallationError("Upgrade configuration has a different plugin ID")
    async with plugin_lifecycle_lock(plugin_id):
        async with AsyncSessionLocal() as session:
            statement = select(InstalledPlugin).where(
                InstalledPlugin.plugin_id == plugin_id
            )
            if project_id:
                statement = statement.where(
                    InstalledPlugin.project_id == UUID(project_id)
                )
            result = await session.execute(statement)
            plugin = result.scalar_one_or_none()
            if plugin is None:
                raise PluginInstallationError("Plugin not found", 404)
            previous = PreviousVersion(
                config=PluginInstallRequest(
                    id=plugin.plugin_id,
                    project_id=str(plugin.project_id),
                    name=plugin.name,
                    version=plugin.version,
                    description=plugin.description,
                    author=plugin.author,
                    source_url=plugin.source_url,
                    source=PluginSourceConfig.model_validate(plugin.source_config),
                    build=PluginBuildConfig.model_validate(plugin.build_config)
                    if plugin.build_config
                    else None,
                    runtime=PluginRuntimeConfig.model_validate(
                        plugin.runtime_config or {}
                    ),
                ),
                install_path=plugin.install_path,
                latest_version=plugin.latest_version,
                was_running=plugin.status in ("running", "starting"),
            )
        backup = contained_path(installer.base_path, f"{plugin_id}_backup")
        if backup.exists():
            raise PluginInstallationError(
                "Existing upgrade backup requires recovery", 409
            )
        if not plugin_directory(installer.base_path, plugin_id).is_dir():
            raise PluginInstallationError(
                "Original installation is missing; upgrade aborted"
            )
        config = PluginInstallRequest(
            project_id=previous.config.project_id, **request.latest_config.model_dump()
        )
        await progress("stopping", "Stopping plugin process...")
        if not await process_manager.stop_plugin(plugin_id):
            raise PluginInstallationError(
                "Plugin could not be stopped; files were preserved", 409
            )
        try:
            success, message, path = await installer.upgrade(
                config, progress_callback=progress, preserve_backup=True
            )
            if not success:
                # Installer restores the old directory on download/build failure.
                if backup.exists():
                    raise RuntimeError(
                        "Upgrade rollback is incomplete; backup preserved"
                    )
                recovery = await restore_previous(previous, message)
                await progress("error", f"Upgrade failed: {message}. {recovery}")
                return
            await save_version(config, path)
            await progress("starting", "Waiting for upgraded plugin to connect...")
            started, message = await process_manager.start_plugin(
                plugin_id, config.model_dump()
            )
            if not started:
                raise RuntimeError(message)
        except Exception as error:
            if not await process_manager.stop_plugin(plugin_id):
                await progress(
                    "error",
                    f"Upgrade failed: {error}. "
                    "Could not stop process; backup preserved",
                )
                return
            try:
                restored = installer.rollback_upgrade(plugin_id)
                if not restored:
                    await progress(
                        "error",
                        f"Upgrade failed: {error}. "
                        "No backup available; recovery not confirmed",
                    )
                    return
                recovery = await restore_previous(previous, str(error))
            except Exception as recovery_error:
                await progress(
                    "error",
                    f"Upgrade failed: {error}. Recovery failed: {recovery_error}",
                )
                return
            await progress("error", f"Upgrade failed: {error}. {recovery}")
            return
        # Do not roll back a running version just because backup cleanup failed.
        try:
            installer.complete_upgrade(plugin_id)
        except Exception as error:
            await progress(
                "error", f"New version connected, but backup cleanup failed: {error}"
            )
            return
        await progress("complete", "Upgrade successful; plugin connected")
