"""WeCom callback cryptography helpers."""

import base64

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def decrypt_wecom_echostr(
    echostr: str,
    encoding_aes_key: str,
    expected_receive_id: str,
) -> str:
    """Decrypt and validate a WeCom URL verification payload."""
    aes_key = base64.b64decode(encoding_aes_key + "=")
    if len(aes_key) != 32:
        raise ValueError("Invalid WeCom AES key")
    cipher_text = base64.b64decode(echostr)
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16]))
    decryptor = cipher.decryptor()
    plain_padded = decryptor.update(cipher_text) + decryptor.finalize()
    if not plain_padded:
        raise ValueError("Empty WeCom verification payload")
    pad_length = plain_padded[-1]
    if pad_length < 1 or pad_length > 32:
        raise ValueError("Invalid WeCom verification padding")
    if plain_padded[-pad_length:] != bytes([pad_length]) * pad_length:
        raise ValueError("Invalid WeCom verification padding")
    plain = plain_padded[:-pad_length]
    if len(plain) < 20:
        raise ValueError("WeCom verification plaintext is too short")
    message_length = int.from_bytes(plain[16:20], byteorder="big")
    message_end = 20 + message_length
    if message_end > len(plain):
        raise ValueError("Invalid WeCom verification message length")
    receive_id = plain[message_end:].decode("utf-8")
    if expected_receive_id and receive_id != expected_receive_id:
        raise ValueError("WeCom receive id mismatch")
    return plain[20:message_end].decode("utf-8")
