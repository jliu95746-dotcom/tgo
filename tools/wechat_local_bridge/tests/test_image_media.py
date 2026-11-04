from io import BytesIO
import struct
import unittest

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from PIL import Image

from tools.wechat_local_bridge.image_media import decode_image


class ImageMediaTests(unittest.TestCase):
    def setUp(self):
        buffer = BytesIO()
        Image.new("RGB", (12, 8), "red").save(buffer, format="PNG")
        self.image = buffer.getvalue()
        self.key = b"fixture-imagekey"

    def encrypted(self, magic=b"\x07\x08V2\x08\x07", key=None):
        key = key or self.key
        head, tail = 16, 12
        return (
            magic
            + struct.pack("<II", head, tail)
            + b"\0"
            + AES.new(key, AES.MODE_ECB).encrypt(pad(self.image[:head], 16))
            + self.image[head:-tail]
            + bytes(x ^ 0x43 for x in self.image[-tail:])
        )

    def test_v2_image_is_decrypted_and_validated_without_changing_source(self):
        source = self.encrypted()
        result = decode_image(source, self.key)
        self.assertEqual(result.content, self.image)
        self.assertEqual(result.mime_type, "image/png")
        self.assertEqual(source, self.encrypted())

    def test_legacy_xor_and_plain_images_are_supported(self):
        self.assertEqual(
            decode_image(bytes(x ^ 0x71 for x in self.image)).content, self.image
        )
        self.assertEqual(decode_image(self.image).content, self.image)

    def test_wrong_key_and_truncated_container_fail_closed(self):
        for source, key in (
            (self.encrypted(), b"anotherimagekey!"),
            (self.encrypted()[:20], self.key),
        ):
            with self.assertRaises(ValueError):
                decode_image(source, key)

    def test_v1_uses_only_its_documented_format_key(self):
        source = self.encrypted(b"\x07\x08V1\x08\x07", b"cfcd208495d565ef")
        self.assertEqual(decode_image(source).content, self.image)
