"""HTTP fixtures for published source approvals; credentials stay in memory."""

import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json
from uuid import uuid4
import httpx


class SourceApprovalHttp:
    def __init__(self, client, base, project, actor, private):
        self.client, self.base = client, base
        self.project, self.actor, self.private = project, actor, private
        self.headers = {"X-Internal-API-Key": private}
        self.hook_secrets = []

    async def ready(self, process, address=None):
        for _ in range(240):
            assert process.poll() is None
            try:
                if (
                    await self.client.get((address or self.base) + "/health")
                ).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
        raise RuntimeError("Owned source API readiness observation expired")

    async def request(self, method, path, expected=200, **kwargs):
        response = await self.client.request(method, self.base + path, **kwargs)
        assert (
            response.status_code == expected
        ), f"Owned HTTP {response.status_code}: {path}"
        return response.json()

    async def create(self, kind, case):
        leaf = "source.data.summary" if kind == "event" else "source.body.question"
        graph = {
            "name": "合成来源审批-" + kind + "-" + case,
            "nodes": [
                {
                    "id": "source",
                    "type": kind,
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "type": kind,
                        "reference_key": "source",
                        "label": "合成来源",
                        "event_type": "staff.session.closed",
                        "method": "POST",
                    },
                },
                {
                    "id": "approval",
                    "type": "approval",
                    "position": {"x": 250, "y": 0},
                    "data": {
                        "type": "approval",
                        "reference_key": "approval",
                        "label": "员工确认",
                        "approver_staff_id": self.actor,
                        "summary": "确认本次数据 {{" + leaf + "}}",
                        "expires_in_minutes": 60,
                    },
                },
                {
                    "id": "answer",
                    "type": "answer",
                    "position": {"x": 500, "y": 0},
                    "data": {
                        "type": "answer",
                        "reference_key": "answer",
                        "label": "原始结果",
                        "output_type": "variable",
                        "output_variable": leaf,
                    },
                },
            ],
            "edges": [
                {"id": "s-a", "source": "source", "target": "approval"},
                {"id": "a-r", "source": "approval", "target": "answer"},
            ],
        }
        flow = await self.request(
            "POST",
            "/v1/workflows/",
            201,
            params={"project_id": self.project},
            json=graph,
        )
        path = "/v1/workflows/" + flow["id"]
        await self.request(
            "POST",
            path + "/publish",
            params={"project_id": self.project},
            json={
                "expected_draft_version": flow["version"],
                "expected_publication_revision": flow["publication_revision"],
            },
        )
        release = await self.request(
            "GET", path + "/releases/1/integration", params={"project_id": self.project}
        )
        row = {
            "case": case,
            "workflow_id": flow["id"],
            "release_id": release["release_id"],
            "release_digest": release["definition_digest"],
        }
        if kind == "webhook":
            receipt = await self.request(
                "PUT",
                path + "/webhook",
                headers=self.headers,
                json={
                    "request_id": str(uuid4()),
                    "project_id": self.project,
                    "actor_staff_id": self.actor,
                    "expected_revision": 0,
                    "expected_release_id": row["release_id"],
                    "expected_release_digest": row["release_digest"],
                    "enabled": True,
                },
            )
            assert receipt["credential_status"] == "available"
            row.update(hook=receipt["webhook"], secret=receipt["credential"]["secret"])
            self.hook_secrets.append(row["secret"])
        return row

    async def send_event(self, row):
        body = json.dumps(
            {
                "project_id": self.project,
                "event_type": "staff.session.closed",
                "data": {"summary": "synthetic-event-" + row["case"]},
                "expected_release_id": row["release_id"],
                "expected_release_digest": row["release_digest"],
                "source_binding": row["source_binding"],
            },
            separators=(",", ":"),
        ).encode()
        instant = str(int(datetime.now(timezone.utc).timestamp()))
        event_id = "source-" + row["source_binding"]["delivery_id"]
        message = b"\n".join(
            (instant.encode(), event_id.encode(), row["workflow_id"].encode(), body)
        )
        return await self.request(
            "POST",
            "/v1/workflows/" + row["workflow_id"] + "/events",
            202,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Workflow-Timestamp": instant,
                "X-Workflow-Event-ID": event_id,
                "X-Workflow-Signature": "sha256="
                + hmac.new(self.private.encode(), message, hashlib.sha256).hexdigest(),
            },
        )

    async def send_webhook(self, row):
        hook = row["hook"]
        instant, event_id = (
            str(int(datetime.now(timezone.utc).timestamp())),
            "synthetic-" + row["case"],
        )
        body = json.dumps(
            {"data": {"question": "synthetic-webhook-" + row["case"]}},
            separators=(",", ":"),
        ).encode()
        message = (
            f'TGO-WEBHOOK-V1\n{hook["id"]}\n{hook["credential_revision"]}\n'
            f'{hook["key_id"]}\n{instant}\n{event_id}\n'
        ).encode() + body
        return await self.request(
            "POST",
            "/v1/webhook-deliveries/" + hook["id"],
            202,
            content=body,
            headers={
                **self.headers,
                "Content-Type": "application/json",
                "X-TGO-Webhook-Timestamp": instant,
                "X-TGO-Webhook-Event-ID": event_id,
                "X-TGO-Webhook-Key-ID": hook["key_id"],
                "X-TGO-Webhook-Signature": "sha256="
                + hmac.new(row["secret"].encode(), message, hashlib.sha256).hexdigest(),
            },
        )

    async def approve(self, identifier):
        query = {"project_id": self.project, "actor_staff_id": self.actor}
        rows = await self.request(
            "GET", "/v1/workflows/approvals", params=query, headers=self.headers
        )
        row = next(row for row in rows if row["execution_id"] == identifier)
        result = await self.request(
            "POST",
            "/v1/workflows/approvals/" + row["id"] + "/decision",
            headers=self.headers,
            params=query,
            json={"decision": "approve", "preview_digest": row["preview_digest"]},
        )
        assert result["dispatch_status"] == "queued"
        return row["id"]

    async def disable_webhook(self, row):
        await self.request(
            "POST",
            "/v1/workflows/" + row["workflow_id"] + "/webhook/disable",
            headers=self.headers,
            json={
                "request_id": str(uuid4()),
                "project_id": self.project,
                "actor_staff_id": self.actor,
                "expected_revision": row["hook"]["revision"],
            },
        )
