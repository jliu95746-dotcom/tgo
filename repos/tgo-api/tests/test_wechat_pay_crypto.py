"""Synthetic keys only: mutation, replay windows and untrusted serials must fail."""

import base64

import pytest
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.services.wechat_pay_crypto import (
    EncryptedResource,
    decrypt_resource,
    sign_request,
    verify_message,
)


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def signed_headers(key, body):
    signature = key.sign(
        b"1000\nsynthetic-nonce\n" + body + b"\n", padding.PKCS1v15(), hashes.SHA256()
    )
    return {
        "Wechatpay-Timestamp": "1000",
        "Wechatpay-Nonce": "synthetic-nonce",
        "Wechatpay-Serial": "synthetic-serial",
        "Wechatpay-Signature": base64.b64encode(signature).decode(),
    }


def test_raw_body_signature_and_tamper_detection(rsa_key):
    body = b'{"amount": 100}'
    headers = signed_headers(rsa_key, body)
    verify_message(body, headers, {"synthetic-serial": rsa_key.public_key()}, now=1000)
    with pytest.raises(InvalidSignature):
        verify_message(
            body.replace(b"100", b"101"),
            headers,
            {"synthetic-serial": rsa_key.public_key()},
            now=1000,
        )
    with pytest.raises(ValueError):
        verify_message(body, headers, {}, now=1000)
    with pytest.raises(ValueError):
        verify_message(
            body, headers, {"synthetic-serial": rsa_key.public_key()}, now=1301
        )


def test_authenticated_decryption_rejects_modified_data():
    key, nonce, aad = b"s" * 32, b"synthetic123", b"transaction"
    encrypted = AESGCM(key).encrypt(nonce, b'{"trade_state":"SUCCESS"}', aad)
    resource = EncryptedResource(
        algorithm="AEAD_AES_256_GCM",
        nonce=nonce.decode(),
        associated_data=aad.decode(),
        ciphertext=base64.b64encode(encrypted).decode(),
    )
    assert decrypt_resource(resource, key) == b'{"trade_state":"SUCCESS"}'
    resource.associated_data = "tampered"
    with pytest.raises(InvalidTag):
        decrypt_resource(resource, key)


def test_request_signature_includes_query_and_final_newline(rsa_key):
    signature = sign_request(
        rsa_key, "GET", "/v3/example?mchid=123", b"", "1000", "nonce"
    )
    rsa_key.public_key().verify(
        base64.b64decode(signature),
        b"GET\n/v3/example?mchid=123\n1000\nnonce\n\n",
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
