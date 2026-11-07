"""Source parsing failures do not record URLs or upstream documents."""

import logging

import httpx
import pytest

from app.services import url_resolver
from app.main import app as runtime_app

PRIVATE = "synthetic-private-plugin-source-349"
URL = f"https://user:{PRIVATE}@source.test/plugin.yaml?token={PRIVATE}"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["parse", "transport", "status"])
async def test_source_failure_logs_do_not_contain_private_data(
    monkeypatch,
    caplog,
    kind,
):
    def respond(request):
        if kind == "transport":
            raise httpx.ConnectError(PRIVATE, request=request)
        return httpx.Response(
            200 if kind == "parse" else 503,
            text=f"private: [{PRIVATE}",
        )

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        url_resolver.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(
            transport=httpx.MockTransport(respond), **kwargs
        ),
    )
    caplog.set_level(logging.INFO, logger="httpx")
    caplog.set_level(logging.DEBUG, logger="services.url_resolver")
    assert await url_resolver.PluginURLResolver()._try_fetch_yaml(URL) is None
    assert PRIVATE not in "\n".join(
        str(record.__dict__) for record in caplog.records
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["private-scalar", "[one, two]", "1: value"])
async def test_only_an_object_with_string_keys_is_a_plugin_document(
    monkeypatch,
    text,
):
    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        url_resolver.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=text)
            ),
            **kwargs,
        ),
    )
    assert await url_resolver.PluginURLResolver()._try_fetch_yaml(URL) is None


@pytest.mark.asyncio
async def test_valid_relative_source_and_runtime_configuration_are_preserved(
    monkeypatch,
    caplog,
):
    document = (
        "id: fixture\nname: Fixture\nversion: '1'\n"
        "source:\n  binary:\n    url: assets/plugin.zip\n"
        f"runtime:\n  env:\n    TOKEN: {PRIVATE}\n"
    )
    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        url_resolver.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=document)
            ),
            **kwargs,
        ),
    )
    result = await url_resolver.PluginURLResolver().resolve(
        "https://source.test/releases/plugin.yaml"
    )
    assert result["source"]["binary"]["url"] == (
        "https://source.test/releases/assets/plugin.zip"
    )
    assert result["runtime"]["env"]["TOKEN"] == PRIVATE
    assert PRIVATE not in "\n".join(str(r.__dict__) for r in caplog.records)


@pytest.mark.parametrize(
    "source", [None, {"binary": []}, {"binary": {"url": 3}}]
)
def test_invalid_source_shapes_report_a_controlled_validation_error(source):
    with pytest.raises(ValueError):
        url_resolver.PluginURLResolver()._resolve_relative_urls(
            {"source": source},
            "https://source.test/releases/",
        )


def test_github_source_can_leave_the_optional_binary_empty():
    config = {"source": {"github": {"repo": "org/fixture"}, "binary": None}}
    assert (
        url_resolver.PluginURLResolver()._resolve_relative_urls(
            config,
            "https://source.test/",
        )
        == config
    )


@pytest.mark.asyncio
async def test_invalid_remote_source_returns_client_error_without_its_contents(
    monkeypatch,
):
    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        url_resolver.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    text=f"source: null\nprivate: {PRIVATE}\n",
                )
            ),
            **kwargs,
        ),
    )
    async with client_type(
        transport=httpx.ASGITransport(app=runtime_app),
        base_url="http://test",
    ) as client:
        response = await client.post("/plugins/fetch-info", json={"url": URL})
    assert response.status_code == 400
    assert PRIVATE not in response.text
