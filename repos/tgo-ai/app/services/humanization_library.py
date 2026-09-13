"""Published style library: review snapshots, atomic releases and scenario retrieval."""

import hashlib
import json
import os
from pathlib import Path
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel, Field

from app.schemas.humanization import (
    ConversationTurn,
    HumanizationContext,
    TrainingExample,
    TrainingPublishRequest,
    TrainingReview,
)
from app.services.humanization_matching import match_examples

_RELEASE_LOCK = RLock()


class StyleRelease(BaseModel):
    version: int = 1
    examples: list[TrainingExample] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    consumed_ids: list[str] = Field(default_factory=list)


def example_id(sample: dict[str, object]) -> str:
    identity = [
        sample.get(k, "")
        for k in ("source_message_id", "customer_message", "ai_draft", "final_reply")
    ]
    if sample.get("delivery_id"):
        identity.append(sample["delivery_id"])
    return hashlib.sha256(
        json.dumps(identity, ensure_ascii=False).encode()
    ).hexdigest()[:24]


class HumanizationLibrary:
    def __init__(self, skill_dir: Path):
        self.path = skill_dir / "references" / "style-library.json"

    def read(self, fallback_version: int = 1) -> StyleRelease:
        if not self.path.exists():
            return StyleRelease(version=fallback_version)
        return StyleRelease.model_validate_json(self.path.read_text(encoding="utf-8"))

    def pending_examples(
        self, pending: list[dict[str, object]], release: StyleRelease
    ) -> list[TrainingExample]:
        samples = []
        for data in pending:
            sample_id = example_id(data)
            if sample_id not in release.consumed_ids:
                payload = {
                    k: v
                    for k, v in data.items()
                    if k in TrainingExample.model_fields and k != "id"
                }
                samples.append(
                    TrainingExample.model_validate({"id": sample_id, **payload})
                )
        return samples

    def review(
        self, name: str, pending: list[dict[str, object]], fallback_version: int = 1
    ) -> TrainingReview:
        release = self.read(fallback_version)
        examples = self.pending_examples(pending, release)
        snapshot = hashlib.sha256(
            json.dumps(
                {
                    "version": release.version,
                    "pending": [e.model_dump() for e in examples],
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        return TrainingReview(
            name=name,
            published_version=release.version,
            snapshot_id=snapshot,
            pending=examples,
            published=release.examples,
            rules=release.rules,
        )

    def candidate(
        self, review: TrainingReview, request: TrainingPublishRequest
    ) -> StyleRelease:
        if request.snapshot_id != review.snapshot_id:
            raise ValueError("Training changed; refresh preview before publishing")
        originals = {e.id: e for e in review.pending}
        if len({e.id for e in request.samples}) != len(request.samples):
            raise ValueError("Duplicate training sample")
        examples = list(review.published)
        rules = list(review.rules)
        consumed = list(self.read(review.published_version).consumed_ids)
        for candidate in request.samples:
            if candidate.change_kind == "review":
                raise ValueError("Unreviewed samples must remain pending for review")
            original = originals.get(candidate.id)
            if original is None:
                raise ValueError("Training sample does not belong to this snapshot")
            if any(
                getattr(candidate, field) != getattr(original, field)
                for field in (
                    "customer_message",
                    "ai_draft",
                    "final_reply",
                    "source_message_id",
                )
            ):
                raise ValueError("Training source content cannot be changed")
            consumed.append(candidate.id)
            # Fact/mixed edits remain review history, never become phrase examples.
            if candidate.selected and candidate.change_kind == "expression":
                examples.append(candidate)
                rules.extend(candidate.rules)
        return StyleRelease(
            version=review.published_version + 1,
            examples=examples,
            rules=list(dict.fromkeys(r.strip() for r in rules if r.strip())),
            consumed_ids=list(dict.fromkeys(consumed)),
        )

    def publish(
        self, review: TrainingReview, request: TrainingPublishRequest
    ) -> StyleRelease:
        release = self.candidate(review, request)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(f".{uuid4().hex}.tmp")
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(release.model_dump_json(indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        finally:
            if temp.exists():
                temp.unlink()
        return release

    @staticmethod
    def context(
        name: str,
        instructions: str,
        release: StyleRelease,
        query: str,
        factual_draft: str = "",
        recent_messages: list[ConversationTurn] | None = None,
    ) -> HumanizationContext:
        examples = match_examples(
            release.examples, query, factual_draft, recent_messages
        )
        rules = list(
            dict.fromkeys(rule for example in examples for rule in example.rules)
        )
        return HumanizationContext(
            name=name,
            published_version=release.version,
            instructions=instructions,
            rules=rules,
            examples=examples,
        )
