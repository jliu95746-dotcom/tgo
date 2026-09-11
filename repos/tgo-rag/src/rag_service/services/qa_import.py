"""Validate an entire QA import before any database rows are created."""

import csv
import io
import json

from pydantic import ValidationError

from ..schemas.qa import QAPairCreateRequest, QAPairImportRequest


def parse_qa_import(request: QAPairImportRequest) -> list[QAPairCreateRequest]:
    try:
        if request.format == "json":
            raw: object = json.loads(request.data)
            if not isinstance(raw, list):
                raise ValueError("JSON 数据必须是问答数组。")
            rows = raw
        else:
            reader = csv.DictReader(
                io.StringIO(request.data.lstrip("\ufeff")), strict=True
            )
            if not reader.fieldnames or not {"question", "answer"}.issubset(
                reader.fieldnames
            ):
                raise ValueError("CSV 表头必须包含 question 和 answer。")
            if len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise ValueError("CSV 表头不能重复，请确保每列名称唯一。")
            rows = list(reader)
    except (json.JSONDecodeError, csv.Error) as error:
        raise ValueError(f"{request.format.upper()} 格式不正确，请检查引号和分隔符。") from error
    if not rows:
        raise ValueError("未找到可导入的问答。")
    if len(rows) > 1000:
        raise ValueError("每次最多导入 1000 条问答。")

    pairs = []
    for row_number, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or "question" not in row or "answer" not in row:
            raise ValueError(f"第 {row_number} 条问答必须包含 question 和 answer。")
        values: dict[str, object] = {
            "question": row["question"], "answer": row["answer"],
            "category": row.get("category") or request.category,
            "subcategory": row.get("subcategory") or None,
            "tags": row.get("tags") or request.tags,
            "qa_metadata": row.get("qa_metadata", row.get("metadata")),
            "priority": row.get("priority", 0),
        }
        if request.format == "csv":
            if None in row:
                raise ValueError(f"第 {row_number} 条 CSV 问答的列数与表头不一致。")
            values["priority"] = row.get("priority") or 0
            tags = row.get("tags")
            if isinstance(tags, str) and tags.strip():
                values["tags"] = [tag.strip() for tag in tags.split(",") if tag.strip()]
            metadata = values["qa_metadata"]
            if isinstance(metadata, str):
                try:
                    values["qa_metadata"] = (
                        json.loads(metadata) if metadata.strip() else None
                    )
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"第 {row_number} 条问答的 metadata 必须是 JSON 对象。"
                    ) from error
        try:
            pairs.append(QAPairCreateRequest.model_validate(values))
        except ValidationError as error:
            fields = ", ".join(dict.fromkeys(
                str(problem["loc"][0]) for problem in error.errors(include_input=False)
            ))
            raise ValueError(f"第 {row_number} 条问答的 {fields} 内容或格式不正确。") from error
    return pairs
