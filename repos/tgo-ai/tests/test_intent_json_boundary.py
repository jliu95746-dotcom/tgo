"""HTTP JSON must preserve media signals without relaxing strict contracts."""

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.schemas.intent_analysis import IntentAnalysisRequest


@pytest.fixture
def client():
    app = FastAPI()

    @app.post("/intent")
    def accept(request: IntentAnalysisRequest):
        return request.classification_input.model_dump(mode="json")

    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("categories", [[], ["identity_number", "phone_number"]])
def test_http_json_media_categories_are_accepted_and_preserved(client, categories):
    response = client.post("/intent", json={
        "provider_id": str(uuid4()), "model": "owned-model",
        "classification_input": {"ocr_text": "订单 A1001", "sensitive_data_categories": categories},
    })
    assert response.status_code == 200
    assert response.json()["sensitive_data_categories"] == categories
    assert response.json()["ocr_text"] == "订单 A1001"


@pytest.mark.parametrize("invalid", [
    {"sensitive_data_categories": ["unknown-category"]},
    {"sensitive_data_categories": [1]},
    {"sensitive_data_categories": "phone_number"},
    {"consecutive_unknown_count": "1"},
    {"ocr_text": 123},
    {"execute_tools": True},
])
def test_http_json_does_not_relax_content_types_or_allow_extra_fields(client, invalid):
    response = client.post("/intent", json={
        "provider_id": str(uuid4()), "model": "owned-model",
        "classification_input": {"ocr_text": "订单 A1001", **invalid},
    })
    assert response.status_code == 422
