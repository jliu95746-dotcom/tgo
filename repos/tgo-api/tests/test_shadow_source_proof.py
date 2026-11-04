"""Only a matching server IM record can prove a shadow draft source."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.shadow_source_proof import verify_shadow_source


class FakeIM:
    enabled = True

    def __init__(self, message=None, error=None):
        self.message = message
        self.error = error
        self.calls = []

    async def get_message_by_client_msg_no(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.message


def customer_message(visitor_id, *, source_id, client_no, text="这款包多大？"):
    return SimpleNamespace(
        message_id=78234,
        message_seq=13,
        client_msg_no=client_no,
        channel_id=f"{visitor_id}-vtr",
        channel_type=251,
        from_uid=f"{visitor_id}-vtr",
        timestamp=int(datetime.now(timezone.utc).timestamp()) - 20,
        payload={
            "type": 1,
            "content": text,
            "extra": {"message_id": source_id},
        },
    )


@pytest.mark.asyncio
async def test_matching_im_message_verifies_platform_and_web_ids():
    visitor_id = uuid4()
    source_id, client_no = "external-42", "im-client-42"
    im = FakeIM(
        customer_message(visitor_id, source_id=source_id, client_no=client_no)
    )
    proof = await verify_shadow_source(
        im_client=im,
        visitor_id=visitor_id,
        source_message_id=source_id,
        source_client_msg_no=client_no,
        customer_message="这款包多大？",
        message_type=1,
    )
    assert proof.status == "verified"
    assert proof.message_id == "78234"
    assert proof.message_seq == 13
    assert proof.content_sha256
    assert "这款包多大" not in proof.model_dump_json()
    assert im.calls[0]["channel_id"] == f"{visitor_id}-vtr"
    assert im.calls[0]["raise_on_error"] is True

    web = await verify_shadow_source(
        im_client=im,
        visitor_id=visitor_id,
        source_message_id=client_no,
        source_client_msg_no=client_no,
        customer_message="这款包多大？",
        message_type=1,
    )
    assert web.status == "verified"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("channel_id", "foreign-vtr"),
        ("from_uid", "foreign-vtr"),
        ("client_msg_no", "wrong"),
        ("timestamp", 0),
    ],
)
async def test_mismatched_or_invalid_im_record_never_verifies(field, value):
    visitor_id = uuid4()
    message = customer_message(
        visitor_id, source_id="source-1", client_no="client-1"
    )
    setattr(message, field, value)
    proof = await verify_shadow_source(
        im_client=FakeIM(message),
        visitor_id=visitor_id,
        source_message_id="source-1",
        source_client_msg_no="client-1",
        customer_message="这款包多大？",
        message_type=1,
    )
    assert proof.status == "mismatch"


@pytest.mark.asyncio
async def test_unavailable_missing_and_media_remain_unverified():
    visitor_id = uuid4()
    base = dict(
        visitor_id=visitor_id,
        source_message_id="source-1",
        source_client_msg_no="client-1",
        customer_message="这款包多大？",
    )
    missing = await verify_shadow_source(
        im_client=FakeIM(), message_type=1, **base
    )
    assert missing.status == "missing"
    unavailable = await verify_shadow_source(
        im_client=FakeIM(error=RuntimeError("private IM failed")),
        message_type=1,
        **base,
    )
    assert unavailable.status == "unavailable"
    media = FakeIM()
    unsupported = await verify_shadow_source(
        im_client=media, message_type=2, **base
    )
    assert unsupported.status == "unsupported"
    assert media.calls == []
    no_id = await verify_shadow_source(
        im_client=FakeIM(),
        message_type=1,
        **{**base, "source_client_msg_no": None},
    )
    assert no_id.status == "unverified"


@pytest.mark.asyncio
async def test_source_id_and_customer_text_must_match_same_im_message():
    visitor_id = uuid4()
    message = customer_message(
        visitor_id,
        source_id="external-1",
        client_no="client-1",
    )
    for source_id, text in (
        ("external-2", "这款包多大？"),
        ("external-1", "客户没说过的内容"),
    ):
        proof = await verify_shadow_source(
            im_client=FakeIM(message),
            visitor_id=visitor_id,
            source_message_id=source_id,
            source_client_msg_no="client-1",
            customer_message=text,
            message_type=1,
        )
        assert proof.status == "mismatch"
