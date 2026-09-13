"""Verify a real RAG upload, background processing and vector retrieval."""

import argparse
import time
from uuid import uuid4

import httpx


def verify(base_url: str, project_id: str) -> None:
    params = {"project_id": project_id}
    marker = uuid4().hex
    name = f"独立知识库验收-{marker}"
    content = f"知识库后台验收专用文档。验收编号为 {marker}，校验内容为蓝色纸鹤。此文档不用于客户答复。"
    collection_id = None
    file_id = None
    completed = False
    with httpx.Client(base_url=base_url, timeout=45, trust_env=False) as client:
        try:
            created = client.post(
                "/v1/collections",
                params=params,
                json={
                    "display_name": name,
                    "collection_type": "file",
                    "description": "受控验收文档，未绑定 AI 员工，完成后自动清理。",
                },
            )
            created.raise_for_status()
            collection_id = created.json()["id"]
            uploaded = client.post(
                "/v1/files",
                data={
                    "project_id": project_id,
                    "collection_id": collection_id,
                    "is_qa_mode": "false",
                    "language": "zh",
                },
                files={
                    "file": (
                        f"verification-{marker}.txt",
                        content.encode("utf-8"),
                        "text/plain",
                    )
                },
            )
            uploaded.raise_for_status()
            file_id = uploaded.json()["id"]
            print(
                f"Created isolated RAG verification: collection={collection_id}, file={file_id}",
                flush=True,
            )
            deadline = time.monotonic() + 120
            last_status = None
            record = uploaded.json()
            while time.monotonic() < deadline:
                response = client.get(f"/v1/files/{file_id}", params=params)
                response.raise_for_status()
                record = response.json()
                if record["status"] != last_status:
                    last_status = record["status"]
                    print(f"Processing state: {last_status}", flush=True)
                if last_status == "completed":
                    completed = True
                    break
                if last_status == "failed":
                    raise RuntimeError(f"RAG processing failed; inspect file {file_id}")
                time.sleep(1)
            assert completed, f"Processing still non-terminal: {last_status}"
            assert record["document_count"] > 0
            found = client.post(
                f"/v1/collections/{collection_id}/documents/search",
                params=params,
                json={"query": content, "search_mode": "embedding", "min_score": 0.1},
            )
            found.raise_for_status()
            matches = [r for r in found.json()["results"] if r["file_id"] == file_id]
            assert any(marker in item["content_preview"] for item in matches)
            print(
                "PASS: upload -> worker -> embedding -> vector retrieval returned the uploaded document",
                flush=True,
            )
        finally:
            if collection_id and (completed or file_id is None):
                current = client.get(f"/v1/collections/{collection_id}", params=params)
                current.raise_for_status()
                assert current.json()["display_name"] == name
                deleted = client.delete(
                    f"/v1/collections/{collection_id}",
                    params={**params, "hard_delete": "true"},
                )
                deleted.raise_for_status()
                assert (
                    client.get(
                        f"/v1/collections/{collection_id}", params=params
                    ).status_code
                    == 404
                )
                print(
                    "Removed only the isolated verification collection, file and vectors",
                    flush=True,
                )
            elif collection_id:
                print(
                    f"Retained unfinished verification: collection={collection_id}, file={file_id}",
                    flush=True,
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--project-id", required=True)
    args = parser.parse_args()
    verify(args.base_url, args.project_id)
