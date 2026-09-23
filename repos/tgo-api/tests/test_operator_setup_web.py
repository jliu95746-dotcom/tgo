"""Local setup refuses cross-origin requests and never echoes passwords."""

import re
from unittest.mock import Mock

import httpx
import pytest

from app.commands.operator_setup_web import build_setup_app


@pytest.mark.asyncio
async def test_local_form_creates_once_and_does_not_echo_secret():
    create = Mock()
    app = build_setup_app(
        "test@example.com", "admin", "http://127.0.0.1:9876", create
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:9876",
    ) as client:
        page = await client.get("/")
        assert page.status_code == 200
        assert 'type="password"' in page.text
        assert page.headers["cache-control"] == "no-store"
        nonce = re.search(r'name="nonce" value="([^"]+)"', page.text)[1]
        payload = {
            "nonce": nonce,
            "password": "synthetic-test-password",
            "confirm": "synthetic-test-password",
        }
        denied = await client.post(
            "/create", data=payload, headers={"Origin": "https://foreign.test"}
        )
        assert denied.status_code == 403
        create.assert_not_called()
        denied = await client.post(
            "/create",
            data={**payload, "nonce": "wrong"},
            headers={"Origin": "http://127.0.0.1:9876"},
        )
        assert denied.status_code == 403
        create.assert_not_called()
        result = await client.post(
            "/create",
            data=payload,
            headers={"Origin": "http://127.0.0.1:9876"},
        )
        assert result.status_code == 200
        assert "账号已创建" in result.text
        assert payload["password"] not in result.text
        create.assert_called_once()
        assert create.call_args.args[0].email == "test@example.com"
        assert (
            await client.post(
                "/create",
                data=payload,
                headers={"Origin": "http://127.0.0.1:9876"},
            )
        ).status_code == 410


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["short", "mismatch", "long", "extra", "expired", "host"]
)
async def test_invalid_setup_requests_do_not_create_accounts(case):
    create = Mock()
    app = build_setup_app(
        "test@example.com",
        "admin",
        "http://127.0.0.1:9876",
        create,
        lifetime_seconds=-1 if case == "expired" else 900,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:9876",
    ) as client:
        if case in {"expired", "host"}:
            response = await client.get(
                "/", headers={"Host": "foreign.test"} if case == "host" else {}
            )
            assert response.status_code in {403, 410}
        else:
            page = await client.get("/")
            nonce = re.search(r'name="nonce" value="([^"]+)"', page.text)[1]
            data = {
                "nonce": nonce,
                "password": "synthetic-password",
                "confirm": "synthetic-password",
            }
            if case == "short":
                data.update(password="short", confirm="short")
            elif case == "mismatch":
                data["confirm"] = "different"
            elif case == "long":
                data.update(password="x" * 5000, confirm="x" * 5000)
            else:
                data["email"] = "other@example.com"
            response = await client.post(
                "/create",
                data=data,
                headers={"Origin": "http://127.0.0.1:9876"},
            )
            assert response.status_code in {400, 413}
            assert "synthetic-password" not in response.text
        create.assert_not_called()
