"""API v3 byte-exact RSA verification and authenticated resource decryption."""

import base64
import time
from collections.abc import Mapping

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, Field


class EncryptedResource(BaseModel):
    algorithm: str
    ciphertext: str = Field(max_length=131072)
    nonce: str = Field(min_length=1, max_length=64)
    associated_data: str = Field(default="", max_length=1024)
    original_type: str = ""


def load_public_key(pem: bytes) -> rsa.RSAPublicKey:
    key: object
    if b"BEGIN CERTIFICATE" in pem:
        from cryptography.x509 import load_pem_x509_certificate

        key = load_pem_x509_certificate(pem).public_key()
    else:
        key = serialization.load_pem_public_key(pem)
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
        raise ValueError("WeChat Pay requires an RSA public key of at least 2048 bits")
    return key


def verify_message(
    body: bytes,
    headers: Mapping[str, str],
    keys: Mapping[str, rsa.RSAPublicKey],
    *,
    now: int | None = None,
) -> None:
    normalized = {key.lower(): value for key, value in headers.items()}
    timestamp = normalized.get("wechatpay-timestamp", "")
    nonce = normalized.get("wechatpay-nonce", "")
    serial = normalized.get("wechatpay-serial", "")
    signature = normalized.get("wechatpay-signature", "")
    if (
        not timestamp.isdigit()
        or abs((now if now is not None else int(time.time())) - int(timestamp)) > 300
    ):
        raise ValueError("Invalid WeChat Pay timestamp")
    if not nonce or len(nonce) > 256 or serial not in keys or len(body) > 131072:
        raise ValueError("Invalid WeChat Pay signature headers")
    signed = timestamp.encode() + b"\n" + nonce.encode() + b"\n" + body + b"\n"
    keys[serial].verify(
        base64.b64decode(signature, validate=True),
        signed,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def decrypt_resource(resource: EncryptedResource, key: bytes) -> bytes:
    if resource.algorithm != "AEAD_AES_256_GCM" or len(key) != 32:
        raise ValueError("Invalid WeChat Pay encryption configuration")
    return AESGCM(key).decrypt(
        resource.nonce.encode(),
        base64.b64decode(resource.ciphertext, validate=True),
        resource.associated_data.encode(),
    )


def sign_request(
    private_key: rsa.RSAPrivateKey,
    method: str,
    path: str,
    body: bytes,
    timestamp: str,
    nonce: str,
) -> str:
    message = f"{method}\n{path}\n{timestamp}\n{nonce}\n".encode() + body + b"\n"
    return base64.b64encode(
        private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    ).decode()
