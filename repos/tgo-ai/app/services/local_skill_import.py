"""Bounded local skill import. Never execute uploaded scripts during import."""

import base64
import binascii
import io
import re
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

import yaml
from agno.skills import LocalSkills

from app.schemas.skill import SkillDetail, SkillImportRequest
from app.services.skill_file_service import (
    SkillConflictError, SkillFileService, _validate_skill_name,
)

MAX_UPLOAD = 10 * 1024 * 1024
MAX_FILE = 1024 * 1024
_RESERVED = re.compile(r'^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)', re.I)


def _safe_path(name: str) -> PurePosixPath:
    if not name or '\\' in name or any(ord(c) < 32 for c in name):
        raise ValueError('技能包包含不安全的文件路径')
    path = PurePosixPath(name)
    if path.is_absolute() or any(
        part in ('.', '..') or any(char in part for char in ':<>"|?*') or part.endswith((' ', '.'))
        or _RESERVED.match(part) for part in name.rstrip('/').split('/')
    ):
        raise ValueError('技能包包含不安全的文件路径')
    return path


def _unpack(data: bytes, filename: str) -> dict[str, bytes]:
    if filename.lower() == 'skill.md':
        if len(data) > MAX_FILE:
            raise ValueError('SKILL.md 不能超过 1 MB')
        return {'SKILL.md': data}
    if not filename.lower().endswith('.zip'):
        raise ValueError('请选择 SKILL.md 或 ZIP 技能包')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            entries = bundle.infolist()
            if len(entries) > 200:
                raise ValueError('技能包最多包含 200 个文件和目录')
            seen: set[str] = set()
            files: dict[str, bytes] = {}
            total = 0
            for entry in entries:
                path = _safe_path(entry.filename)
                key = str(path).casefold()
                mode = entry.external_attr >> 16
                if key in seen or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)) or entry.flag_bits & 1:
                    raise ValueError('技能包包含重复路径、链接或加密文件')
                seen.add(key)
                if entry.is_dir():
                    continue
                total += entry.file_size
                if entry.file_size > MAX_FILE or total > MAX_UPLOAD or len(files) >= 100:
                    raise ValueError('最多 100 个文件，单文件 1 MB，解压后合计 10 MB')
                with bundle.open(entry) as source:
                    content = source.read(MAX_FILE + 1)
                if len(content) > MAX_FILE:
                    raise ValueError('技能文件超过 1 MB')
                files[str(path)] = content
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise ValueError('ZIP 文件损坏、加密或压缩格式不支持') from exc
    roots = [PurePosixPath(path) for path in files if PurePosixPath(path).name == 'SKILL.md']
    if len(roots) != 1 or len(roots[0].parts) > 2:
        raise ValueError('ZIP 必须仅包含一个技能，SKILL.md 位于根目录或一层技能目录内')
    root = roots[0].parent
    result: dict[str, bytes] = {}
    for path, content in files.items():
        try:
            relative = PurePosixPath(path).relative_to(root)
        except ValueError as exc:
            raise ValueError('ZIP 中存在技能目录以外的文件') from exc
        result[str(relative)] = content
    file_paths = {path.casefold() for path in result}
    for path in result:
        if any(str(parent).casefold() in file_paths for parent in PurePosixPath(path).parents):
            raise ValueError('技能包中文件和目录路径冲突')
    return result


def _normalize(document: bytes, display_name: str | None) -> tuple[str, bytes]:
    try:
        text = document.decode('utf-8-sig')
        match = re.match(r'\A---\s*\r?\n(.*?)\r?\n---\s*\r?\n(.*)\Z', text, re.S)
        if not match:
            raise ValueError('SKILL.md 需要 YAML 头部和技能指令正文')
        frontmatter = yaml.safe_load(match[1])
        if not isinstance(frontmatter, dict):
            raise ValueError('SKILL.md 的 YAML 头部必须是字段对象')
        name = frontmatter.get('name')
        description = frontmatter.get('description')
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError('SKILL.md 需要有效的 name（最多 100 字）')
        if not isinstance(description, str) or not description.strip() or len(description) > 1024 or not match[2].strip():
            raise ValueError('SKILL.md 需要 description（最多 1024 字）和非空指令正文')
        original_name = name
        try:
            _validate_skill_name(name)
        except ValueError:
            name = f'skill-{uuid4().hex[:16]}'
        metadata = frontmatter.get('metadata') or {}
        if not isinstance(metadata, dict):
            raise ValueError('metadata 必须是字段对象')
        for key in list(frontmatter):
            if key not in {'name', 'description', 'license', 'allowed-tools', 'compatibility', 'metadata'}:
                metadata[key] = frontmatter.pop(key)
        label = (display_name or metadata.get('display_name') or original_name)
        if not isinstance(label, str) or not label.strip() or len(label) > 100:
            raise ValueError('显示名称必须为 1–100 字')
        # Local imports are ordinary instructions, not published training libraries.
        metadata['display_name'] = label.strip()
        metadata['skill_type'] = 'standard'
        frontmatter['metadata'] = metadata
        frontmatter['name'] = name
        output = '---\n' + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False) + '---\n' + match[2]
        return name, output.encode('utf-8')
    except (UnicodeError, yaml.YAMLError, RecursionError) as exc:
        raise ValueError('SKILL.md 必须使用 UTF-8 编码和有效 YAML') from exc


async def import_local_skill(service: SkillFileService, project_id: str, data: SkillImportRequest) -> SkillDetail:
    project_id = str(UUID(project_id))
    try:
        content = base64.b64decode(data.content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError('上传文件编码无效') from exc
    if len(content) > MAX_UPLOAD:
        raise ValueError('上传文件不能超过 10 MB')
    files = _unpack(content, data.filename)
    name, files['SKILL.md'] = _normalize(files['SKILL.md'], data.display_name)
    project_dir = service._project_dir(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)
    final_dir = project_dir / name
    if final_dir.exists() or (service.official_dir / name).exists():
        raise SkillConflictError(f'技能标识 {name} 已存在，请修改 SKILL.md 中的 name 后重新导入')
    with tempfile.TemporaryDirectory(prefix='.import-', dir=project_dir) as temporary:
        staging = Path(temporary)
        payload = staging / name
        payload.mkdir()
        for relative, contents in files.items():
            target = payload / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents)
        try:
            if not LocalSkills(str(payload)).load():
                raise ValueError('技能无法被 AI 运行时识别')
            service._parse_skill_detail(payload)
        except Exception as exc:
            raise ValueError(f'技能格式无法加载：{exc}') from exc
        # Synchronous publication has no await between disabling and rename.
        disabled = service.get_disabled_skills(project_id)
        disabled.add(name)
        service._save_disabled_skills(project_id, disabled)
        try:
            payload.rename(final_dir)
        except FileExistsError as exc:
            raise SkillConflictError(f'技能标识 {name} 已存在') from exc
    return await service.get_skill(project_id, name)
