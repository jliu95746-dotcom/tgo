"""SQLCipher4 authenticated read-only snapshots. Never checkpoint the source."""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
import struct
import time
from pathlib import Path

from Crypto.Cipher import AES

PAGE_SIZE = 4096


def authentication_key(key: bytes, salt: bytes) -> bytes:
    if len(key) != 32 or len(salt) != 16:
        raise ValueError("密钥或 salt 长度错误")
    return hashlib.pbkdf2_hmac("sha512", key, bytes(v ^ 0x3A for v in salt), 2, 32)


def decrypt_page(page: bytes, number: int, key: bytes, mac_key: bytes) -> bytes:
    if len(page) != PAGE_SIZE or number < 1:
        raise ValueError("数据库页长度或页号错误")
    start = 16 if number == 1 else 0
    expected = hmac.new(
        mac_key, page[start:4032] + struct.pack("<I", number), hashlib.sha512
    ).digest()
    if not hmac.compare_digest(expected, page[4032:]):
        raise ValueError(f"第 {number} 页 HMAC 校验失败")
    plain = AES.new(key, AES.MODE_CBC, page[4016:4032]).decrypt(page[start:4016])
    return (b"SQLite format 3\x00" if number == 1 else b"") + plain + bytes(80)


def decrypt_database(encrypted: bytes, key: bytes) -> bytes:
    if not encrypted or len(encrypted) % PAGE_SIZE:
        raise ValueError("加密数据库长度不是完整页")
    mac_key = authentication_key(key, encrypted[:16])
    return b"".join(
        decrypt_page(
            encrypted[offset : offset + PAGE_SIZE],
            offset // PAGE_SIZE + 1,
            key,
            mac_key,
        )
        for offset in range(0, len(encrypted), PAGE_SIZE)
    )


def wal_checksum(
    data: bytes, seed: tuple[int, int] = (0, 0), endian: str = "<"
) -> tuple[int, int]:
    if len(data) % 8:
        raise ValueError("WAL checksum 数据长度错误")
    values = struct.unpack(endian + str(len(data) // 4) + "I", data)
    left, right = seed
    for offset in range(0, len(values), 2):
        left = (left + values[offset] + right) & 0xFFFFFFFF
        right = (right + values[offset + 1] + left) & 0xFFFFFFFF
    return left, right


def merge_wal(main: bytes, wal: bytes, key: bytes, shm: bytes = b"") -> bytes:
    plain = decrypt_database(main, key)
    if not wal:
        return plain
    if len(wal) < 32:
        raise ValueError("WAL 头不完整")
    header = struct.unpack(">8I", wal[:32])
    if (
        header[0] not in (0x377F0682, 0x377F0683)
        or header[1] != 3007000
        or header[2] != PAGE_SIZE
    ):
        raise ValueError("WAL magic/version/page size 不支持")
    endian = "<" if header[0] == 0x377F0682 else ">"
    seed = wal_checksum(wal[:24], endian=endian)
    if seed != header[6:]:
        raise ValueError("WAL header checksum 错误")
    frames: list[tuple[int, int, bytes]] = []
    last_commit = 0
    mac_key = authentication_key(key, main[:16])
    for offset in range(32, len(wal) - 4119, 4120):
        fields = struct.unpack(">6I", wal[offset : offset + 24])
        if fields[2:4] != header[4:6]:
            break
        page = wal[offset + 24 : offset + 4120]
        checksum = wal_checksum(wal[offset : offset + 8] + page, seed, endian)
        if checksum != fields[4:]:
            break  # A concurrently appended / incomplete tail is not a commit.
        seed = checksum
        decoded = decrypt_page(page, fields[0], key, mac_key)
        frames.append((fields[0], fields[1], decoded))
        if fields[1]:
            last_commit = len(frames)
    if (
        len(shm) >= 136
        and shm[:48] == shm[48:96]
        and shm[32:40] == wal[16:24]
        and shm[12]
        and struct.unpack_from("<I", shm, 16)[0] > last_commit
    ):
        raise ValueError("WAL/shm 提交边界不一致，稍后重试")
    if not last_commit:
        return plain
    size = frames[last_commit - 1][1] * PAGE_SIZE
    result = bytearray(plain[:size])
    result.extend(bytes(max(0, size - len(result))))
    for number, _size, page in frames[:last_commit]:
        if number * PAGE_SIZE <= size:
            result[(number - 1) * PAGE_SIZE : number * PAGE_SIZE] = page
    return bytes(result)


def open_snapshot(path: Path, key: bytes, attempts: int = 4) -> sqlite3.Connection:
    """Best-effort stable DB/WAL/shm copy with cryptographic/SQLite validation."""
    paths = [path, Path(str(path) + "-wal"), Path(str(path) + "-shm")]
    for attempt in range(attempts):
        before = {p: p.read_bytes() for p in paths if p.is_file()}
        after = {p: p.read_bytes() for p in paths if p.is_file()}
        if before != after:
            if attempt + 1 < attempts:
                time.sleep(0.05)
                continue
            raise ValueError("微信数据库持续变化，未取得可验证快照")
        plain = merge_wal(
            before[path], before.get(paths[1], b""), key, before.get(paths[2], b"")
        )
        memory = bytearray(plain)
        memory[18:20] = b"\x01\x01"  # In-memory SQLite cannot load WAL mode directly.
        connection = sqlite3.connect(":memory:")
        try:
            connection.deserialize(bytes(memory))
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("SQLite integrity_check 失败")
            connection.row_factory = sqlite3.Row
            return connection
        except Exception:
            connection.close()
            raise
    raise ValueError("未取得数据库快照")
