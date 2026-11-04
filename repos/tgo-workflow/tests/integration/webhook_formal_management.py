"""Exercise the employee-facing configuration path over real HTTP."""

from uuid import uuid4

from app.schemas.workflow_webhook import WebhookReceipt
from app.services.webhook_http import SIGNATURE_HEADERS


async def exercise_formal_configuration(
    client, staff_headers, completed, signed, owned_hooks,
):
    root = "/v1/ai/workflows"
    graph = {
        "name": "外部通知正式配置验收",
        "nodes": [
            {
                "id": "source", "type": "webhook",
                "position": {"x": 0, "y": 0},
                "data": {
                    "type": "webhook", "reference_key": "source",
                    "label": "接收通知", "method": "POST",
                },
            },
            {
                "id": "answer", "type": "answer",
                "position": {"x": 300, "y": 0},
                "data": {
                    "type": "answer", "reference_key": "answer",
                    "label": "返回结果", "output_type": "template",
                    "output_template": "{{source.body.question}}",
                },
            },
        ],
        "edges": [{"id": "source-answer", "source": "source",
                   "target": "answer"}],
    }
    created = await client.post(root, json=graph, headers=staff_headers)
    assert created.status_code == 201, "Formal workflow creation failed"
    workflow = created.json()
    path = root + "/" + workflow["id"]
    unpublished = await client.get(path + "/webhook", headers=staff_headers)
    assert unpublished.status_code == 200
    assert unpublished.json()["current_release"] is None
    refused_draft = await client.put(
        path + "/webhook", headers=staff_headers,
        json={"request_id": str(uuid4()), "expected_revision": 0,
              "expected_release_id": str(uuid4()),
              "expected_release_digest": "0" * 64, "enabled": False},
    )
    assert refused_draft.status_code == 409
    published = await client.post(
        path + "/publish", headers=staff_headers,
        json={
            "expected_draft_version": workflow["version"],
            "expected_publication_revision": workflow["publication_revision"],
        },
    )
    assert published.status_code == 200, "Formal workflow publication failed"
    assert published.json()["published_version"] == 1
    state = await client.get(path + "/webhook", headers=staff_headers)
    assert state.status_code == 200
    assert state.headers["Cache-Control"] == "no-store"
    assert state.json()["registration_available"] is True
    release = state.json()["current_release"]
    request = {
        "request_id": str(uuid4()), "expected_revision": 0,
        "expected_release_id": release["release_id"],
        "expected_release_digest": release["definition_digest"],
        "enabled": False,
    }
    saved = await client.put(
        path + "/webhook", json=request, headers=staff_headers,
    )
    assert saved.status_code == 200
    assert saved.headers["Cache-Control"] == "no-store"
    receipt = WebhookReceipt.model_validate(saved.json())
    owned_hooks.append(str(receipt.webhook.id))
    assert not receipt.webhook.enabled and receipt.credential is not None
    replay = await client.put(
        path + "/webhook", json=request, headers=staff_headers,
    )
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["webhook"]["id"] == str(receipt.webhook.id)
    assert replay.json()["credential"] == saved.json()["credential"]
    original = await client.get(
        path + "/webhook/commands/" + request["request_id"],
        headers=staff_headers,
    )
    assert original.status_code == 200
    assert original.headers["Cache-Control"] == "no-store"
    assert original.json()["request_id"] == request["request_id"]
    enabled = await client.put(
        path + "/webhook", headers=staff_headers,
        json={**request, "request_id": str(uuid4()),
              "expected_revision": receipt.webhook.revision, "enabled": True},
    )
    assert enabled.status_code == 200
    active = enabled.json()["webhook"]
    assert active["enabled"] is True

    async def send(credential, event):
        body, headers = signed(credential, event=event)
        wire = {SIGNATURE_HEADERS[key]: value for key, value in headers.items()}
        wire["Content-Type"] = "application/json"
        return await client.post(
            "/v1/workflow-webhooks/" + str(receipt.webhook.id),
            content=body, headers=wire,
        )

    accepted = await send(receipt, "formal-001")
    assert accepted.status_code == 202
    identifier = accepted.json()["delivery_id"]
    await completed(identifier)
    duplicate = await send(receipt, "formal-001")
    assert duplicate.status_code == 202
    assert duplicate.json()["delivery_id"] == identifier
    assert duplicate.json()["deduplicated"] is True
    history = await client.get(
        path + "/webhook/deliveries", headers=staff_headers,
    )
    assert history.status_code == 200 and history.json()["total"] == 1
    assert history.json()["items"][0]["execution_status"] == "completed"
    assert "synthetic-private-notification" not in history.text
    rotated = await client.post(
        path + "/webhook/rotate", headers=staff_headers,
        json={"request_id": str(uuid4()),
              "expected_revision": active["revision"]},
    )
    assert rotated.status_code == 200
    assert rotated.json()["webhook"]["credential_revision"] == 2
    rejected = await send(receipt, "formal-obsolete")
    assert rejected.status_code == 403
    disabled = await client.post(
        path + "/webhook/disable", headers=staff_headers,
        json={"request_id": str(uuid4()),
              "expected_revision": rotated.json()["webhook"]["revision"]},
    )
    assert disabled.status_code == 200
    assert disabled.json()["webhook"]["enabled"] is False
    final = await client.get(path + "/webhook", headers=staff_headers)
    assert final.status_code == 200 and final.json()["binding_current"] is True
    assert final.json()["webhook"]["enabled"] is False
    assert "credential" not in final.json()
    return str(receipt.webhook.id)
