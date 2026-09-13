from app.services.storage.local import LocalStorageBackend

import pytest


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "http://localhost:5173/api/v1/chat/files/a?signature=abc",
            "http://api:18000/v1/chat/files/a?signature=abc",
        ),
        (
            "http://127.0.0.1:5173/api/v1/chat/files/a",
            "http://api:18000/v1/chat/files/a",
        ),
        (
            "/api/v1/chat/files/a?signature=abc",
            "http://api:18000/v1/chat/files/a?signature=abc",
        ),
        (
            "https://localhost.example.invalid/api/v1/a?signature=abc",
            "https://localhost.example.invalid/api/v1/a?signature=abc",
        ),
        (
            "https://cdn.example.invalid/api/v1/a?host=localhost",
            "https://cdn.example.invalid/api/v1/a?host=localhost",
        ),
    ],
)
def test_resolve_url_preserves_signed_query_and_external_hostname(
    tmp_path, url, expected
):
    storage = LocalStorageBackend(str(tmp_path), "http://api:18000")
    assert storage.resolve_url(url) == expected
