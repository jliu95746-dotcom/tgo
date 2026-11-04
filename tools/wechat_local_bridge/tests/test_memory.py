import hashlib
import unittest

from test_crypto import KEY, SALT, encrypted_page

from tools.wechat_local_bridge import _scanner_vendor as scanner


class MemoryTests(unittest.TestCase):
    def test_candidate_must_validate_against_authorized_database(self):
        literal = ("x'" + KEY.hex() + SALT.hex() + "'").encode()
        blob = bytes(
            value ^ scanner.WINDOWS_CONFIG_XOR_MASK[i % 32]
            for i, value in enumerate(literal)
        )
        candidates = scanner._windows_v411_config_key_candidates(blob)
        self.assertIn((KEY.hex(), SALT.hex()), candidates)
        key_map = {}
        salts = {SALT.hex()}
        files = [("message_0.db", "unused", 4096, SALT.hex(), encrypted_page(1))]
        hits = scanner._verify_windows_direct_key_candidate(
            KEY.hex(), SALT.hex(), files, {}, key_map, salts
        )
        self.assertEqual(hits, 1)
        self.assertEqual(salts, set())
        wrong_map = {}
        wrong_salts = {SALT.hex()}
        scanner._verify_windows_direct_key_candidate(
            hashlib.sha256(b"wrong").hexdigest(),
            SALT.hex(),
            files,
            {},
            wrong_map,
            wrong_salts,
        )
        self.assertEqual(wrong_map, {})

    def test_pattern_crossing_a_memory_chunk_boundary_is_found(self):
        memory = b"a" * 15 + b"PATTERN" + b"b" * 20
        parts = list(
            scanner._iter_windows_region_chunks(
                [(0, len(memory))],
                lambda address, size: memory[address : address + size],
                chunk_size=16,
                overlap=6,
            )
        )
        self.assertTrue(any(b"PATTERN" in chunk for _, chunk in parts))


if __name__ == "__main__":
    unittest.main()
