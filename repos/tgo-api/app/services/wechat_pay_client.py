"""Native payments using configured merchant credentials and pinned provider keys."""

import json
import logging
import re
import time
from datetime import date
from hashlib import sha1
from datetime import datetime
from pathlib import Path
from secrets import token_hex
from urllib.parse import quote, urlencode, urlsplit

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import BaseModel, Field, JsonValue, TypeAdapter

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.services.wechat_pay_crypto import (
    EncryptedResource,
    decrypt_resource,
    load_public_key,
    sign_request,
    verify_message,
)
from app.schemas.billing_refunds import ProviderRefund

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class WeChatPayError(TGOAPIException):
    def __init__(self, provider_code: str) -> None:
        super().__init__(
            "微信支付暂不可用，请稍后重试或联系平台", code="WECHAT_PAY_UNAVAILABLE", status_code=503
        )
        self.provider_code = provider_code


class PaymentAmount(BaseModel):
    total: int = Field(ge=0, strict=True)
    currency: str


class PaymentTransaction(BaseModel):
    mchid: str
    appid: str
    out_trade_no: str
    trade_state: str
    transaction_id: str | None = None
    success_time: datetime | None = None
    amount: PaymentAmount | None = None


class PaymentNotification(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    event_type: str
    resource_type: str
    resource: EncryptedResource


def unavailable() -> TGOAPIException:
    return TGOAPIException(
        "微信支付暂不可用，请稍后重试或联系平台", code="WECHAT_PAY_UNAVAILABLE", status_code=503
    )


class WeChatPayClient:
    def __init__(self) -> None:
        callback = urlsplit(settings.WECHAT_PAY_NOTIFY_URL)
        base = urlsplit(settings.WECHAT_PAY_API_BASE_URL)
        if (
            not settings.WECHAT_PAY_MCH_ID
            or not settings.WECHAT_PAY_APP_ID
            or not settings.WECHAT_PAY_CERT_SERIAL
            or not settings.WECHAT_PAY_PRIVATE_KEY_FILE
            or not settings.WECHAT_PAY_TRUSTED_KEYS
            or settings.WECHAT_PAY_API_V3_KEY is None
            or callback.scheme != "https"
            or not callback.hostname
            or callback.query
            or callback.fragment
            or callback.username
            or base.scheme != "https"
            or base.hostname != "api.mch.weixin.qq.com"
            or base.path not in {"", "/"}
            or base.query
            or base.fragment
            or base.username
            or base.port not in {None, 443}
        ):
            raise unavailable()
        try:
            private = serialization.load_pem_private_key(
                Path(settings.WECHAT_PAY_PRIVATE_KEY_FILE).read_bytes(), password=None
            )
            if not isinstance(private, rsa.RSAPrivateKey) or private.key_size < 2048:
                raise ValueError("Invalid merchant private key")
            self.private_key = private
            self.keys = {
                serial: load_public_key(Path(path).read_bytes())
                for serial, path in settings.WECHAT_PAY_TRUSTED_KEYS.items()
            }
            self.api_key = settings.WECHAT_PAY_API_V3_KEY.get_secret_value().encode()
            if len(self.api_key) != 32:
                raise ValueError("Invalid API v3 key")
        except (ValueError, OSError) as exc:
            raise unavailable() from exc

    def request(
        self, method: str, path: str, payload: dict[str, JsonValue] | None = None
    ) -> dict[str, JsonValue]:
        body = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            if payload is not None
            else b""
        )
        timestamp, nonce = str(int(time.time())), token_hex(16)
        signature = sign_request(self.private_key, method, path, body, timestamp, nonce)
        authorization = (
            "WECHATPAY2-SHA256-RSA2048 "
            f'mchid="{settings.WECHAT_PAY_MCH_ID}",nonce_str="{nonce}",'
            f'signature="{signature}",timestamp="{timestamp}",'
            f'serial_no="{settings.WECHAT_PAY_CERT_SERIAL}"'
        )
        try:
            with httpx.Client(
                timeout=20, follow_redirects=False, trust_env=False
            ) as client:
                response = client.request(
                    method,
                    settings.WECHAT_PAY_API_BASE_URL.rstrip("/") + path,
                    content=body,
                    headers={
                        "Authorization": authorization,
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "User-Agent": "Yujian-Billing/1",
                    },
                )
            verify_message(response.content, response.headers, self.keys)
            if response.status_code not in {200, 204}:
                error = TypeAdapter(dict[str, JsonValue]).validate_json(
                    response.content
                )
                code = error.get("code")
                raise WeChatPayError(
                    code
                    if isinstance(code, str) and re.fullmatch(r"[A-Z_]{1,60}", code)
                    else "UNKNOWN"
                )
            if not response.content:
                return {}
            return TypeAdapter(dict[str, JsonValue]).validate_json(response.content)
        except TGOAPIException:
            raise
        except Exception as exc:
            # Never expose provider bodies, signatures or credential paths to customers.
            raise unavailable() from exc

    def native(self, number: str, amount: int, expires_at: datetime) -> str:
        result = self.request(
            "POST",
            "/v3/pay/transactions/native",
            {
                "mchid": settings.WECHAT_PAY_MCH_ID,
                "appid": settings.WECHAT_PAY_APP_ID,
                "description": "域见企业客服订阅",
                "out_trade_no": number,
                "time_expire": expires_at.isoformat(),
                "notify_url": settings.WECHAT_PAY_NOTIFY_URL,
                "amount": {"total": amount, "currency": "CNY"},
            },
        )
        url = result.get("code_url")
        if (
            not isinstance(url, str)
            or not url.startswith("weixin://wxpay/")
            or len(url) > 1024
        ):
            raise unavailable()
        return url

    def query(self, number: str) -> PaymentTransaction:
        result = self.request(
            "GET",
            f"/v3/pay/transactions/out-trade-no/{quote(number, safe='')}?"
            + urlencode({"mchid": settings.WECHAT_PAY_MCH_ID}),
        )
        transaction = PaymentTransaction.model_validate(result)
        self.validate_merchant(transaction)
        if transaction.out_trade_no != number:
            raise unavailable()
        return transaction

    def close(self, number: str) -> None:
        self.request(
            "POST",
            f"/v3/pay/transactions/out-trade-no/{quote(number, safe='')}/close",
            {"mchid": settings.WECHAT_PAY_MCH_ID},
        )

    def decode_notification(
        self, raw: bytes, headers: dict[str, str]
    ) -> tuple[str, PaymentTransaction]:
        verify_message(raw, headers, self.keys)
        notification = PaymentNotification.model_validate_json(raw)
        if (
            notification.event_type != "TRANSACTION.SUCCESS"
            or notification.resource_type != "encrypt-resource"
        ):
            raise ValueError("Unexpected payment notification type")
        transaction = PaymentTransaction.model_validate_json(
            decrypt_resource(notification.resource, self.api_key)
        )
        self.validate_merchant(transaction)
        return notification.id, transaction

    @staticmethod
    def validate_merchant(transaction: PaymentTransaction) -> None:
        if (
            transaction.mchid != settings.WECHAT_PAY_MCH_ID
            or transaction.appid != settings.WECHAT_PAY_APP_ID
        ):
            raise ValueError("Payment merchant mismatch")
        if transaction.trade_state == "SUCCESS" and (
            transaction.transaction_id is None
            or transaction.success_time is None
            or transaction.success_time.tzinfo is None
            or transaction.amount is None
        ):
            raise ValueError("Incomplete successful payment")

    def request_refund(
        self,
        order_number: str,
        refund_number: str,
        total: int,
        refund: int,
        reason: str,
    ) -> ProviderRefund:
        callback = urlsplit(settings.WECHAT_PAY_REFUND_NOTIFY_URL)
        if (
            callback.scheme != "https"
            or not callback.hostname
            or callback.query
            or callback.fragment
            or callback.username
        ):
            raise unavailable()
        paid = self.query(order_number)
        if (
            paid.trade_state != "SUCCESS"
            or paid.amount is None
            or paid.amount.total != total
        ):
            raise unavailable()
        result = self.request(
            "POST",
            "/v3/refund/domestic/refunds",
            {
                "out_trade_no": order_number,
                "out_refund_no": refund_number,
                "reason": reason.encode()[:80].decode(errors="ignore"),
                "notify_url": settings.WECHAT_PAY_REFUND_NOTIFY_URL,
                "amount": {"refund": refund, "total": total, "currency": "CNY"},
            },
        )
        return ProviderRefund.model_validate(result)

    def query_refund(self, refund_number: str) -> ProviderRefund:
        return ProviderRefund.model_validate(
            self.request(
                "GET", f"/v3/refund/domestic/refunds/{quote(refund_number, safe='')}"
            )
        )

    def decode_refund_notification(
        self, raw: bytes, headers: dict[str, str]
    ) -> ProviderRefund:
        verify_message(raw, headers, self.keys)
        notification = PaymentNotification.model_validate_json(raw)
        if (
            notification.resource_type != "encrypt-resource"
            or notification.event_type
            not in {"REFUND.SUCCESS", "REFUND.CLOSED", "REFUND.ABNORMAL"}
        ):
            raise ValueError("Unexpected refund notification")
        result = ProviderRefund.model_validate_json(
            decrypt_resource(notification.resource, self.api_key)
        )
        if (
            result.mchid != settings.WECHAT_PAY_MCH_ID
            or notification.event_type != f"REFUND.{result.status}"
        ):
            raise ValueError("Refund merchant or state mismatch")
        return result

    def download_trade_bill(self, bill_date: date) -> tuple[bytes, str]:
        result = self.request(
            "GET",
            "/v3/bill/tradebill?"
            + urlencode({"bill_date": bill_date.isoformat(), "bill_type": "ALL"}),
        )
        url, expected_hash = result.get("download_url"), result.get("hash_value")
        if (
            not isinstance(url, str)
            or not isinstance(expected_hash, str)
            or result.get("hash_type") != "SHA1"
        ):
            raise unavailable()
        location = urlsplit(url)
        if (
            location.scheme != "https"
            or location.hostname != "api.mch.weixin.qq.com"
            or location.port not in {None, 443}
            or location.username
            or location.fragment
            or location.path not in {"/v3/billdownload/file", "/v3/bill/downloadurl"}
        ):
            raise unavailable()
        path = location.path + ("?" + location.query if location.query else "")
        timestamp, nonce = str(int(time.time())), token_hex(16)
        signature = sign_request(self.private_key, "GET", path, b"", timestamp, nonce)
        authorization = (
            "WECHATPAY2-SHA256-RSA2048 "
            f'mchid="{settings.WECHAT_PAY_MCH_ID}",nonce_str="{nonce}",signature="{signature}",'
            f'timestamp="{timestamp}",serial_no="{settings.WECHAT_PAY_CERT_SERIAL}"'
        )
        content = bytearray()
        with httpx.Client(
            timeout=30, follow_redirects=False, trust_env=False
        ) as client:
            with client.stream(
                "GET",
                url,
                headers={
                    "Authorization": authorization,
                    "User-Agent": "Yujian-Billing/1",
                },
            ) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 50 * 1024 * 1024:
                        raise ValueError("Trade statement exceeds processing limit")
        # Bill downloads intentionally have no Wechatpay-Signature header.
        # Integrity comes from the hash in the verified bill application response.
        digest = sha1(content).hexdigest()
        if digest != expected_hash.lower():
            raise ValueError("Trade statement hash mismatch")
        return bytes(content), digest
