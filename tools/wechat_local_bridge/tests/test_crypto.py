import hashlib
import hmac
import struct
import unittest

from Crypto.Cipher import AES

from tools.wechat_local_bridge.crypto import decrypt_database, merge_wal, wal_checksum

KEY = bytes(range(32))
SALT = bytes(range(16))


def encrypted_page(number, fill=65):
    size = 4000 if number == 1 else 4016
    plain = bytes([fill]) * size
    iv = bytes([number]) * 16
    cipher = AES.new(KEY, AES.MODE_CBC, iv).encrypt(plain)
    payload = (SALT if number == 1 else b"") + cipher + iv
    mac_key = hashlib.pbkdf2_hmac("sha512", KEY, bytes(x ^ 0x3A for x in SALT), 2, 32)
    signed = payload[16:] if number == 1 else payload
    return (
        payload
        + hmac.new(mac_key, signed + struct.pack("<I", number), hashlib.sha512).digest()
    )


def wal_bytes(frames):
    head = struct.pack(">6I", 0x377F0682, 3007000, 4096, 0, 42, 43)
    checksum = wal_checksum(head)
    result = head + struct.pack(">2I", *checksum)
    for number, db_size, page in frames:
        fields = struct.pack(">4I", number, db_size, 42, 43)
        checksum = wal_checksum(fields[:8] + page, checksum)
        result += fields + struct.pack(">2I", *checksum) + page
    return result


class CipherTests(unittest.TestCase):
    def test_authenticated_page_and_wrong_key(self):
        raw = encrypted_page(1)
        plain = decrypt_database(raw, KEY)
        self.assertEqual(plain[:16], b"SQLite format 3\x00")
        self.assertEqual(plain[16:4016], b"A" * 4000)
        with self.assertRaisesRegex(ValueError, "HMAC"):
            decrypt_database(raw, b"x" * 32)

    def test_only_committed_wal_frames_are_applied(self):
        main = encrypted_page(1) + encrypted_page(2)
        wal = wal_bytes([(2, 2, encrypted_page(2, 66)), (2, 0, encrypted_page(2, 67))])
        merged = merge_wal(main, wal, KEY)
        self.assertEqual(merged[4096 : 4096 + 4016], b"B" * 4016)

    def test_corrupt_wal_header_fails(self):
        wal = bytearray(wal_bytes([(1, 1, encrypted_page(1))]))
        wal[24] ^= 1
        with self.assertRaisesRegex(ValueError, "checksum"):
            merge_wal(encrypted_page(1), bytes(wal), KEY)

    def test_valid_checksum_does_not_hide_page_hmac_failure(self):
        page = bytearray(encrypted_page(1))
        page[50] ^= 1
        wal = wal_bytes([(1, 1, bytes(page))])
        with self.assertRaisesRegex(ValueError, "HMAC"):
            merge_wal(encrypted_page(1), wal, KEY)


if __name__ == "__main__":
    unittest.main()
