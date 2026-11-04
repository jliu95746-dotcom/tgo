import unittest

from tools.wechat_local_bridge.hevc_stream import hevc_offset


def picture() -> bytes:
    return b"".join(
        b"\x00\x00\x00\x01" + bytes([kind << 1, 1]) + b"payload"
        for kind in (32, 33, 34, 19)
    )


class HevcStreamTests(unittest.TestCase):
    def test_skips_metadata_at_original_image_offsets(self):
        for offset in (42, 601):
            metadata = b"wxgf" + b"x" * 24 + b"\x00\x00\x00\x01\x2c\x01"
            metadata = metadata.ljust(offset, b"x")
            self.assertEqual(hevc_offset(metadata + picture()), offset)

    def test_skips_fake_vps_without_parameter_chain(self):
        fake = b"wxgf\x00\x00\x00\x01\x40\x01wrong\x00\x00\x01\x26\x01x"
        self.assertEqual(hevc_offset(fake + picture()), len(fake))

    def test_truncated_or_invalid_headers_do_not_pass(self):
        for raw in (
            b"wxgf" + picture()[:-18],
            b"wxgf" + picture().replace(b"\x40\x01", b"\x40\x00"),
        ):
            with self.assertRaises(ValueError):
                hevc_offset(raw)
