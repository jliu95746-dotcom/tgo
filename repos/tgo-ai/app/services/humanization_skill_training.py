"""Pending training storage for humanization skills.

Pending corrections live outside skill directories so LocalSkills cannot see
them before an administrator explicitly publishes an update.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Dict, List

from app.schemas.skill import HumanizationTrainingSampleRequest


_TRAINING_LOCK = Lock()
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_CN_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def _redact_customer_data(value: str) -> str:
    redacted = _EMAIL_RE.sub("[邮箱]", value)
    return _CN_PHONE_RE.sub("[手机号]", redacted)


class HumanizationTrainingStore:
    """Append, inspect and publish staff correction pairs."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def _pending_dir(self, project_id: str) -> Path:
        return self.base_dir / project_id / ".humanization-training"

    def _pending_path(self, project_id: str, skill_name: str) -> Path:
        return self._pending_dir(project_id) / f"{skill_name}.jsonl"

    def list_pending(self, project_id: str, skill_name: str) -> List[Dict[str, str]]:
        path = self._pending_path(project_id, skill_name)
        if not path.exists():
            return []
        samples: List[Dict[str, str]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                value = json.loads(line)
                if isinstance(value, dict):
                    samples.append({str(k): str(v) for k, v in value.items()})
        except (OSError, json.JSONDecodeError):
            return []
        return samples

    def pending_count(self, project_id: str, skill_name: str) -> int:
        return len(self.list_pending(project_id, skill_name))

    def append(
        self,
        project_id: str,
        skill_name: str,
        sample: HumanizationTrainingSampleRequest,
    ) -> int:
        if sample.ai_draft.strip() == sample.final_reply.strip():
            raise ValueError("Unchanged AI drafts are not correction samples")

        path = self._pending_path(project_id, skill_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "customer_message": _redact_customer_data(sample.customer_message.strip()),
            "ai_draft": _redact_customer_data(sample.ai_draft.strip()),
            "final_reply": _redact_customer_data(sample.final_reply.strip()),
            "source_message_id": sample.source_message_id or "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        with _TRAINING_LOCK:
            existing = self.list_pending(project_id, skill_name)
            source_message_id = payload["source_message_id"]
            if source_message_id and any(
                item.get("source_message_id") == source_message_id for item in existing
            ):
                return len(existing)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            return len(existing) + 1

    def consume(self, project_id: str, skill_name: str) -> List[Dict[str, str]]:
        path = self._pending_path(project_id, skill_name)
        with _TRAINING_LOCK:
            samples = self.list_pending(project_id, skill_name)
            if path.exists():
                path.unlink()
            return samples

    def delete(self, project_id: str, skill_name: str) -> None:
        path = self._pending_path(project_id, skill_name)
        with _TRAINING_LOCK:
            if path.exists():
                path.unlink()

    @staticmethod
    def render_approved_examples(samples: List[Dict[str, str]]) -> str:
        sections = ["# 已确认的人工修正样本", ""]
        for index, sample in enumerate(samples, start=1):
            sections.extend(
                [
                    f"## 样本 {index}",
                    "",
                    f"- 客户：{sample.get('customer_message', '')}",
                    f"- AI 草稿：{sample.get('ai_draft', '')}",
                    f"- 人工发送：{sample.get('final_reply', '')}",
                    "",
                ]
            )
        return "\n".join(sections).rstrip() + "\n"
