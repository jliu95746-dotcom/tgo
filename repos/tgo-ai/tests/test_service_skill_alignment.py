"""Default prompt alignment backs up originals and preserves custom skills."""

import hashlib
from pathlib import Path

import pytest

from scripts import align_service_skill_defaults as alignment
from app.services.service_skill_defaults import humanization_instructions


def test_alignment_preserves_custom_prompts_and_backs_up_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "skills"
    source = base / "project" / "wecom-cn-service-style" / "SKILL.md"
    source.parent.mkdir(parents=True)
    original_body = "原来的渠道默认指令"
    original = f"---\nname: wecom-cn-service-style\n---\n\n{original_body}\n"
    source.write_text(original, encoding="utf-8")
    custom = base / "other-project" / "wecom-cn-service-style" / "SKILL.md"
    custom.parent.mkdir(parents=True)
    custom_text = original + "\n人工自定义：保持正式称呼。\n"
    custom.write_text(custom_text, encoding="utf-8")
    monkeypatch.setitem(
        alignment.OLD_HASHES,
        "wecom-cn-service-style",
        hashlib.sha256(original_body.encode()).hexdigest(),
    )

    assert alignment.align_skills(base) == [source.relative_to(base)]
    assert source.read_text(encoding="utf-8") == original
    backup = tmp_path / "backup"
    assert alignment.align_skills(base, backup) == [source.relative_to(base)]
    assert (backup / source.relative_to(base)).read_text(
        encoding="utf-8"
    ) == original
    assert custom.read_text(encoding="utf-8") == custom_text
    assert alignment.align_skills(base, backup) == []


def test_alignment_does_not_overwrite_a_previous_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "skills"
    source = base / "project" / "grounded-support-answer" / "SKILL.md"
    source.parent.mkdir(parents=True)
    text = "---\nname: grounded-support-answer\n---\n\n原默认内容\n"
    source.write_text(text, encoding="utf-8")
    monkeypatch.setitem(
        alignment.OLD_HASHES,
        "grounded-support-answer",
        hashlib.sha256("原默认内容".encode()).hexdigest(),
    )
    backup = tmp_path / "backup"
    target = backup / source.relative_to(base)
    target.parent.mkdir(parents=True)
    target.write_text("以前的备份", encoding="utf-8")
    with pytest.raises(FileExistsError):
        alignment.align_skills(base, backup)
    assert source.read_text(encoding="utf-8") == text
    assert target.read_text(encoding="utf-8") == "以前的备份"


def test_default_expression_prompt_has_no_unreadable_reference_file() -> None:
    instructions = humanization_instructions("售后客服")
    assert "# 售后客服" in instructions
    assert "程序会提供" in instructions
    assert "approved-examples.md" not in instructions
    assert "业务决策" in instructions
