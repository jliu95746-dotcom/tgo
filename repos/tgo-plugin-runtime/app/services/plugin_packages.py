"""Cross-platform download and archive extraction for plugin packages."""

import shutil
import stat
import tarfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.services.plugin_paths import contained_path

MAX_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_PACKAGE_FILES = 10000


async def download_package(url: str, target: Path) -> None:
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("Plugin packages require an HTTP(S) URL")
    size = 0
    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True, trust_env=False
    ) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with target.open("wb") as output:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_PACKAGE_BYTES:
                        raise ValueError("Plugin package exceeds size limit")
                    output.write(chunk)


def unpack_package(archive: Path, install_dir: Path, url: str) -> None:
    suffix = urlparse(url).path.lower()
    if suffix.endswith(".zip"):
        with zipfile.ZipFile(archive) as package:
            members = package.infolist()
            if (
                len(members) > MAX_PACKAGE_FILES
                or sum(m.file_size for m in members) > MAX_PACKAGE_BYTES
            ):
                raise ValueError("Plugin archive exceeds extraction limit")
            # Validate the entire archive before writing any member.
            for member in members:
                contained_path(install_dir, member.filename)
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError("Plugin archives cannot contain symlinks")
            for member in members:
                target = contained_path(install_dir, member.filename)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(member) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    target.chmod((member.external_attr >> 16) & 0o777 or 0o755)
    elif suffix.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive, "r:gz") as tar_package:
            tar_members = tar_package.getmembers()
            if (
                len(tar_members) > MAX_PACKAGE_FILES
                or sum(m.size for m in tar_members) > MAX_PACKAGE_BYTES
            ):
                raise ValueError("Plugin archive exceeds extraction limit")
            for tar_member in tar_members:
                contained_path(install_dir, tar_member.name)
                if not (tar_member.isfile() or tar_member.isdir()):
                    raise ValueError(
                        "Plugin archives may contain only files/directories"
                    )
            for tar_member in tar_members:
                target = contained_path(install_dir, tar_member.name)
                if tar_member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    tar_source = tar_package.extractfile(tar_member)
                    if tar_source is None:
                        raise ValueError("Missing archive member")
                    with tar_source, target.open("wb") as output:
                        shutil.copyfileobj(tar_source, output)
                    target.chmod(tar_member.mode & 0o777)
    else:
        target = install_dir / ("plugin.exe" if suffix.endswith(".exe") else "plugin")
        shutil.copy2(archive, target)
        target.chmod(0o755)
