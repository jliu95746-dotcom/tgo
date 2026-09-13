"""Opt-in native workflow API/Redis/worker/database verification, without LLMs."""

import argparse
import json
import time
from uuid import uuid4

import httpx


def verify(base_url: str) -> None:
    project_id = str(uuid4())
    params = {"project_id": project_id}
    expected = f"native-worker-verification-{uuid4().hex}"
    workflow_id = None
    terminal = True
    with httpx.Client(base_url=base_url, timeout=15, trust_env=False) as client:
        try:
            created = client.post(
                "/v1/workflows/",
                params=params,
                json={
                    "name": "独立后台任务验收（自动清理）",
                    "description": "受控测试：不调用模型、不访问外部系统、不发送客户消息。",
                    "nodes": [
                        {
                            "id": "input",
                            "type": "input",
                            "position": {"x": 0, "y": 0},
                            "data": {
                                "type": "input",
                                "label": "输入",
                                "reference_key": "start",
                                "input_variables": [
                                    {"name": "message", "type": "string"}
                                ],
                            },
                        },
                        {
                            "id": "answer",
                            "type": "answer",
                            "position": {"x": 300, "y": 0},
                            "data": {
                                "type": "answer",
                                "label": "结果",
                                "reference_key": "answer",
                                "output_type": "variable",
                                "output_variable": "start.message",
                            },
                        },
                    ],
                    "edges": [{"id": "edge", "source": "input", "target": "answer"}],
                },
            )
            created.raise_for_status()
            workflow_id = created.json()["id"]
            print(f"Created isolated verification workflow: {workflow_id}", flush=True)

            # Run twice: a solo worker reuses the process across separate asyncio.run loops.
            for run_index in range(2):
                terminal = False
                started = client.post(
                    f"/v1/workflows/{workflow_id}/execute",
                    params=params,
                    json={"async": True, "inputs": {"message": expected}},
                )
                started.raise_for_status()
                execution_id = started.json()["id"]
                deadline = time.monotonic() + 45
                result = started.json()
                while time.monotonic() < deadline:
                    status = client.get(
                        f"/v1/workflows/executions/{execution_id}", params=params
                    )
                    status.raise_for_status()
                    result = status.json()
                    if result["status"] in {"completed", "failed", "cancelled"}:
                        terminal = True
                        break
                    time.sleep(0.5)
                assert (
                    result["status"] == "completed"
                ), f"Run {run_index + 1}: {json.dumps(result, ensure_ascii=False)}"
                assert result["output"] == {"result": expected}, result["output"]
                assert len(result["node_executions"]) == 2, result["node_executions"]
                print(
                    f"PASS: async execution {run_index + 1}, output and 2 node records persisted",
                    flush=True,
                )
        finally:
            if workflow_id and terminal:
                deleted = client.delete(f"/v1/workflows/{workflow_id}", params=params)
                deleted.raise_for_status()
                check = client.get(f"/v1/workflows/{workflow_id}", params=params)
                assert check.status_code == 404
                print(
                    "Removed only the isolated verification workflow and its execution records",
                    flush=True,
                )
            elif workflow_id:
                print(
                    f"Retained non-terminal verification: project={project_id}, workflow={workflow_id}",
                    flush=True,
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Local workflow service URL")
    args = parser.parse_args()
    verify(args.base_url)
