"""Decode local image containers, then verify real image data with Pillow."""

from io import BytesIO
import struct

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from PIL import Image, UnidentifiedImageError

from .media_types import MAX_MEDIA_BYTES, MediaContent
from .hevc_stream import hevc_offset

V1 = b"\x07\x08V1\x08\x07"
V2 = b"\x07\x08V2\x08\x07"
MAGICS = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF8", b"RIFF")


def _verified(content: bytes) -> MediaContent:
    if not 0 < len(content) <= MAX_MEDIA_BYTES:
        raise ValueError("图片大小超出限制")
    try:
        with Image.open(BytesIO(content)) as image:
            if not 0 < image.width * image.height <= 25_000_000:
                raise ValueError("图片尺寸超出限制")
            image.load()
            if image.format in {"JPEG", "PNG"}:
                return MediaContent(
                    "image",
                    "image/jpeg" if image.format == "JPEG" else "image/png",
                    content,
                )
            if image.format not in {"GIF", "WEBP", "BMP"}:
                raise ValueError("暂不支持该图片编码")
            output = BytesIO()
            image.convert("RGB").save(output, format="PNG")
            normalized = output.getvalue()
            if len(normalized) > MAX_MEDIA_BYTES:
                raise ValueError("图片大小超出限制")
            return MediaContent("image", "image/png", normalized)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("无法验证完整图片") from error


def decode_image(
    data: bytes, aes_key: bytes | None = None, xor_key: int | None = None
) -> MediaContent:
    if not 0 < len(data) <= MAX_MEDIA_BYTES + 128:
        raise ValueError("图片文件大小无效")
    if data[:6] in (V1, V2):
        if len(data) < 15:
            raise ValueError("图片容器不完整")
        key = b"cfcd208495d565ef" if data[:6] == V1 else aes_key
        if key is None or len(key) != 16:
            raise ValueError("尚未核验本账号图片密钥")
        plain_size, tail_size = struct.unpack_from("<II", data, 6)
        encrypted_size = (plain_size // 16 + 1) * 16
        boundary = 15 + encrypted_size
        if not 0 < plain_size <= MAX_MEDIA_BYTES or boundary + tail_size > len(data):
            raise ValueError("图片容器长度无效")
        head = unpad(AES.new(key, AES.MODE_ECB).decrypt(data[15:boundary]), 16)
        if len(head) != plain_size:
            raise ValueError("图片密钥未通过核验")
        if not tail_size:
            return _verified(head + data[boundary:])
        if head.startswith(b"wxgf") and xor_key is not None:
            content = (
                head
                + data[boundary:-tail_size]
                + bytes(value ^ xor_key for value in data[-tail_size:])
            )
            hevc_offset(content)
            return MediaContent("image", "application/vnd.wechat.wxgf", content)
        endings = (
            (b"\xff\xd9",)
            if head.startswith(b"\xff\xd8\xff")
            else (b"IEND\xaeB`\x82",)
            if head.startswith(MAGICS[0])
            else (b";",)
            if head.startswith(b"GIF8")
            else ()
        )
        for ending in endings:
            key_byte = data[-1] ^ ending[-1]
            tail = bytes(value ^ key_byte for value in data[-tail_size:])
            if tail.endswith(ending):
                return _verified(head + data[boundary:-tail_size] + tail)
        raise ValueError("图片尾部未通过核验")
    try:
        return _verified(data)
    except ValueError:
        pass
    for magic in MAGICS:
        key_byte = data[0] ^ magic[0]
        if bytes(value ^ key_byte for value in data[: len(magic)]) == magic:
            return _verified(bytes(value ^ key_byte for value in data))
    raise ValueError("图片格式尚不支持或文件未下载完整")
