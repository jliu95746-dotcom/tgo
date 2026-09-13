"""Server-owned signed logistics protocols; no caller-defined credential targets."""
import base64
import hashlib
import json
import re

import httpx
from pydantic import JsonValue

from app.config import settings
from app.schemas.logistics_provider import LogisticsProviderConfig


def configure_preset(config: LogisticsProviderConfig, endpoint: str) -> str:
    if config.provider_kind == "custom":
        return endpoint
    config.account_id = config.account_id.strip()
    if not config.account_id:
        raise ValueError("请填写服务商提供的账号标识")
    config.method = "POST"
    config.body_format = "form"
    config.auth_type = "header"
    config.auth_header = "Authorization"
    config.fixed_params = {}
    if config.provider_kind == "kuaidi100":
        config.provider_name = "快递100"
        config.success_path, config.success_value = "status", "200"
        config.events_path, config.time_field = "data", "time"
        config.description_field = "context"
        config.carrier_path, config.tracking_path = "com", "nu"
        return settings.logistics_kuaidi100_endpoint
    config.provider_name = "快递鸟"
    config.success_path, config.success_value = "Success", "true"
    config.events_path, config.time_field = "Traces", "AcceptTime"
    config.description_field = "AcceptStation"
    config.carrier_path, config.tracking_path = "ShipperCode", "LogisticCode"
    return settings.logistics_kdniao_endpoint


async def _post(
    client: httpx.AsyncClient, endpoint: str, data: dict[str, str]
) -> JsonValue:
    response = await client.post(endpoint, data=data, follow_redirects=False)
    response.raise_for_status()
    if len(response.content) > 2_000_000:
        raise ValueError("服务商返回内容过大")
    try:
        result: JsonValue = response.json()
        return result
    except json.JSONDecodeError:
        raise ValueError("服务商未返回有效 JSON") from None


async def query_preset(
    client: httpx.AsyncClient,
    endpoint: str,
    config: LogisticsProviderConfig,
    credential: str,
    tracking_no: str,
    args: dict[str, JsonValue],
) -> JsonValue:
    carrier = str(args.get("carrier_code") or "").strip()
    phone = str(args.get("phone") or "").strip()
    if carrier and not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", carrier):
        raise ValueError("快递公司编码格式不正确")
    if phone and not re.fullmatch(r"[0-9]{4}|[0-9]{11}", phone):
        raise ValueError("请填写手机号后四位或十一位手机号")
    if config.provider_kind == "kdniao":
        payload = {"LogisticCode": tracking_no}
        if carrier:
            payload["ShipperCode"] = carrier.upper()
        if phone:
            payload["CustomerName"] = phone[-4:]
        request_data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.md5((request_data + credential).encode()).hexdigest()
        return await _post(
            client,
            endpoint,
            {
                "RequestData": request_data,
                "EBusinessID": config.account_id,
                "RequestType": "8002",
                "DataType": "2",
                "DataSign": base64.b64encode(digest.encode()).decode(),
            },
        )
    if len(tracking_no) > 32:
        raise ValueError("快递100单号长度不能超过32位")
    if not carrier:
        candidates = await _post(
            client,
            settings.logistics_kuaidi100_recognize_endpoint,
            {"num": tracking_no, "key": credential},
        )
        rows = candidates if isinstance(candidates, list) else [candidates]
        codes = {
            row["comCode"]
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("comCode"), str)
        }
        if len(codes) != 1:
            raise ValueError("未能唯一识别快递公司，请补充快递公司编码再查询")
        carrier = str(next(iter(codes)))
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", carrier):
            raise ValueError("服务商返回的快递公司编码无效")
    param = {"com": carrier.lower(), "num": tracking_no, "show": "0", "order": "desc"}
    if phone:
        param["phone"] = phone
    encoded = json.dumps(param, ensure_ascii=False, separators=(",", ":"))
    signature = (
        hashlib.md5((encoded + credential + config.account_id).encode())
        .hexdigest()
        .upper()
    )
    return await _post(
        client,
        endpoint,
        {
            "customer": config.account_id,
            "sign": signature,
            "param": encoded,
        },
    )
