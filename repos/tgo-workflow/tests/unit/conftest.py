"""Independent synthetic approval keys for unit tests; no local secrets."""

import json

from pydantic import SecretStr
import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def approval_checkpoint_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings,
        "WORKFLOW_APPROVAL_KEYS",
        SecretStr(
            json.dumps(
                {
                    "unit": "synthetic-approval-unit-key-" + "x" * 32,
                }
            )
        ),
    )
    monkeypatch.setattr(settings, "WORKFLOW_APPROVAL_ACTIVE_KEY_ID", "unit")
    monkeypatch.setattr(
        settings,
        "WORKFLOW_EVENT_INPUT_KEYS",
        SecretStr(
            json.dumps({"unit": "synthetic-event-input-unit-key-" + "x" * 32})
        ),
    )
    monkeypatch.setattr(settings, "WORKFLOW_EVENT_INPUT_ACTIVE_KEY_ID", "unit")
