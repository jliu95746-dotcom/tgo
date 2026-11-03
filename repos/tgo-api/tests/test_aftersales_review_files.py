"""Only active files uploaded within the approved visitor session are selectable."""

from datetime import UTC, datetime, timedelta
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models import ChatFile, VisitorSession
from app.services.aftersales_review_files import (
    list_review_files, require_intake_evidence_files,
)


@pytest.mark.asyncio
async def test_review_files_reject_other_tenants_channels_and_older_sessions(monkeypatch):
    from app.core.exceptions import TGOAPIException
    from app.services import aftersales_review_files
    from app.services import staff_conversation_scope

    monkeypatch.setattr(staff_conversation_scope.settings, "SAAS_BILLING_ENABLED", False)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    project, other_project, visitor, other_visitor, platform, session_id, staff_id = (
        uuid4() for _ in range(7)
    )
    started = datetime.now(UTC) - timedelta(hours=1)
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE api_projects (id CHAR(32) PRIMARY KEY, deleted_at DATETIME)"
        ))
        connection.execute(text(
            "CREATE TABLE api_visitors (id CHAR(32) PRIMARY KEY, project_id CHAR(32), "
            "platform_id CHAR(32), deleted_at DATETIME, access_retired_at DATETIME)"
        ))
        connection.execute(text(
            "CREATE TABLE api_platforms (id CHAR(32) PRIMARY KEY, project_id CHAR(32), "
            "deleted_at DATETIME, is_active BOOLEAN)"
        ))
        VisitorSession.__table__.create(connection)
        ChatFile.__table__.create(connection)
        connection.execute(text("INSERT INTO api_projects VALUES (:id, NULL)"), {"id": project.hex})
        connection.execute(text("INSERT INTO api_platforms VALUES (:id, :project, NULL, 1)"), {
            "id": platform.hex, "project": project.hex,
        })
        for identity in (visitor, other_visitor):
            connection.execute(text(
                "INSERT INTO api_visitors VALUES (:id, :project, :platform, NULL, NULL)"
            ), {"id": identity.hex, "project": project.hex, "platform": platform.hex})

    with Session(engine) as db:
        db.add(VisitorSession(
            id=session_id, project_id=project, visitor_id=visitor,
            platform_id=platform, status="open", created_at=started,
        ))
        common = {
            "file_path": "chat/test", "file_size": 12, "file_type": "image/png",
            "channel_type": 251, "uploaded_by_staff_id": staff_id,
        }
        valid = ChatFile(
            **common, project_id=project, channel_id=f"{visitor}-vtr",
            file_name="本次照片.png", created_at=started + timedelta(minutes=10),
        )
        db.add(valid)
        db.add(ChatFile(
            **common, project_id=other_project, channel_id=f"{visitor}-vtr",
            file_name="其他企业.png", created_at=started + timedelta(minutes=10),
        ))
        db.add(ChatFile(
            **common, project_id=project, channel_id=f"{other_visitor}-vtr",
            file_name="其他客户.png", created_at=started + timedelta(minutes=10),
        ))
        db.add(ChatFile(
            **common, project_id=project, channel_id=f"{visitor}-vtr",
            file_name="上次会话.png", created_at=started - timedelta(days=1),
        ))
        db.add(ChatFile(
            **common, project_id=project, channel_id=f"{visitor}-vtr",
            file_name="已删除.png", created_at=started + timedelta(minutes=10),
            deleted_at=started + timedelta(minutes=20),
        ))
        db.commit()
        staff = SimpleNamespace(id=staff_id, project_id=project, role="admin")
        rows = await list_review_files(db, staff, session_id)
        assert [row.id for row in rows] == [valid.id]
        assert rows[0].file_name == "本次照片.png"
        target = await aftersales_review_files.review_chat_target(db, staff, session_id)
        assert target.channel_type == 251
        assert target.channel_id == f"{visitor}-vtr"
        assert target.message_seq is None

        source_id = "customer-message-1"
        expected_no = "platform_" + hashlib.sha256(
            f"{visitor}-vtr:{visitor}-vtr:{source_id}".encode("utf-8")
        ).hexdigest()[:32]
        message = SimpleNamespace(
            channel_id=f"{visitor}-vtr", channel_type=251,
            from_uid=f"{visitor}-vtr", client_msg_no=expected_no,
            message_id=123, message_seq=42,
            timestamp=int((started + timedelta(minutes=10)).timestamp()),
            payload={"type": 1, "content": "我要退货", "extra": {"message_id": source_id}},
        )
        lookup = AsyncMock(return_value=message)
        monkeypatch.setattr(
            aftersales_review_files, "wukongim_client",
            SimpleNamespace(enabled=True, get_message_by_client_msg_no=lookup),
            raising=False,
        )
        located = await aftersales_review_files.review_chat_target(
            db, staff, session_id, source_message_id=source_id,
            source_channel="web",
        )
        assert located.message_seq == 42
        lookup.assert_awaited_once_with(
            channel_id=f"{visitor}-vtr", channel_type=251,
            client_msg_no=expected_no, raise_on_error=True,
        )
        execution_id = uuid4()
        get_execution = AsyncMock(return_value={
            "id": str(execution_id), "workflow_id": str(uuid4()),
            "status": "completed", "started_at": started.isoformat(),
            "execution_mode": "production",
            "visitor_session_id": str(session_id),
            "source_message_id": source_id, "source_channel": "web",
        })
        monkeypatch.setattr(
            aftersales_review_files.workflow_client, "get_execution", get_execution,
        )
        located_review = await aftersales_review_files.review_chat_target(
            db, staff, session_id, execution_id=execution_id,
        )
        assert located_review.message_seq == 42
        get_execution.assert_awaited_once_with(str(execution_id), str(project))
        get_execution.return_value["visitor_session_id"] = str(uuid4())
        assert (await aftersales_review_files.review_chat_target(
            db, staff, session_id, execution_id=execution_id,
        )).message_seq is None
        get_execution.return_value["visitor_session_id"] = str(session_id)
        get_execution.side_effect = HTTPException(503, "Workflow unavailable")
        assert (await aftersales_review_files.review_chat_target(
            db, staff, session_id, execution_id=execution_id,
        )).message_seq is None
        get_execution.side_effect = None

        lookup.reset_mock()
        lookup.side_effect = [None, message]
        message.client_msg_no = source_id
        message.payload = {"type": 1, "content": "我要退货"}
        direct = await aftersales_review_files.review_chat_target(
            db, staff, session_id, source_message_id=source_id,
            source_channel="web",
        )
        assert direct.message_seq == 42
        assert [call.kwargs["client_msg_no"] for call in lookup.await_args_list] == [
            expected_no, source_id,
        ]
        lookup.side_effect = None
        message.client_msg_no = expected_no
        message.payload = {
            "type": 1, "content": "我要退货", "extra": {"message_id": source_id},
        }

        message.from_uid = f"{other_visitor}-vtr"
        assert (await aftersales_review_files.review_chat_target(
            db, staff, session_id, source_message_id=source_id,
            source_channel="web",
        )).message_seq is None
        message.from_uid = f"{visitor}-vtr"
        message.timestamp = int((started - timedelta(days=1)).timestamp())
        assert (await aftersales_review_files.review_chat_target(
            db, staff, session_id, source_message_id=source_id,
            source_channel="web",
        )).message_seq is None
        message.timestamp = int((started + timedelta(minutes=10)).timestamp())
        message.payload["extra"]["message_id"] = "unrelated"
        assert (await aftersales_review_files.review_chat_target(
            db, staff, session_id, source_message_id=source_id,
            source_channel="web",
        )).message_seq is None
        message.payload["extra"]["message_id"] = source_id
        lookup.side_effect = RuntimeError("IM unavailable")
        assert (await aftersales_review_files.review_chat_target(
            db, staff, session_id, source_message_id=source_id,
            source_channel="web",
        )).message_seq is None
        lookup.side_effect = None
        await require_intake_evidence_files(db, staff, session_id, [valid.id])
        await require_intake_evidence_files(db, staff, session_id, [])
        with pytest.raises(TGOAPIException) as invalid_file:
            await require_intake_evidence_files(db, staff, session_id, [uuid4()])
        assert invalid_file.value.status_code == 409
        assert await list_review_files(
            db, SimpleNamespace(id=staff_id, project_id=other_project, role="admin"),
            session_id,
        ) == []
        with pytest.raises(TGOAPIException) as foreign:
            await aftersales_review_files.review_chat_target(
                db, SimpleNamespace(id=staff_id, project_id=other_project, role="admin"),
                session_id,
            )
        assert foreign.value.status_code == 404
        monkeypatch.setattr(aftersales_review_files, "check_user_permission", lambda *_: True)
        monkeypatch.setattr(staff_conversation_scope.settings, "SAAS_BILLING_ENABLED", True)
        with pytest.raises(TGOAPIException) as denied:
            await list_review_files(
                db, SimpleNamespace(id=staff_id, project_id=project, role="user"),
                session_id,
            )
        assert denied.value.status_code == 404
        with pytest.raises(TGOAPIException) as denied_target:
            await aftersales_review_files.review_chat_target(
                db, SimpleNamespace(id=staff_id, project_id=project, role="user"),
                session_id,
            )
        assert denied_target.value.status_code == 404
        monkeypatch.setattr(staff_conversation_scope.settings, "SAAS_BILLING_ENABLED", False)
        session = db.get(VisitorSession, session_id)
        assert session is not None
        session.closed_at = started + timedelta(minutes=5)
        db.commit()
        assert await list_review_files(db, staff, session_id) == []
        session.closed_at = None
        session.status = "closed"
        db.commit()
        assert await list_review_files(db, staff, session_id) == []
        session.status = "open"
        db.add(VisitorSession(
            id=uuid4(), project_id=project, visitor_id=visitor,
            platform_id=platform, status="closed",
            created_at=started + timedelta(minutes=5),
            closed_at=started + timedelta(minutes=15),
        ))
        db.commit()
        assert await list_review_files(db, staff, session_id) == []
        assert (await aftersales_review_files.review_chat_target(
            db, staff, session_id, source_message_id=source_id,
            source_channel="web",
        )).message_seq is None
        with pytest.raises(TGOAPIException) as missing_session:
            await require_intake_evidence_files(db, staff, uuid4(), [])
        assert missing_session.value.status_code == 409
        db.execute(text(
            "UPDATE api_visitors SET access_retired_at = :retired_at WHERE id = :id"
        ), {"retired_at": datetime.now(UTC), "id": visitor.hex})
        db.commit()
        with pytest.raises(TGOAPIException) as retired_visitor:
            await require_intake_evidence_files(db, staff, session_id, [])
        assert retired_visitor.value.status_code == 409
        with pytest.raises(TGOAPIException) as retired_chat:
            await aftersales_review_files.review_chat_target(db, staff, session_id)
        assert retired_chat.value.status_code == 404
    engine.dispose()


@pytest.mark.asyncio
async def test_review_files_require_customer_read_permission(monkeypatch):
    from app.services import aftersales_review_files
    from app.core.exceptions import TGOAPIException

    monkeypatch.setattr(aftersales_review_files, "check_user_permission", lambda *_: False)
    with pytest.raises(TGOAPIException) as error:
        await list_review_files(
            object(), SimpleNamespace(id=uuid4(), project_id=uuid4(), role="user"),
            uuid4(),
        )
    assert error.value.status_code == 403
    with pytest.raises(TGOAPIException) as chat_error:
        await aftersales_review_files.review_chat_target(
            object(), SimpleNamespace(id=uuid4(), project_id=uuid4(), role="user"),
            uuid4(),
        )
    assert chat_error.value.status_code == 403


def test_aftersales_intake_contract_accepts_an_explicit_empty_attachment_list():
    from app.schemas.workflow_approval import WorkflowAfterSalesIntakeCreate

    request = WorkflowAfterSalesIntakeCreate.model_validate({
        "review_digest": "a" * 64,
        "order_reference": "ORDER-1",
        "sku": "LV-T01",
        "request_reason": "客户申请无理由退货",
        "policy_reference": "售后政策 v1",
        "evidence_file_ids": [],
    })
    assert request.evidence_file_ids == []
