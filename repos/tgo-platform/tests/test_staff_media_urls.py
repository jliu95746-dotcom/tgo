from __future__ import annotations

import pytest

from app.api.v1 import messages


@pytest.mark.parametrize(
    "url,path",
    [
        (
            "http://127.0.0.1:5173/api/v1/chat/files/a?token=abc",
            "/v1/chat/files/a?token=abc",
        ),
        ("http://localhost:5173/api/v1/chat/files/a", "/v1/chat/files/a"),
        ("http://localhost:8000/v1/chat/files/a", "/v1/chat/files/a"),
        ("http://127.0.0.1:18000/v1/chat/files/a", "/v1/chat/files/a"),
        ("/api/v1/chat/files/a", "/v1/chat/files/a"),
        ("/v1/chat/files/a", "/v1/chat/files/a"),
    ],
)
def test_media_urls_use_configured_internal_origin_without_double_port(
    monkeypatch, url, path
):
    monkeypatch.setattr(messages.settings, "api_base_url", "http://api-internal:18001")
    assert messages._internalize_url(url) == "http://api-internal:18001" + path


@pytest.mark.parametrize(
    "url",
    [
        "https://cdn.example.invalid/api/v1/image.png?host=localhost",
        "https://localhost.example.invalid/api/v1/image.png",
        "https://example.invalid/image.png",
        "http://127.0.0.1:5173/unrelated/path",
    ],
)
def test_external_and_unrelated_urls_are_not_rewritten(monkeypatch, url):
    monkeypatch.setattr(messages.settings, "api_base_url", "http://api-internal:18001")
    assert messages._internalize_url(url) == url
