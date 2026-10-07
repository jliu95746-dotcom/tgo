"""Align unchanged default skills while preserving custom prompts."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.service_skill_defaults import (  # noqa: E402
    CHANNEL_FORMAT_INSTRUCTIONS,
    GROUNDED_SUPPORT_INSTRUCTIONS,
    humanization_instructions,
)

OLD_HASHES = {
    "wecom-cn-service-style": (
        "b8599067ecc3bcb982e3427cdc2253bfada75" "ecb3fd50e013793facac372cf2d"
    ),
    "grounded-support-answer": (
        "1bb34d3f3954a43b4c0f046140b8de7632aa" "6b3420b62b301a26af59c85319c7"
    ),
    "humanization": (
        "89ad01347a25def3fafdd1d79bf92a051eeb" "1b54c332518ba0300926ebd0e483"
    ),
}


def aligned_content(name: str, text: str) -> str | None:
    """Return replacement only when the body still matches a known original."""
    parts = text.split("---", 2)
    if len(parts) != 3 or parts[0].strip():
        return None
    body = parts[2].strip()
    key = name
    if "skill_type: humanization" in parts[1]:
        key = "humanization"
        heading, separator, old_body = body.partition("\n")
        if not separator or not heading.startswith("# "):
            return None
        body = old_body.strip()
        replacement = humanization_instructions(heading[2:])
    elif name == "wecom-cn-service-style":
        replacement = CHANNEL_FORMAT_INSTRUCTIONS
    elif name == "grounded-support-answer":
        replacement = GROUNDED_SUPPORT_INSTRUCTIONS
    else:
        return None
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != OLD_HASHES[key]:
        return None
    return "---" + parts[1] + "---\n\n" + replacement


def align_skills(base_dir: Path, backup_dir: Path | None = None) -> list[Path]:
    root = base_dir.resolve(strict=True)
    backup = backup_dir.resolve() if backup_dir is not None else None
    if backup is not None and (backup == root or root in backup.parents):
        raise ValueError("Backups must be outside the skill directory")
    changed: list[Path] = []
    for source in sorted(root.glob("*/*/SKILL.md")):
        if source.is_symlink() or root not in source.resolve().parents:
            continue
        original = source.read_text(encoding="utf-8")
        replacement = aligned_content(source.parent.name, original)
        if replacement is None:
            continue
        changed.append(source.relative_to(root))
        if backup is None:
            continue
        target = backup / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(
                "A prior skill backup must not be overwritten"
            )
        shutil.copy2(source, target)
        temporary = source.with_name(f".{uuid4().hex}.tmp")
        try:
            temporary.write_text(replacement, encoding="utf-8", newline="\n")
            shutil.copymode(source, temporary)
            if source.read_text(encoding="utf-8") != original:
                raise ValueError(
                    "Skill changed concurrently; original was preserved"
                )
            os.replace(temporary, source)
        finally:
            temporary.unlink(missing_ok=True)
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", required=True, type=Path)
    parser.add_argument(
        "--backup-dir",
        type=Path,
        help="Apply with original backups; omit for preview",
    )
    args = parser.parse_args()
    changed = align_skills(args.base_dir, args.backup_dir)
    action = "Updated" if args.backup_dir else "Would update"
    for path in changed:
        print(f"{action}: {path}")
    print(f"{len(changed)} unchanged default skills; custom skills preserved")


if __name__ == "__main__":
    main()
