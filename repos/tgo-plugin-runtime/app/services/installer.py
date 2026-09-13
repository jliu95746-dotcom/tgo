"""Plugin Installer Service - Handles downloading, building and installing plugins."""

import os
import shutil
import asyncio
import platform
import sys
from uuid import uuid4
from typing import Optional, Tuple, Any
from pathlib import Path

from app.core.logging import get_logger
from app.schemas.install import PluginInstallRequest, PluginBuildConfig
from app.services.plugin_paths import plugin_directory, plugin_python, contained_path
from app.services.plugin_packages import download_package, unpack_package

logger = get_logger("services.installer")


class PluginInstaller:
    """Service for installing and uninstalling plugins."""

    def __init__(self, base_path: str = "/var/lib/tgo/plugins"):
        self.base_path = Path(base_path)
        self.temp_path = self.base_path / "temp"

    def _ensure_dirs(self):
        """Ensure necessary directories exist."""
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.temp_path.mkdir(parents=True, exist_ok=True)

    async def install(self, request: PluginInstallRequest, progress_callback: Optional[Any] = None) -> Tuple[bool, str, Optional[str]]:
        """
        Install a plugin based on the request.
        
        Returns:
            (success, message, install_path)
        """
        async def report(stage: str, message: str):
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(stage, message)
                else:
                    progress_callback(stage, message)

        plugin_id = request.id
        install_dir = plugin_directory(self.base_path, plugin_id)
        self._ensure_dirs()
        
        # Never overwrite another installation through the install endpoint.
        if install_dir.exists():
            return False, "Plugin already installed; use upgrade instead", None
        
        install_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            if request.source.github:
                success, message = await self._install_from_github(plugin_id, install_dir, request.source.github, request.build, report)
            elif request.source.binary:
                success, message = await self._install_from_binary(plugin_id, install_dir, request.source.binary, report)
                if success and request.build:
                    success, message = await self._build_plugin(plugin_id, install_dir, request.build, report)
            else:
                return False, "No source configuration provided", None
            
            if success:
                await report("starting", "Starting plugin...")
                return True, "Installation successful", str(install_dir)
            else:
                # Cleanup on failure
                if install_dir.exists():
                    shutil.rmtree(install_dir)
                return False, message, None
                
        except Exception as e:
            logger.exception(f"Unexpected error during installation of {plugin_id}: {e}")
            if install_dir.exists():
                shutil.rmtree(install_dir)
            return False, f"Unexpected error: {str(e)}", None

    async def upgrade(self, request: PluginInstallRequest, progress_callback: Optional[Any] = None, preserve_backup: bool = False) -> Tuple[bool, str, Optional[str]]:
        """
        Upgrade a plugin. Basically an install with backup/rollback support.
        """
        async def report(stage: str, message: str):
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(stage, message)
                else:
                    progress_callback(stage, message)

        plugin_id = request.id
        install_dir = plugin_directory(self.base_path, plugin_id)
        backup_dir = self.base_path / f"{plugin_id}_backup"
        
        # 1. Backup existing installation
        if install_dir.exists():
            await report("upgrading", "Backing up existing version...")
            if backup_dir.exists():
                return False, "Existing upgrade backup requires recovery", None
            # Both names resolve beneath the configured plugin root.
            contained_path(self.base_path, backup_dir.name)
            shutil.move(str(install_dir), str(backup_dir))
            
        try:
            # 2. Install at the final path while the old version stays in backup.
            success, message, path = await self.install(request, progress_callback=progress_callback)
            
            if success:
                # 3. Cleanup backup on success
                if backup_dir.exists() and not preserve_backup:
                    shutil.rmtree(backup_dir)
                return True, "Upgrade successful", path
            else:
                # 4. Rollback on failure
                await report("rolling_back", f"Upgrade failed: {message}. Rolling back...")
                if backup_dir.exists():
                    if install_dir.exists():
                        shutil.rmtree(install_dir)
                    shutil.move(str(backup_dir), str(install_dir))
                return False, message, None
                
        except Exception as e:
            logger.exception(f"Unexpected error during upgrade of {plugin_id}: {e}")
            # Rollback
            if backup_dir.exists():
                if install_dir.exists():
                    shutil.rmtree(install_dir)
                shutil.move(str(backup_dir), str(install_dir))
            return False, f"Unexpected error during upgrade: {str(e)}", None

    async def _install_from_github(
        self, 
        plugin_id: str, 
        install_dir: Path, 
        github_config: Any, 
        build_config: Optional[PluginBuildConfig],
        report: Any
    ) -> Tuple[bool, str]:
        """Clone and build from GitHub."""
        repo_url = f"https://github.com/{github_config.repo}.git"
        temp_repo_path = contained_path(self.temp_path, f"{plugin_id}_{uuid4().hex}")
        
        if temp_repo_path.exists():
            shutil.rmtree(temp_repo_path)
            
        try:
            # Clone repo
            msg = f"Cloning {repo_url} (ref: {github_config.ref})"
            logger.info(msg)
            await report("cloning", msg)
            
            clone_cmd = ["git", "clone", "-b", github_config.ref, repo_url, str(temp_repo_path)]
            try:
                process = await asyncio.create_subprocess_exec(
                    *clone_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
            except FileNotFoundError:
                return False, "System error: 'git' command not found. Please ensure git is installed in the runtime environment."
            
            if process.returncode != 0:
                return False, f"Git clone failed: {stderr.decode()}"
            
            await report("copying", "Copying files to installation directory...")
            
            # Move to target path
            source_path = temp_repo_path
            if github_config.path and github_config.path != "/":
                source_path = contained_path(temp_repo_path, github_config.path.lstrip("/"))
            
            if not source_path.exists():
                return False, f"Source path {github_config.path} not found in repo"
            
            # Copy all files to install dir
            for item in source_path.iterdir():
                if item.is_dir():
                    shutil.copytree(item, install_dir / item.name)
                else:
                    shutil.copy2(item, install_dir / item.name)
            
            # Build if needed
            if build_config:
                return await self._build_plugin(plugin_id, install_dir, build_config, report)
            
            return True, "Source installed successfully"
            
        finally:
            if temp_repo_path.exists():
                shutil.rmtree(temp_repo_path)

    async def _install_from_binary(self, plugin_id: str, install_dir: Path, binary_config: Any, report: Any) -> Tuple[bool, str]:
        """Download and install binary."""
        # Replace variables in URL
        url = binary_config.url
        # ... (rest of system/arch detection) ...
        system = platform.system().lower()
        if system == "darwin":
            system = "darwin"
        elif system == "windows":
            system = "windows"
        else:
            system = "linux"
            
        arch = platform.machine().lower()
        if arch in ("x86_64", "amd64"):
            arch = "amd64"
        elif arch in ("arm64", "aarch64"):
            arch = "arm64"
            
        url = url.replace("${os}", system).replace("${arch}", arch)
        
        msg = f"Downloading package for {plugin_id}"
        logger.info(msg)
        await report("downloading", msg)
        
        temp_file = contained_path(self.temp_path, f"{plugin_id}_{uuid4().hex}_bin")
        try:
            await download_package(url, temp_file)
            await report("copying", "Extracting binary...")
            unpack_package(temp_file, install_dir, url)
            return True, "Binary installed successfully"
            
        finally:
            if temp_file.exists():
                temp_file.unlink()

    async def _build_plugin(self, plugin_id: str, install_dir: Path, build_config: PluginBuildConfig, report: Any) -> Tuple[bool, str]:
        """Build plugin based on language."""
        lang = build_config.language.lower()
        
        if lang == "go":
            if not build_config.go:
                return False, "Missing Go build configuration"
            
            msg = f"Building Go plugin {plugin_id}"
            logger.info(msg)
            await report("building", msg)
            
            cmd = ["go", "build", "-o", build_config.go.output, build_config.go.main]
            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=str(install_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
            except FileNotFoundError:
                return False, "System error: 'go' compiler not found. Please ensure Go is installed."
            
            if process.returncode != 0:
                return False, f"Go build failed: {stderr.decode()}"
            
            return True, "Go build successful"
            
        elif lang == "python":
            if not build_config.python:
                return False, "Missing Python build configuration"
            
            msg = f"Installing Python dependencies for {plugin_id}"
            logger.info(msg)
            await report("building", msg)
            
            # Create venv
            venv_dir = install_dir / ".venv"
            cmd_venv = [sys.executable, "-m", "venv", str(venv_dir)]
            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd_venv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
            except FileNotFoundError:
                return False, "Python interpreter is unavailable"
            if process.returncode != 0:
                return False, f"Python venv creation failed: {stderr.decode(errors='replace')}"
            venv_python = plugin_python(install_dir, require_venv=True)
            
            # Install requirements
            req_file = build_config.python.requirements
            if contained_path(install_dir, req_file).exists():
                cmd_pip = [venv_python, "-m", "pip", "install", "-r", req_file]
                try:
                    process = await asyncio.create_subprocess_exec(
                        *cmd_pip,
                        cwd=str(install_dir),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    stdout, stderr = await process.communicate()
                except FileNotFoundError:
                    return False, "System error: 'pip' not found in virtual environment."
                
                if process.returncode != 0:
                    return False, f"Python pip install failed: {stderr.decode()}"
            
            return True, "Python setup successful"
            
        elif lang == "nodejs":
            if not build_config.nodejs:
                return False, "Missing Node.js build configuration"
            
            msg = f"Installing Node.js dependencies for {plugin_id}"
            logger.info(msg)
            await report("building", msg)
            
            if contained_path(install_dir, build_config.nodejs.package).exists():
                cmd = [shutil.which("npm.cmd" if os.name == "nt" else "npm") or "npm", "install"]
                try:
                    process = await asyncio.create_subprocess_exec(
                        *cmd,
                        cwd=str(install_dir),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    stdout, stderr = await process.communicate()
                except FileNotFoundError:
                    return False, "System error: 'npm' command not found. Please ensure Node.js is installed."
                
                if process.returncode != 0:
                    return False, f"npm install failed: {stderr.decode()}"
            
            return True, "Node.js setup successful"
            
        return False, f"Unsupported language: {lang}"

    async def uninstall(self, plugin_id: str) -> bool:
        """Uninstall a plugin."""
        install_dir = plugin_directory(self.base_path, plugin_id)
        if install_dir.exists():
            shutil.rmtree(install_dir)
            logger.info(f"Uninstalled plugin {plugin_id}")
            return True
        return False

    def complete_upgrade(self, plugin_id: str) -> None:
        plugin_directory(self.base_path, plugin_id)
        backup = contained_path(self.base_path, f"{plugin_id}_backup")
        if backup.exists():
            shutil.rmtree(backup)

    def rollback_upgrade(self, plugin_id: str) -> bool:
        target = plugin_directory(self.base_path, plugin_id)
        backup = contained_path(self.base_path, f"{plugin_id}_backup")
        if not backup.exists():
            return False
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(backup), str(target))
        return True


# Global instance
from app.config import settings as _settings

installer = PluginInstaller(base_path=_settings.PLUGIN_BASE_PATH)

