"""Loopback-only synthetic AI endpoint: barriers prove overlap and disconnects."""

import asyncio
from datetime import datetime, timezone
import os

from fastapi import FastAPI, HTTPException, Request

app = FastAPI()
calls = []
entered = {}


def now():
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
async def health():
    return {"status": "synthetic"}


@app.get("/fixture/state")
async def state():
    return {"calls": calls}


@app.post("/api/v1/chat/completions")
async def chat(request: Request):
    if request.query_params.get("project_id") != os.environ["TGO_PARALLEL_PROJECT"]:
        raise HTTPException(403, "Wrong owned fixture scope")
    body = await request.json()
    if body.get("provider_id") != "owned-fixture" or body.get("model") != "synthetic":
        raise HTTPException(403, "Only owned synthetic requests are accepted")
    case, step = body["messages"][-1]["content"].split(":", 1)
    if case not in {"success", "limit", "failure", "timeout", "worker", "sync", "stream"}:
        raise HTTPException(400, "Unknown owned fixture case")
    event = entered.setdefault(case, asyncio.Event())
    row = {
        "case": case,
        "step": step,
        "started_at": now(),
        "returned_at": None,
        "disconnected_at": None,
    }
    calls.append(row)
    expected = 5 if case == "limit" else 2
    if len([item for item in calls if item["case"] == case]) >= expected:
        event.set()
    await asyncio.wait_for(event.wait(), 10)
    if case == "failure" and step == "first":
        row["returned_at"] = now()
        raise HTTPException(503, "private-synthetic-provider-body")
    if case in {"failure", "timeout", "worker", "sync", "stream"}:
        for _ in range(600):
            if await request.is_disconnected():
                row["disconnected_at"] = now()
                return {"disconnected": True}
            await asyncio.sleep(0.05)
        raise HTTPException(504, "Owned blocking fixture expired")
    if case == "success" and step == "second":
        for _ in range(200):
            if any(item["case"] == case and item["step"] == "followup-first" for item in calls):
                break
            await asyncio.sleep(0.01)
        else:
            raise HTTPException(504, "Sequential follow-up never started")
    await asyncio.sleep(0.1)
    row["returned_at"] = now()
    return {"choices": [{"message": {"content": step}}]}
