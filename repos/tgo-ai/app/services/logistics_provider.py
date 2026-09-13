"""One provider adapter shared by tool tests, archive queries and AI employees."""
import base64
import hashlib
import ipaddress
import json
from datetime import datetime
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet
from pydantic import JsonValue

from app.config import settings
from app.schemas.logistics_provider import LogisticsProviderConfig
from app.services.logistics_presets import configure_preset, query_preset


def validate_endpoint(endpoint: str) -> None:
    parts = urlsplit(endpoint)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError("请填写不含密钥或参数的 HTTPS 服务商接口地址")
    if parts.hostname.lower() in ("localhost", "localhost.localdomain"):
        raise ValueError("请使用服务商的公网 HTTPS 地址")
    try:
        address = ipaddress.ip_address(parts.hostname)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError("请使用服务商的公网 HTTPS 地址")


def _cipher() -> Fernet:
    if not settings.secret_key or settings.secret_key.startswith("your-super-secret"):
        raise ValueError("请管理员先配置服务端 SECRET_KEY，再保存服务商密钥")
    key = hashlib.sha256(
        ("logistics-provider:" + settings.secret_key).encode()
    ).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def prepare_config(
    raw: object, previous: object, *, endpoint: str
) -> dict[str, JsonValue]:
    config = LogisticsProviderConfig.model_validate(raw)
    endpoint = configure_preset(config, endpoint)
    validate_endpoint(endpoint)
    old = LogisticsProviderConfig.model_validate(previous) if previous else None
    # Never trust client-supplied ciphertext; only reuse this record's stored key.
    config.credential_encrypted = ""
    if config.auth_type != "none":
        if config.credential:
            config.credential_encrypted = (
                _cipher().encrypt(config.credential.encode()).decode()
            )
        elif (
            old
            and old.credential_endpoint == endpoint
            and old.auth_type == config.auth_type
            and old.auth_header == config.auth_header
            and old.provider_kind == config.provider_kind
            and old.account_id == config.account_id
        ):
            config.credential_encrypted = old.credential_encrypted
        if not config.credential_encrypted:
            raise ValueError("请填写授权密钥；更换接口地址或授权方式后需要重新填写")
    config.credential = ""
    config.credential_endpoint = endpoint
    config.credential_configured = bool(config.credential_encrypted)
    return config.model_dump(mode="json")


def public_config(raw: object) -> dict[str, JsonValue]:
    config = LogisticsProviderConfig.model_validate(raw)
    return config.model_dump(
        mode="json",
        exclude={"credential", "credential_encrypted", "credential_endpoint"},
    )


def _path(value: JsonValue, path: str) -> JsonValue:
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _scalar(value: JsonValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value) if isinstance(value, (str, int, float)) else ""


def normalize_result(
    raw: JsonValue, config: LogisticsProviderConfig, tracking_no: str
) -> dict[str, JsonValue]:
    if _scalar(_path(raw, config.success_path)) != config.success_value:
        raise ValueError("服务商未返回查询成功状态，请检查账号权限、额度、单号或成功字段配置")
    if config.tracking_path:
        returned = _scalar(_path(raw, config.tracking_path)).replace(" ", "").upper()
        if returned != tracking_no:
            raise ValueError("服务商返回的单号与查询单号不一致，已停止使用该结果")
    rows = _path(raw, config.events_path)
    if not isinstance(rows, list) or not rows or len(rows) > 500:
        raise ValueError("未取得有效物流轨迹，请检查单号或返回字段配置")
    events: list[JsonValue] = []
    for row in rows:
        description = _scalar(_path(row, config.description_field))
        timestamp = _scalar(_path(row, config.time_field))
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            valid_time = 2000 <= parsed.year <= 2100
        except ValueError:
            valid_time = False
        if not description or not valid_time:
            raise ValueError("轨迹时间或内容字段无效，请检查返回字段配置；时间需为 ISO 日期格式")
        events.append({"time": timestamp, "description": description[:4000]})
    # No raw payload/headers: avoid echoing credentials or unrelated customer data.
    return {
        "tracking_no": tracking_no,
        "carrier_name": _scalar(_path(raw, config.carrier_path))[:128],
        "events": events,
    }


async def execute_logistics_provider(
    endpoint: str,
    raw_config: object,
    args: dict[str, JsonValue],
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, JsonValue]:
    config = LogisticsProviderConfig.model_validate(raw_config)
    endpoint = configure_preset(config, endpoint)
    validate_endpoint(endpoint)
    if config.provider_kind == "kuaidi100":
        validate_endpoint(settings.logistics_kuaidi100_recognize_endpoint)
    tracking_no = str(args.get("tracking_no") or "").replace(" ", "").upper()
    if (
        not 6 <= len(tracking_no) <= 64
        or not tracking_no.isascii()
        or not tracking_no.isalnum()
    ):
        raise ValueError("请输入有效的物流单号")
    credential = config.credential
    if config.credential_encrypted:
        if config.credential_endpoint != endpoint:
            raise ValueError("接口地址已变更，请重新保存服务商授权密钥")
        credential = _cipher().decrypt(config.credential_encrypted.encode()).decode()
    headers: dict[str, str] = {}
    if config.auth_type != "none":
        if not credential:
            raise ValueError("尚未填写快递服务商授权密钥")
        prefix = {"appcode": "APPCODE ", "bearer": "Bearer ", "header": ""}[
            config.auth_type
        ]
        headers[
            config.auth_header if config.auth_type == "header" else "Authorization"
        ] = (prefix + credential)
    # Only declared tracking number goes to provider; LLM cannot override credentials.
    params = {**config.fixed_params, config.tracking_param: tracking_no}

    async def request(active: httpx.AsyncClient) -> dict[str, JsonValue]:
        if config.provider_kind != "custom":
            preset_payload = await query_preset(
                active, endpoint, config, credential, tracking_no, args
            )
            return normalize_result(preset_payload, config, tracking_no)
        if config.method == "GET":
            response = await active.get(
                endpoint, params=params, headers=headers, follow_redirects=False
            )
        elif config.body_format == "form":
            response = await active.post(
                endpoint, data=params, headers=headers, follow_redirects=False
            )
        else:
            response = await active.post(
                endpoint, json=params, headers=headers, follow_redirects=False
            )
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            raise ValueError("服务商返回内容过大")
        try:
            payload: JsonValue = response.json()
        except json.JSONDecodeError:
            raise ValueError("服务商未返回 JSON 数据，请检查接口地址") from None
        return normalize_result(payload, config, tracking_no)

    if client:
        return await request(client)
    async with httpx.AsyncClient(timeout=30.0) as active:
        return await request(active)
