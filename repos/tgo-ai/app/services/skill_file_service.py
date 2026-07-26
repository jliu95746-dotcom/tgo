"""File-system-based skill CRUD service.

Each skill is a directory containing a SKILL.md file (with YAML frontmatter)
plus optional ``scripts/`` and ``references/`` sub-directories.

Directory layout::

    {base_dir}/
    ├── _official/          # Global read-only skills shared across all projects
    │   └── code-review/
    │       └── SKILL.md
    ├── {project_id}/       # Project-private skills
    │   └── my-skill/
    │       ├── SKILL.md
    │       ├── scripts/
    │       └── references/
    └── ...
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional, Set
from uuid import uuid4

import yaml

from app.schemas.skill import (
    HumanizationSkillCreateRequest,
    HumanizationTrainingApplyResponse,
    HumanizationTrainingSampleRequest,
    HumanizationTrainingStatus,
    SkillCreateRequest,
    SkillDetail,
    SkillSummary,
    SkillUpdateRequest,
)
from app.services.humanization_skill_training import HumanizationTrainingStore

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class SkillNotFoundError(Exception):
    """Raised when a requested skill directory does not exist."""


class SkillConflictError(Exception):
    """Raised when a skill directory already exists (duplicate creation)."""


class SkillReadOnlyError(Exception):
    """Raised when attempting to mutate an official (read-only) skill."""


class SkillPathTraversalError(Exception):
    """Raised when path validation detects a traversal attempt."""


# ---------------------------------------------------------------------------
# Skill name validation
# ---------------------------------------------------------------------------

_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
_MAX_SKILL_NAME_LEN = 64


def _validate_skill_name(name: str) -> str:
    """Validate that *name* matches the naming convention.

    Rules: lowercase + digits + single hyphens, 2-64 chars, no consecutive
    hyphens, no leading/trailing hyphens.
    """
    if len(name) < 2 or len(name) > _MAX_SKILL_NAME_LEN:
        raise ValueError(
            f"Skill name must be 2-{_MAX_SKILL_NAME_LEN} characters, got {len(name)}"
        )
    if not _SKILL_NAME_RE.match(name):
        raise ValueError(
            f"Invalid skill name '{name}': must be lowercase letters, digits, "
            "and single hyphens (no leading/trailing hyphens)"
        )
    if "--" in name:
        raise ValueError("Consecutive hyphens are not allowed in skill names")
    return name


# ---------------------------------------------------------------------------
# SKILL.md parsing / serialization helpers
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a SKILL.md file into (frontmatter_dict, markdown_body).

    The frontmatter is delimited by ``---`` lines at the very start of the
    file.  If there is no frontmatter the entire text is returned as the
    body.
    """
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    try:
        fm: dict = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        fm = {}

    body = parts[2].lstrip("\n")
    return fm, body


def _serialize_skill_md(
    fm: dict,
    body: str,
) -> str:
    """Serialize frontmatter dict + body back into SKILL.md format."""
    fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False).rstrip("\n")
    return f"---\n{fm_str}\n---\n\n{body}\n"


# ---------------------------------------------------------------------------
# SkillFileService
# ---------------------------------------------------------------------------


class SkillFileService:
    """File-system-based skill CRUD service."""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir)
        self.official_dir = self.base_dir / "_official"
        self.training_store = HumanizationTrainingStore(self.base_dir)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _project_dir(self, project_id: str) -> Path:
        return self.base_dir / project_id

    def _skill_dir(self, project_id: str, skill_name: str) -> Path:
        """Return the path for a *project-private* skill, with traversal check."""
        safe_name = _validate_skill_name(skill_name)
        path = self._project_dir(project_id) / safe_name
        # Prevent directory-traversal attacks
        try:
            resolved = path.resolve()
            base_resolved = self.base_dir.resolve()
            if not str(resolved).startswith(str(base_resolved)):
                raise SkillPathTraversalError(f"Path traversal detected: {skill_name}")
        except (OSError, ValueError) as exc:
            raise SkillPathTraversalError(f"Invalid path: {exc}") from exc
        return path

    def _resolve_skill_dir(self, project_id: str, skill_name: str) -> Path:
        """Look up a skill directory: project-private first, then _official."""
        safe_name = _validate_skill_name(skill_name)
        project_path = self._project_dir(project_id) / safe_name
        if project_path.exists() and project_path.is_dir():
            return project_path
        official_path = self.official_dir / safe_name
        if official_path.exists() and official_path.is_dir():
            return official_path
        raise SkillNotFoundError(f"Skill '{skill_name}' not found")

    def _is_official(self, skill_dir: Path) -> bool:
        """Return True if *skill_dir* lives under the _official tree."""
        try:
            return str(skill_dir.resolve()).startswith(str(self.official_dir.resolve()))
        except (OSError, ValueError):
            return False

    # ------------------------------------------------------------------
    # Disabled-skills state management
    # ------------------------------------------------------------------

    _DISABLED_FILE = ".disabled_skills.json"

    def _disabled_file_path(self, project_id: str) -> Path:
        """Return the path to the disabled-skills JSON file for a project."""
        return self._project_dir(project_id) / self._DISABLED_FILE

    def _load_disabled_skills(self, project_id: str) -> Set[str]:
        """Load the set of disabled skill names for a project."""
        path = self._disabled_file_path(project_id)
        if not path.exists():
            return set()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return set(data)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Cannot read disabled skills file %s: %s", path, exc)
        return set()

    def _save_disabled_skills(self, project_id: str, disabled: Set[str]) -> None:
        """Persist the set of disabled skill names for a project."""
        path = self._disabled_file_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(sorted(disabled), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def toggle_skill(
        self, project_id: str, skill_name: str, enabled: bool
    ) -> bool:
        """Set the enabled/disabled state of a skill. Returns the new state."""
        # Validate skill exists
        _validate_skill_name(skill_name)
        self._resolve_skill_dir(project_id, skill_name)

        disabled = self._load_disabled_skills(project_id)
        if enabled:
            disabled.discard(skill_name)
        else:
            disabled.add(skill_name)
        self._save_disabled_skills(project_id, disabled)
        return enabled

    def get_disabled_skills(self, project_id: str) -> Set[str]:
        """Public accessor for the set of disabled skill names."""
        return self._load_disabled_skills(project_id)

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    async def list_skills(self, project_id: str) -> List[SkillSummary]:
        """List all skills visible to a project (private + official)."""
        skills: List[SkillSummary] = []
        disabled = self._load_disabled_skills(project_id)

        # 1. Project-private skills
        project_dir = self._project_dir(project_id)
        if project_dir.exists():
            for child in sorted(project_dir.iterdir()):
                if child.is_dir() and (child / "SKILL.md").exists():
                    summary = self._parse_skill_summary(child, is_official=False)
                    if summary is not None:
                        summary.enabled = child.name not in disabled
                        if summary.skill_type == "humanization":
                            summary.pending_training_count = (
                                self.training_store.pending_count(
                                    project_id, child.name
                                )
                            )
                        skills.append(summary)

        # 2. Official (global) skills
        if self.official_dir.exists():
            for child in sorted(self.official_dir.iterdir()):
                if child.is_dir() and (child / "SKILL.md").exists():
                    summary = self._parse_skill_summary(child, is_official=True)
                    if summary is not None:
                        summary.enabled = child.name not in disabled
                        skills.append(summary)

        return skills

    async def get_skill(self, project_id: str, skill_name: str) -> SkillDetail:
        """Read a skill's full detail (frontmatter + body + file list)."""
        skill_dir = self._resolve_skill_dir(project_id, skill_name)
        detail = self._parse_skill_detail(skill_dir)
        detail.enabled = skill_name not in self._load_disabled_skills(project_id)
        if detail.skill_type == "humanization":
            detail.pending_training_count = self.training_store.pending_count(
                project_id, skill_name
            )
        return detail

    async def create_skill(
        self, project_id: str, data: SkillCreateRequest
    ) -> SkillDetail:
        """Create a new project-private skill directory with SKILL.md."""
        skill_dir = self._skill_dir(project_id, data.name)
        if skill_dir.exists():
            raise SkillConflictError(f"Skill '{data.name}' already exists")

        # Create directory structure
        skill_dir.mkdir(parents=True, exist_ok=False)

        # Write SKILL.md
        self._write_skill_md(skill_dir, data)

        # Write optional sub-files
        if data.scripts:
            self._write_files(skill_dir / "scripts", data.scripts)
        if data.references:
            self._write_files(skill_dir / "references", data.references)

        detail = self._parse_skill_detail(skill_dir)
        detail.enabled = data.name not in self._load_disabled_skills(project_id)
        return detail

    async def create_humanization_skill(
        self,
        project_id: str,
        data: HumanizationSkillCreateRequest,
    ) -> SkillDetail:
        """Create a trainable skill that stays outside global skill loading."""
        skill_name = data.name or f"humanization-{uuid4().hex[:8]}"
        instructions = f"""# {data.display_name}

将客户回复改写得自然、简洁，像真实客服在聊天。

## 规则

- 直接回应客户，不描述分析、查询、工具调用或内部工作过程。
- 保留订单、价格、政策、时效等业务事实，不根据表达样本发明事实。
- 优先使用短句和口语化表达，避免模板化标题、总结和重复复述。
- `references/approved-examples.md` 存在时，参考其中人工确认的最终表达，但不要照搬其中的客户信息。
"""
        detail = await self.create_skill(
            project_id,
            SkillCreateRequest(
                name=skill_name,
                description=data.description,
                instructions=instructions,
                tags=["customer-service", "humanization"],
                metadata={
                    "skill_type": "humanization",
                    "display_name": data.display_name,
                    "published_version": "1",
                },
            ),
        )
        await self.toggle_skill(project_id, skill_name, False)
        detail.enabled = False
        detail.skill_type = "humanization"
        detail.display_name = data.display_name
        return detail

    async def update_skill(
        self, project_id: str, skill_name: str, data: SkillUpdateRequest
    ) -> SkillDetail:
        """Update SKILL.md content for a project-private skill."""
        skill_dir = self._resolve_skill_dir(project_id, skill_name)

        if self._is_official(skill_dir):
            raise SkillReadOnlyError(
                f"Cannot modify official skill '{skill_name}'"
            )

        # Merge update into existing frontmatter
        self._write_skill_md(skill_dir, data, merge=True)
        detail = self._parse_skill_detail(skill_dir)
        detail.enabled = skill_name not in self._load_disabled_skills(project_id)
        if detail.skill_type == "humanization":
            detail.pending_training_count = self.training_store.pending_count(
                project_id, skill_name
            )
        return detail

    async def add_humanization_training_sample(
        self,
        project_id: str,
        skill_name: str,
        data: HumanizationTrainingSampleRequest,
    ) -> HumanizationTrainingStatus:
        detail = await self.get_skill(project_id, skill_name)
        if detail.skill_type != "humanization":
            raise ValueError(f"Skill '{skill_name}' is not a humanization skill")
        pending_count = self.training_store.append(project_id, skill_name, data)
        return HumanizationTrainingStatus(
            name=skill_name,
            pending_training_count=pending_count,
            published_version=detail.published_version,
        )

    async def apply_humanization_training(
        self,
        project_id: str,
        skill_name: str,
    ) -> HumanizationTrainingApplyResponse:
        detail = await self.get_skill(project_id, skill_name)
        if detail.skill_type != "humanization":
            raise ValueError(f"Skill '{skill_name}' is not a humanization skill")

        samples = self.training_store.list_pending(project_id, skill_name)
        if not samples:
            return HumanizationTrainingApplyResponse(
                name=skill_name,
                applied_count=0,
                pending_training_count=0,
                published_version=detail.published_version,
            )

        skill_dir = self._skill_dir(project_id, skill_name)
        examples_path = skill_dir / "references" / "approved-examples.md"
        examples_path.parent.mkdir(parents=True, exist_ok=True)
        examples_existed = examples_path.exists()
        previous_examples = (
            examples_path.read_text(encoding="utf-8")
            if examples_existed
            else ""
        )
        existing = previous_examples.rstrip()
        rendered = self.training_store.render_approved_examples(samples)
        next_version = detail.published_version + 1
        if existing:
            batch = rendered.replace(
                "# 已确认的人工修正样本",
                f"## 训练批次 v{next_version}",
                1,
            )
            content = f"{existing}\n\n{batch}"
        else:
            content = rendered
        examples_path.write_text(content, encoding="utf-8")

        try:
            await self.update_skill(
                project_id,
                skill_name,
                SkillUpdateRequest(
                    metadata={"published_version": str(next_version)}
                ),
            )
        except Exception:
            if examples_existed:
                examples_path.write_text(previous_examples, encoding="utf-8")
            elif examples_path.exists():
                examples_path.unlink()
            raise
        self.training_store.delete(project_id, skill_name)
        return HumanizationTrainingApplyResponse(
            name=skill_name,
            applied_count=len(samples),
            pending_training_count=0,
            published_version=next_version,
        )

    async def delete_skill(self, project_id: str, skill_name: str) -> None:
        """Delete a project-private skill directory entirely."""
        skill_dir = self._skill_dir(project_id, skill_name)
        if not skill_dir.exists():
            raise SkillNotFoundError(f"Skill '{skill_name}' not found")

        if self._is_official(skill_dir):
            raise SkillReadOnlyError(
                f"Cannot delete official skill '{skill_name}'"
            )

        shutil.rmtree(skill_dir)
        self.training_store.delete(project_id, skill_name)
        disabled = self._load_disabled_skills(project_id)
        if skill_name in disabled:
            disabled.discard(skill_name)
            self._save_disabled_skills(project_id, disabled)

    # ------------------------------------------------------------------
    # Sub-file CRUD
    # ------------------------------------------------------------------

    async def get_file(
        self, project_id: str, skill_name: str, file_path: str
    ) -> str:
        """Read a sub-file (script / reference) content as text."""
        skill_dir = self._resolve_skill_dir(project_id, skill_name)
        target = (skill_dir / file_path).resolve()
        # Path traversal check
        if not str(target).startswith(str(skill_dir.resolve())):
            raise SkillPathTraversalError(f"Invalid file path: {file_path}")
        if not target.is_file():
            raise SkillNotFoundError(
                f"File '{file_path}' not found in skill '{skill_name}'"
            )
        return target.read_text(encoding="utf-8")

    async def put_file(
        self,
        project_id: str,
        skill_name: str,
        file_path: str,
        content: str,
    ) -> None:
        """Create or update a sub-file inside a project-private skill."""
        skill_dir = self._resolve_skill_dir(project_id, skill_name)

        if self._is_official(skill_dir):
            raise SkillReadOnlyError(
                f"Cannot modify files in official skill '{skill_name}'"
            )

        target = (skill_dir / file_path).resolve()
        if not str(target).startswith(str(skill_dir.resolve())):
            raise SkillPathTraversalError(f"Invalid file path: {file_path}")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    async def delete_file(
        self, project_id: str, skill_name: str, file_path: str
    ) -> None:
        """Delete a sub-file from a project-private skill."""
        skill_dir = self._resolve_skill_dir(project_id, skill_name)

        if self._is_official(skill_dir):
            raise SkillReadOnlyError(
                f"Cannot delete files in official skill '{skill_name}'"
            )

        target = (skill_dir / file_path).resolve()
        if not str(target).startswith(str(skill_dir.resolve())):
            raise SkillPathTraversalError(f"Invalid file path: {file_path}")
        if not target.is_file():
            raise SkillNotFoundError(
                f"File '{file_path}' not found in skill '{skill_name}'"
            )
        target.unlink()

    # ------------------------------------------------------------------
    # Internal parsing helpers
    # ------------------------------------------------------------------

    def _parse_skill_summary(
        self, skill_dir: Path, *, is_official: bool
    ) -> Optional[SkillSummary]:
        """Parse SKILL.md frontmatter into a SkillSummary, or None on error."""
        skill_md = skill_dir / "SKILL.md"
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", skill_md, exc)
            return None

        fm, _ = _parse_frontmatter(text)
        meta = fm.get("metadata") or {}

        # Determine updated_at from file modification time
        try:
            stat = skill_md.stat()
            updated_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        except OSError:
            updated_at = None

        skill_type: Literal["standard", "humanization"] = (
            "humanization"
            if meta.get("skill_type") == "humanization"
            else "standard"
        )
        try:
            published_version = max(1, int(meta.get("published_version", 1)))
        except (TypeError, ValueError):
            published_version = 1

        return SkillSummary(
            name=fm.get("name", skill_dir.name),
            description=fm.get("description", ""),
            author=meta.get("author"),
            is_official=is_official,
            is_featured=meta.get("is_featured", False),
            tags=meta.get("tags", []),
            updated_at=updated_at,
            skill_type=skill_type,
            display_name=meta.get("display_name"),
            published_version=published_version,
        )

    def _parse_skill_detail(self, skill_dir: Path) -> SkillDetail:
        """Parse full SKILL.md + enumerate sub-files → SkillDetail."""
        skill_md = skill_dir / "SKILL.md"
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillNotFoundError(
                f"Cannot read SKILL.md in '{skill_dir.name}': {exc}"
            ) from exc

        fm, body = _parse_frontmatter(text)
        meta = fm.get("metadata") or {}

        try:
            stat = skill_md.stat()
            updated_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        except OSError:
            updated_at = None

        is_official = self._is_official(skill_dir)

        # Collect sub-file listings
        scripts = self._list_relative_files(skill_dir / "scripts")
        references = self._list_relative_files(skill_dir / "references")

        skill_type: Literal["standard", "humanization"] = (
            "humanization"
            if meta.get("skill_type") == "humanization"
            else "standard"
        )
        try:
            published_version = max(1, int(meta.get("published_version", 1)))
        except (TypeError, ValueError):
            published_version = 1

        return SkillDetail(
            name=fm.get("name", skill_dir.name),
            description=fm.get("description", ""),
            author=meta.get("author"),
            is_official=is_official,
            is_featured=meta.get("is_featured", False),
            tags=meta.get("tags", []),
            updated_at=updated_at,
            instructions=body,
            license=fm.get("license"),
            version=meta.get("version"),
            metadata={
                k: str(v)
                for k, v in meta.items()
                if k
                not in (
                    "author",
                    "version",
                    "tags",
                    "is_featured",
                    "skill_type",
                    "display_name",
                    "published_version",
                )
            },
            scripts=scripts,
            references=references,
            skill_type=skill_type,
            display_name=meta.get("display_name"),
            published_version=published_version,
        )

    # ------------------------------------------------------------------
    # Internal write helpers
    # ------------------------------------------------------------------

    def _write_skill_md(
        self,
        skill_dir: Path,
        data: SkillCreateRequest | SkillUpdateRequest,
        *,
        merge: bool = False,
    ) -> None:
        """Write (or merge-update) SKILL.md from schema data."""
        skill_md = skill_dir / "SKILL.md"

        if merge and skill_md.exists():
            existing_text = skill_md.read_text(encoding="utf-8")
            fm, body = _parse_frontmatter(existing_text)
        else:
            fm = {}
            body = ""

        # Update frontmatter fields
        if hasattr(data, "name") and getattr(data, "name", None) is not None:
            fm["name"] = data.name
        if data.description is not None:
            fm["description"] = data.description
        if hasattr(data, "license") and getattr(data, "license", None) is not None:
            fm["license"] = data.license

        # Update metadata sub-dict
        meta = fm.get("metadata") or {}
        if hasattr(data, "author") and getattr(data, "author", None) is not None:
            meta["author"] = data.author
        if data.tags is not None:
            meta["tags"] = data.tags
        if hasattr(data, "is_featured") and getattr(data, "is_featured", None) is not None:
            meta["is_featured"] = data.is_featured
        metadata = getattr(data, "metadata", None)
        if metadata is not None:
            for k, v in metadata.items():
                meta[k] = v
        if meta:
            fm["metadata"] = meta

        # Update body
        if data.instructions is not None:
            body = data.instructions

        skill_md.write_text(
            _serialize_skill_md(fm, body),
            encoding="utf-8",
        )

    @staticmethod
    def _write_files(parent_dir: Path, files: Dict[str, str]) -> None:
        """Write multiple files under *parent_dir*."""
        parent_dir.mkdir(parents=True, exist_ok=True)
        for filename, content in files.items():
            # Basic filename safety
            safe = Path(filename).name
            (parent_dir / safe).write_text(content, encoding="utf-8")

    @staticmethod
    def _list_relative_files(directory: Path) -> List[str]:
        """List files recursively under *directory* as relative paths."""
        if not directory.exists() or not directory.is_dir():
            return []
        result: List[str] = []
        prefix = str(directory.name)
        for root, _dirs, files in os.walk(directory):
            for f in sorted(files):
                full = Path(root) / f
                rel = full.relative_to(directory.parent)
                result.append(str(rel))
        return sorted(result)
