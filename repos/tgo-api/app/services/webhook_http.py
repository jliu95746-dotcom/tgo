"""One bounded, unambiguous HTTP representation of signed webhook bytes."""

from fastapi import HTTPException, Request

MAX_BODY_BYTES = 65536
SIGNATURE_HEADERS = {
    "timestamp": "x-tgo-webhook-timestamp",
    "event_id": "x-tgo-webhook-event-id",
    "key_id": "x-tgo-webhook-key-id",
    "signature": "x-tgo-webhook-signature",
}


async def read_signed_delivery(
    request: Request,
) -> tuple[bytes, dict[str, str], str]:
    headers: dict[str, str] = {}
    for name, wire_name in SIGNATURE_HEADERS.items():
        values = request.headers.getlist(wire_name)
        if (
            len(values) != 1
            or not values[0].isascii()
            or not 1 <= len(values[0]) <= 256
        ):
            raise HTTPException(403, "外部通知签名头缺失、重复或格式无效。")
        headers[name] = values[0]
    media_types = request.headers.getlist("content-type")
    if len(media_types) != 1 or len(media_types[0]) > 128:
        raise HTTPException(415, "外部通知仅接受 UTF-8 JSON。")
    if request.headers.get("content-encoding") not in (None, "identity"):
        raise HTTPException(415, "外部通知不接受压缩请求体。")
    sizes = request.headers.getlist("content-length")
    if sizes:
        if len(sizes) != 1 or not sizes[0].isascii() or not sizes[0].isdigit():
            raise HTTPException(400, "外部通知长度声明无效。")
        if len(sizes[0]) > 10 or int(sizes[0]) > MAX_BODY_BYTES:
            raise HTTPException(413, "外部通知数据超过大小限制。")
    body = bytearray()
    async for piece in request.stream():
        if len(body) + len(piece) > MAX_BODY_BYTES:
            raise HTTPException(413, "外部通知数据超过大小限制。")
        body.extend(piece)
    return bytes(body), headers, media_types[0]
