"""Portable plugin paths; installation IDs never select arbitrary directories."""

import os
import re
import stat
import sys
from pathlib import Path, PureWindowsPath


def validate_plugin_id(plugin_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", plugin_id):
        raise ValueError("Invalid plugin ID")
    if (
        plugin_id.endswith(".")
        or plugin_id.lower() == "temp"
        or plugin_id.lower().endswith("_backup")
        or PureWindowsPath(plugin_id).is_reserved()
    ):
        raise ValueError("Reserved plugin ID")
    return plugin_id


def contained_path(root: Path, relative: str) -> Path:
    """Reject traversal, Windows drives, and existing symlinks/junctions."""
    if PureWindowsPath(relative).drive or relative.startswith(("/", "\\")):
        raise ValueError("Plugin path must be relative")
    parts = relative.replace("\\", "/").split("/")
    if any(
        part == ".."
        or PureWindowsPath(part).is_reserved()
        or ":" in part
        or (part not in ("", ".") and part.endswith((".", " ")))
        for part in parts
    ):
        raise ValueError("Unsafe plugin path")
    resolved_root = root.resolve()
    target = root
    for part in parts:
        target = target / part
        if target.exists() or target.is_symlink():
            info = target.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400
            ):
                raise ValueError("Plugin paths cannot follow links or junctions")
    if not target.resolve().is_relative_to(resolved_root):
        raise ValueError("Plugin path escaped its root")
    return target


def plugin_directory(root: Path, plugin_id: str) -> Path:
    return contained_path(root, validate_plugin_id(plugin_id))


def plugin_python(install_dir: Path, require_venv: bool = False) -> str:
    relative = ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"
    # POSIX venvs intentionally symlink the interpreter to the trusted runtime.
    parent, filename = relative.rsplit("/", 1)
    executable = contained_path(install_dir, parent) / filename
    if executable.is_file():
        return str(executable)
    if require_venv:
        raise ValueError("Plugin virtual environment interpreter is missing")
    return sys.executable
