"""Installation must work natively and must not erase unrelated directories."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4
import zipfile
import io
import tarfile

import pytest

from app.schemas.install import (
    PluginBuildConfig,
    PluginBuildPython,
    PluginInstallRequest,
)
from app.services.installer import PluginInstaller


def request(plugin_id="fixture-plugin"):
    return PluginInstallRequest(
        id=plugin_id,
        project_id=str(uuid4()),
        name="fixture",
        version="1",
        source={"binary": {"url": "http://127.0.0.1/fixture.zip"}},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "plugin_id", ["..", "../outside", "a/../b", "C:\\outside", "temp", "CON", "name."]
)
async def test_unsafe_install_id_cannot_touch_existing_files(tmp_path, plugin_id):
    root = tmp_path / "plugins"
    root.mkdir()
    marker = root / "keep.txt"
    marker.write_text("owned by someone else")
    installer = PluginInstaller(str(root))
    with pytest.raises(ValueError):
        await installer.install(request(plugin_id))
    assert marker.read_text() == "owned by someone else"


@pytest.mark.asyncio
async def test_failed_venv_creation_is_not_success(tmp_path, monkeypatch):
    class FailedProcess:
        returncode = 1

        async def communicate(self):
            return b"", b"venv failed"

        async def wait(self):
            return 1

    spawn = AsyncMock(return_value=FailedProcess())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    installer = PluginInstaller(str(tmp_path))
    build = PluginBuildConfig(language="python", python=PluginBuildPython())
    success, message = await installer._build_plugin(
        "fixture", tmp_path, build, AsyncMock()
    )
    assert not success and "venv" in message.lower()
    assert spawn.await_args.args[0] == sys.executable


def test_regular_install_route_is_not_bound_to_uninstall():
    from app.main import app

    endpoint = next(
        route.endpoint
        for route in app.routes
        if getattr(route, "path", "") == "/plugins/install"
    )
    assert endpoint.__name__ == "install_plugin"


@pytest.mark.parametrize(
    "name", ["../escape.txt", "a/.. /escape.txt", "C:/escape.txt", "file:stream"]
)
def test_archives_cannot_write_outside_install_directory(tmp_path, name):
    from app.services.plugin_packages import unpack_package

    archive = tmp_path / "plugin.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("main.py", "safe")
        package.writestr(name, "must not be written")
    destination = tmp_path / "install"
    destination.mkdir()
    with pytest.raises(ValueError):
        unpack_package(archive, destination, "http://127.0.0.1/plugin.zip")
    assert list(destination.iterdir()) == []


def test_zip_with_query_string_extracts_without_external_unzip(tmp_path):
    from app.services.plugin_packages import unpack_package

    archive = tmp_path / "package"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("main.py", "fixture")
    destination = tmp_path / "install"
    destination.mkdir()
    unpack_package(archive, destination, "http://127.0.0.1/plugin.zip?download=1")
    assert (destination / "main.py").read_text() == "fixture"


@pytest.mark.asyncio
async def test_install_never_erases_an_existing_version(tmp_path, monkeypatch):
    installer = PluginInstaller(str(tmp_path))
    existing = tmp_path / "fixture-plugin"
    existing.mkdir()
    (existing / "main.py").write_text("existing version")
    download = AsyncMock()
    monkeypatch.setattr(installer, "_install_from_binary", download)
    success, message, _ = await installer.install(request())
    assert not success and "upgrade" in message
    assert (existing / "main.py").read_text() == "existing version"
    download.assert_not_awaited()


@pytest.mark.asyncio
async def test_npm_cmd_is_executable_on_this_windows_host():
    import os
    import shutil

    if os.name != "nt" or not shutil.which("npm.cmd"):
        pytest.skip("Windows npm command probe")
    process = await asyncio.create_subprocess_exec(
        shutil.which("npm.cmd"),
        "--version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
    assert process.returncode == 0, stderr.decode(errors="replace")
    assert stdout.strip()


def test_tar_extracts_regular_files_without_external_tar(tmp_path):
    from app.services.plugin_packages import unpack_package

    archive = tmp_path / "package.tgz"
    with tarfile.open(archive, "w:gz") as package:
        item = tarfile.TarInfo("main.py")
        item.size = 7
        package.addfile(item, io.BytesIO(b"fixture"))
    destination = tmp_path / "install"
    destination.mkdir()
    unpack_package(archive, destination, "http://127.0.0.1/package.tgz")
    assert (destination / "main.py").read_text() == "fixture"


def test_tar_link_is_rejected_before_any_files_are_written(tmp_path):
    from app.services.plugin_packages import unpack_package

    archive = tmp_path / "package.tgz"
    with tarfile.open(archive, "w:gz") as package:
        item = tarfile.TarInfo("main.py")
        item.size = 7
        package.addfile(item, io.BytesIO(b"fixture"))
        link = tarfile.TarInfo("link")
        link.type = tarfile.SYMTYPE
        link.linkname = "../outside"
        package.addfile(link)
    destination = tmp_path / "install"
    destination.mkdir()
    with pytest.raises(ValueError):
        unpack_package(archive, destination, "http://127.0.0.1/package.tgz")
    assert not list(destination.iterdir())


@pytest.mark.asyncio
async def test_upgrade_keeps_previous_files_until_startup_is_confirmed(tmp_path, monkeypatch):
    installer = PluginInstaller(str(tmp_path))
    config = request()
    directory = tmp_path / config.id
    directory.mkdir()
    (directory / "main.py").write_text("previous")

    async def download(plugin_id, target, binary, report):
        (target / "main.py").write_text("new")
        return True, "downloaded"

    monkeypatch.setattr(installer, "_install_from_binary", download)
    success, _, _ = await installer.upgrade(config, preserve_backup=True)
    assert success and (directory / "main.py").read_text() == "new"
    assert (tmp_path / (config.id + "_backup") / "main.py").read_text() == "previous"
    assert installer.rollback_upgrade(config.id)
    assert (directory / "main.py").read_text() == "previous"
    assert not (tmp_path / (config.id + "_backup")).exists()
