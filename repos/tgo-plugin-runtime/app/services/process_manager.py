"""Process Manager - Manages plugin processes and their lifecycle."""

import asyncio
import os
import collections
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger
from app.config import settings
from app.core.database import SessionLocal
from app.models.plugin import InstalledPlugin
from app.services.plugin_paths import plugin_directory, plugin_python, contained_path
from app.services.plugin_manager import plugin_manager

logger = get_logger("services.process_manager")


@dataclass
class ManagedPlugin:
    """Represents a plugin process managed by this service."""
    id: str
    config: Dict[str, Any]
    process: Optional[asyncio.subprocess.Process] = None
    status: str = "stopped"  # stopped, running, starting, error
    pid: Optional[int] = None
    last_error: Optional[str] = None
    restart_count: int = 0
    logs: collections.deque = field(default_factory=lambda: collections.deque(maxlen=1000))
    _stop_requested: bool = False
    _restart_task: Optional[asyncio.Task] = None


class ProcessManager:
    """Service to manage plugin processes."""
    
    def __init__(self, base_path: str = "/var/lib/tgo/plugins"):
        self.base_path = Path(base_path)
        self._managed_plugins: Dict[str, ManagedPlugin] = {}
        self._lock = asyncio.Lock()
        self._monitor_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the process monitor task."""
        if self._monitor_task is None:
            self._monitor_task = asyncio.create_task(self._monitor_loop())
            logger.info("Process manager monitor loop started")

    async def stop(self):
        """Stop all managed plugins and the monitor task."""
        if self._monitor_task:
            self._monitor_task.cancel()
            self._monitor_task = None
            
        async with self._lock:
            for plugin_id in list(self._managed_plugins.keys()):
                resume = self._managed_plugins[plugin_id].status in ("running", "starting")
                stopped = await self._stop_plugin_inner(plugin_id)
                if resume and stopped:
                    # A service shutdown is not an explicit user stop.
                    await self._update_db_status(plugin_id, "starting", pid=None)
        
        logger.info("Process manager stopped")

    async def _update_db_status(self, plugin_id: str, status: str, pid: Optional[int] = None, last_error: Optional[str] = None):
        """Update plugin status in database."""
        try:
            with SessionLocal() as db:
                plugin = db.query(InstalledPlugin).filter(InstalledPlugin.plugin_id == plugin_id).first()
                if plugin:
                    plugin.status = status
                    plugin.pid = pid
                    if last_error is not None:
                        plugin.last_error = last_error
                    db.commit()
        except Exception as e:
            logger.error(f"Failed to update DB status for {plugin_id}: {e}")

    async def _monitor_loop(self):
        """Monitor running processes and auto-restart if needed."""
        while True:
            try:
                await asyncio.sleep(5)
                async with self._lock:
                    for plugin_id, managed in list(self._managed_plugins.items()):
                        if managed.status == "running" and managed.process:
                            # Check if process is still alive
                            if managed.process.returncode is not None:
                                logger.warning(f"Plugin {plugin_id} exited with code {managed.process.returncode}")
                                managed.status = "error"
                                managed.pid = None
                                await self._update_db_status(plugin_id, "error", pid=None, last_error=f"Exited with code {managed.process.returncode}")
                                
                                # Auto restart if configured
                                runtime_config = managed.config.get("runtime") or {}
                                if runtime_config.get("auto_restart", True) and not managed._stop_requested:
                                    delay = runtime_config.get("restart_delay", 5)
                                    logger.info(f"Auto-restarting plugin {plugin_id} in {delay}s...")
                                    managed._restart_task = asyncio.create_task(self._delayed_restart(plugin_id, delay))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in process monitor loop: {e}")

    async def _delayed_restart(self, plugin_id: str, delay: int):
        """Restart a plugin after a delay."""
        await asyncio.sleep(delay)
        async with self._lock:
            managed = self._managed_plugins.get(plugin_id)
            if managed and not managed._stop_requested and managed.status == "error":
                await self._start_plugin_inner(plugin_id, managed.config)

    async def start_plugin(self, plugin_id: str, config: Dict[str, Any]) -> Tuple[bool, str]:
        """Start a plugin process."""
        async with self._lock:
            return await self._start_plugin_inner(plugin_id, config)

    async def _start_plugin_inner(self, plugin_id: str, config: Dict[str, Any]) -> Tuple[bool, str]:
        """Internal start_plugin without lock."""
        try:
            success, message = await self._launch_plugin(plugin_id, config)
        except Exception as error:
            success, message = False, f"Plugin startup failed: {error}"
        if not success:
            managed = self._managed_plugins.get(plugin_id)
            if managed:
                managed.status = "error"
                managed.last_error = message
            await self._update_db_status(plugin_id, "error", last_error=message)
        return success, message

    async def _launch_plugin(self, plugin_id: str, config: Dict[str, Any]) -> Tuple[bool, str]:
        if plugin_id in self._managed_plugins:
            managed = self._managed_plugins[plugin_id]
            if managed.status == "running" and managed.process and managed.process.returncode is None:
                return True, "Already running"
        else:
            managed = ManagedPlugin(id=plugin_id, config=config)
            self._managed_plugins[plugin_id] = managed
        
        managed.config = config  # Update config
        managed.status = "starting"
        managed._stop_requested = False
        
        # Prepare command
        install_dir = plugin_directory(self.base_path, plugin_id)
        if not install_dir.exists():
            managed.status = "error"
            managed.last_error = f"Install directory not found: {install_dir}"
            return False, managed.last_error
        
        build_config = config.get("build") or {}
        runtime_config = config.get("runtime") or {}
        lang = build_config.get("language", "").lower()
        
        cmd = []
        env = os.environ.copy()
        env.update(runtime_config.get("env", {}))
        
        # Set socket path for plugin SDK to connect
        env["TGO_SOCKET_PATH"] = settings.PLUGIN_SOCKET_PATH
        if settings.PLUGIN_TCP_PORT:
            env["TGO_TCP_PORT"] = str(settings.PLUGIN_TCP_PORT)
            env["TGO_TCP_HOST"] = "127.0.0.1" if settings.PLUGIN_TCP_HOST in ("0.0.0.0", "::") else settings.PLUGIN_TCP_HOST
        env["TGO_PLUGIN_ID"] = plugin_id
        
        if lang == "go":
            # Entrypoint is the compiled binary
            binary_name = (build_config.get("go") or {}).get("output", "plugin")
            binary_path = contained_path(install_dir, binary_name)
            if not binary_path.exists():
                # Fallback to 'plugin'
                binary_path = install_dir / "plugin"
            
            if not binary_path.exists():
                managed.status = "error"
                managed.last_error = f"Plugin binary not found: {binary_path}"
                return False, managed.last_error
            
            cmd = [str(binary_path)]
            
        elif lang == "python":
            entrypoint = (build_config.get("python") or {}).get("entrypoint", "main.py")
            contained_path(install_dir, entrypoint)
            cmd = [plugin_python(install_dir), entrypoint]
            
        elif lang == "nodejs":
            entrypoint = (build_config.get("nodejs") or {}).get("entrypoint", "index.js")
            contained_path(install_dir, entrypoint)
            cmd = ["node", entrypoint]
        else:
            # Default to binary install
            binary_path = install_dir / "plugin"
            if not binary_path.exists() and os.name == "nt":
                binary_path = install_dir / "plugin.exe"
            if not binary_path.exists():
                managed.status = "error"
                managed.last_error = "No entrypoint or binary found for plugin"
                return False, managed.last_error
            cmd = [str(binary_path)]
        
        # Add arguments
        cmd.extend(runtime_config.get("args", []))
        
        logger.info(f"Starting plugin {plugin_id}")
        
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(install_dir),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            managed.process = process
            managed.pid = process.pid
            # Start logging task
            asyncio.create_task(self._read_logs(managed, process))
            await self._update_db_status(plugin_id, "starting", pid=managed.pid)
            deadline = asyncio.get_running_loop().time() + settings.PLUGIN_REQUEST_TIMEOUT
            while asyncio.get_running_loop().time() < deadline:
                if process.returncode is not None:
                    raise RuntimeError(f"Plugin exited before registration (code={process.returncode})")
                connection = plugin_manager.get_plugin(plugin_id, config.get("project_id"))
                if connection and connection.writer and not connection.writer.is_closing():
                    break
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Plugin did not register before the startup timeout")
            managed.status = "running"
            managed.last_error = None
            managed.restart_count += 1
            await self._update_db_status(plugin_id, "running", pid=managed.pid, last_error="")
            return True, "Started successfully"
        except Exception as e:
            await self._stop_plugin_inner(plugin_id)
            managed.status = "error"
            managed.last_error = f"Failed to start: {str(e)}"
            logger.error(f"Failed to start plugin {plugin_id}: {e}")
            await self._update_db_status(plugin_id, "error", last_error=managed.last_error)
            return False, managed.last_error

    async def _read_logs(self, managed: ManagedPlugin, process: asyncio.subprocess.Process):
        """Read process output and store in buffer."""
        if not process.stdout:
            return
            
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            
            decoded_line = line.decode(errors="replace").strip()
            managed.logs.append(decoded_line)
            # Optional: also log to system logger
            # logger.debug(f"[{managed.id}] {decoded_line}")

    async def stop_plugin(self, plugin_id: str) -> bool:
        """Stop a plugin process."""
        async with self._lock:
            return await self._stop_plugin_inner(plugin_id)

    async def _stop_plugin_inner(self, plugin_id: str) -> bool:
        """Internal stop_plugin without lock."""
        managed = self._managed_plugins.get(plugin_id)
        if not managed or managed.status == "stopped":
            return True
        
        managed._stop_requested = True
        if managed._restart_task and managed._restart_task is not asyncio.current_task():
            managed._restart_task.cancel()
            managed._restart_task = None
        if managed.process and managed.process.returncode is None:
            logger.info(f"Stopping plugin {plugin_id} (pid={managed.pid})")
            try:
                managed.process.terminate()
                try:
                    await asyncio.wait_for(managed.process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    managed.process.kill()
                    await managed.process.wait()
            except Exception as e:
                logger.error(f"Error stopping plugin {plugin_id}: {e}")
                return False
        connection = plugin_manager.get_plugin(plugin_id)
        if connection:
            await plugin_manager.unregister(plugin_id, expected_connection=connection)
        
        managed.status = "stopped"
        managed.pid = None
        managed.process = None
        await self._update_db_status(plugin_id, "stopped", pid=None)
        return True

    async def restart_plugin(self, plugin_id: str) -> Tuple[bool, str]:
        """Restart a plugin process."""
        async with self._lock:
            managed = self._managed_plugins.get(plugin_id)
            if not managed:
                return False, "Plugin not managed"
            
            config = managed.config
            if not await self._stop_plugin_inner(plugin_id):
                return False, "Plugin could not be stopped; restart aborted"
            return await self._start_plugin_inner(plugin_id, config)

    def get_logs(self, plugin_id: str) -> List[str]:
        """Get the latest logs for a plugin."""
        managed = self._managed_plugins.get(plugin_id)
        if managed:
            return list(managed.logs)
        return []

    def get_status(self, plugin_id: str) -> Dict[str, Any]:
        """Get the status of a plugin."""
        managed = self._managed_plugins.get(plugin_id)
        if managed:
            return {
                "id": managed.id,
                "status": managed.status,
                "pid": managed.pid,
                "restart_count": managed.restart_count,
                "last_error": managed.last_error
            }
        return {"id": plugin_id, "status": "not_managed"}


# Global instance
from app.config import settings as _settings

process_manager = ProcessManager(base_path=_settings.PLUGIN_BASE_PATH)

