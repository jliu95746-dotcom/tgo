from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.utils.wecom_crypto import decrypt_wecom_echostr


def encrypt_echostr(message: str, encoding_aes_key: str, receive_id: str) -> str:
    aes_key = base64.b64decode(encoding_aes_key + "=")
    plain = (
        b"0123456789abcdef"
        + len(message.encode("utf-8")).to_bytes(4, "big")
        + message.encode("utf-8")
        + receive_id.encode("utf-8")
    )
    pad_length = 32 - len(plain) % 32
    padded = plain + bytes([pad_length]) * pad_length
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16]))
    encryptor = cipher.encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode("ascii")


def test_decrypt_wecom_echostr_validates_receive_id() -> None:
    encoding_aes_key = base64.b64encode(bytes(range(32))).decode("ascii").rstrip("=")
    echostr = encrypt_echostr("verified", encoding_aes_key, "expected-corp")

    assert decrypt_wecom_echostr(echostr, encoding_aes_key, "expected-corp") == "verified"
    with pytest.raises(ValueError, match="receive id"):
        decrypt_wecom_echostr(echostr, encoding_aes_key, "wrong-corp")
