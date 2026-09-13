import base64
import io
import zipfile
from uuid import uuid4

import pytest
from agno.skills import LocalSkills

from app.schemas.skill import SkillImportRequest, SkillUpdateRequest
from app.services.local_skill_import import import_local_skill, _safe_path
from app.services.skill_file_service import SkillConflictError, SkillFileService


DOCUMENT = b'---\nname: local-demo\ndescription: A local test skill\n---\nAnswer concisely.\n'


def request(content=DOCUMENT, filename='SKILL.md', **kwargs):
    return SkillImportRequest(filename=filename, content_base64=base64.b64encode(content).decode(), **kwargs)


def archive(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as bundle:
        for name, content in files:
            bundle.writestr(name, content)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_import_disabled_rename_preserves_identifier(tmp_path):
    service = SkillFileService(str(tmp_path))
    project = str(uuid4())
    result = await import_local_skill(service, project, request(display_name='价格咨询'))
    assert result.name == 'local-demo'
    assert result.display_name == '价格咨询'
    assert result.enabled is False
    assert LocalSkills(str(tmp_path / project / result.name)).load()
    updated = await service.update_skill(project, result.name, SkillUpdateRequest(display_name='产品咨询'))
    assert updated.name == result.name
    assert updated.display_name == '产品咨询'
    with pytest.raises(SkillConflictError):
        await import_local_skill(service, project, request())
    assert (await service.get_skill(project, result.name)).display_name == '产品咨询'


@pytest.mark.asyncio
async def test_zip_preserves_resources_and_chinese_name(tmp_path):
    service = SkillFileService(str(tmp_path))
    project = str(uuid4())
    data = archive([('demo/SKILL.md', DOCUMENT.decode().replace('local-demo', '物流追踪')), ('demo/references/说明.md', '说明')])
    result = await import_local_skill(service, project, request(data, 'demo.zip'))
    assert result.display_name == '物流追踪'
    assert result.name.startswith('skill-')
    assert (tmp_path / project / result.name / 'references/说明.md').read_text(encoding='utf-8') == '说明'


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['../outside', '/absolute', 'C:/drive', 'demo/../evil', 'demo/CON.txt', 'demo/a:stream', 'demo/trailing.'])
async def test_reject_unsafe_archive_paths(tmp_path, path):
    data = archive([('demo/SKILL.md', DOCUMENT), (path, b'bad')])
    with pytest.raises(ValueError):
        await import_local_skill(SkillFileService(str(tmp_path)), str(uuid4()), request(data, 'bad.zip'))
    assert not list(tmp_path.rglob('SKILL.md'))


def test_reject_backslash_before_platform_normalization():
    with pytest.raises(ValueError):
        _safe_path('demo\\evil')


@pytest.mark.asyncio
async def test_reject_symlink_and_oversized_entry(tmp_path):
    import stat
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as bundle:
        bundle.writestr('SKILL.md', DOCUMENT)
        link = zipfile.ZipInfo('linked')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(link, '../outside')
    service = SkillFileService(str(tmp_path))
    with pytest.raises(ValueError):
        await import_local_skill(service, str(uuid4()), request(buffer.getvalue(), 'link.zip'))
    with pytest.raises(ValueError):
        await import_local_skill(service, str(uuid4()), request(archive([('SKILL.md', DOCUMENT), ('large.txt', b'x' * (1024 * 1024 + 1))]), 'large.zip'))


@pytest.mark.asyncio
async def test_official_conflict_and_project_isolation(tmp_path):
    service = SkillFileService(str(tmp_path))
    official = service.official_dir / 'local-demo'
    official.mkdir(parents=True)
    (official / 'SKILL.md').write_bytes(DOCUMENT)
    with pytest.raises(SkillConflictError):
        await import_local_skill(service, str(uuid4()), request())
    document = DOCUMENT.replace(b'local-demo', b'private-demo')
    first, second = str(uuid4()), str(uuid4())
    await import_local_skill(service, first, request(document))
    assert not (tmp_path / second / 'private-demo').exists()
    assert 'private-demo' not in {skill.name for skill in await service.list_skills(second)}


@pytest.mark.asyncio
async def test_file_directory_collision(tmp_path):
    files = [('SKILL.md', DOCUMENT), ('refs', b'file'), ('refs/a.md', b'data')]
    with pytest.raises(ValueError):
        await import_local_skill(SkillFileService(str(tmp_path)), str(uuid4()), request(archive(files), 'bad.zip'))


@pytest.mark.asyncio
async def test_api_import_and_collision(tmp_path, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from app.api.v1 import skills
    service = SkillFileService(str(tmp_path))
    monkeypatch.setattr(skills, '_get_skill_service', lambda: service)
    app = FastAPI()
    app.include_router(skills.router, prefix='/skills')
    project = str(uuid4())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        headers = {'X-Project-Id': project}
        response = await client.post('/skills/import', json=request().model_dump(), headers=headers)
        assert response.status_code == 201, response.text
        assert response.json()['enabled'] is False
        renamed = await client.patch('/skills/local-demo', json={'display_name': '中文名称'}, headers=headers)
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()['display_name'] == '中文名称'
        assert renamed.json()['name'] == 'local-demo'
        duplicate = await client.post('/skills/import', json=request().model_dump(), headers=headers)
        assert duplicate.status_code == 409
        invalid = await client.post('/skills/import', json=request(b'invalid').model_dump(), headers=headers)
        assert invalid.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize('files', [ [('SKILL.md', DOCUMENT), ('skill.md', DOCUMENT)], [('a/SKILL.md', DOCUMENT), ('b/SKILL.md', DOCUMENT)], [('demo/SKILL.md', DOCUMENT), ('outside.txt', b'bad')]])
async def test_reject_ambiguous_archives(tmp_path, files):
    with pytest.raises(ValueError):
        await import_local_skill(SkillFileService(str(tmp_path)), str(uuid4()), request(archive(files), 'bad.zip'))


@pytest.mark.asyncio
@pytest.mark.parametrize('content', [b'no frontmatter', b'---\n- list\n---\ntext', b'---\nname: valid-name\n---\ntext', b'---\nname: valid-name\ndescription: desc\n---\n'])
async def test_reject_invalid_skill(tmp_path, content):
    with pytest.raises(ValueError):
        await import_local_skill(SkillFileService(str(tmp_path)), str(uuid4()), request(content))
