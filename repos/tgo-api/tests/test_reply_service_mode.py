"""Publication checks always read the current tenant-scoped service mode."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models import Platform, Visitor
from app.services import reply_service_mode
from app.services.ai_reply_control import ReplyStopped


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode, disabled, blocked",
    [
        ("auto", False, False),
        ("manual", True, True),
        ("assist", True, True),
        ("auto", True, True),
        (None, None, False),
    ],
)
async def test_publication_respects_current_mode(
    monkeypatch, mode, disabled, blocked
):
    project_id, visitor_id = uuid4(), uuid4()
    visitor = SimpleNamespace(
        project_id=project_id,
        platform_id=uuid4(),
        deleted_at=None,
        service_mode=mode,
        ai_disabled=disabled,
    )
    platform = SimpleNamespace(
        project_id=project_id, deleted_at=None, ai_mode="auto"
    )
    install_session(monkeypatch, visitor, platform)
    if blocked:
        with pytest.raises(ReplyStopped):
            await reply_service_mode.ensure_customer_auto_reply(
                str(project_id), str(visitor_id)
            )
    else:
        await reply_service_mode.ensure_customer_auto_reply(
            str(project_id), str(visitor_id)
        )


def install_session(monkeypatch, visitor, platform):
    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get(self, model, _identity):
            return (
                visitor
                if model is Visitor
                else platform
                if model is Platform
                else None
            )

    monkeypatch.setattr(reply_service_mode, "SessionLocal", Session)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", ["missing", "deleted", "foreign_visitor", "foreign_platform"]
)
async def test_missing_or_foreign_context_cannot_publish(monkeypatch, invalid):
    project_id = uuid4()
    visitor = SimpleNamespace(
        project_id=project_id,
        platform_id=uuid4(),
        deleted_at=None,
        service_mode="auto",
        ai_disabled=False,
    )
    platform = SimpleNamespace(
        project_id=project_id, deleted_at=None, ai_mode="auto"
    )
    if invalid == "deleted":
        visitor.deleted_at = object()
    elif invalid == "foreign_visitor":
        visitor.project_id = uuid4()
    elif invalid == "foreign_platform":
        platform.project_id = uuid4()
    install_session(
        monkeypatch, None if invalid == "missing" else visitor, platform
    )
    with pytest.raises(ReplyStopped):
        await reply_service_mode.ensure_customer_auto_reply(
            str(project_id), str(uuid4())
        )
