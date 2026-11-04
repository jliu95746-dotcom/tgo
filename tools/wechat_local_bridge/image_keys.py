"""Verify an image key against one authorized file, using read-only process access."""

from collections.abc import Callable
import hashlib
import json
from pathlib import Path
import re
import threading

from Crypto.Cipher import AES

from .image_media import MAGICS, decode_image
from .media_types import MediaNotReady
from .memory import ReadOnlyMemory
from Crypto.Util.Padding import unpad
import struct

_scan_lock = threading.Lock()


class ImageKeys:
    def __init__(
        self,
        key_file: Path,
        runtime: Path,
        account_root: Path,
        active: Callable[[], bool],
    ):
        self.key_file, self.active = key_file, active
        self.path = runtime / "image-media-key.private.json"
        self.account = hashlib.sha256(str(account_root.resolve()).encode()).hexdigest()
        self.username = re.sub(r"_[0-9a-fA-F]{4}$", "", account_root.name)
        self.xor_key: int | None = None

    def _from_config(
        self, memory: ReadOnlyMemory, block: bytes, base: int, data: bytes
    ) -> bytes | None:
        # Verified cfg layout: global_config SSO anchor and account string.
        # Reference: fanyuantaier/wechatauto-replica (Apache-2.0), db.py/media.py.
        def pointer(address: int) -> int:
            value = memory.read(address, 8)
            return struct.unpack("<Q", value)[0] if len(value) == 8 else 0

        position = block.find(b"global_config")
        while 0 <= position <= len(block) - 32:
            if struct.unpack_from("<QQ", block, position + 16) == (13, 15):
                for back in range(0x100, 0x181, 8):
                    root = pointer(base + position + 16 - back)
                    if not 0x10000 <= root < 0x800000000000:
                        continue
                    for offset in range(0x40, 0x91, 8):
                        cfg = pointer(root + offset)
                        if not 0x10000 <= cfg < 0x800000000000:
                            continue
                        value = memory.read(cfg + 0x40, 40)
                        if len(value) != 40 or struct.unpack_from("<Q", value, 24)[
                            0
                        ] != len(self.username.encode()):
                            continue
                        size = len(self.username.encode())
                        name = (
                            value[8 : 8 + size]
                            if size <= 15
                            else memory.read(
                                struct.unpack_from("<Q", value, 8)[0], size
                            )
                        )
                        if name != self.username.encode():
                            continue
                        number = struct.unpack_from("<I", value)[0]
                        key = (
                            hashlib.md5((str(number) + self.username).encode())
                            .hexdigest()[:16]
                            .encode("ascii")
                        )
                        if self.verifies(data, key):
                            self.xor_key = number & 255
                            return key
            position = block.find(b"global_config", position + 1)
        return None

    def _save(self, key: bytes) -> bytes:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"account": self.account, "key": key.hex(), "xor": self.xor_key}
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)
        return key

    @staticmethod
    def verifies(data: bytes, key: bytes) -> bool:
        if len(key) != 16 or len(data) < 31:
            return False
        head = AES.new(key, AES.MODE_ECB).decrypt(data[15:31])
        if not any(head.startswith(magic) for magic in (*MAGICS, b"wxgf")):
            return False
        if head.startswith(b"wxgf"):
            size = struct.unpack_from("<I", data, 6)[0]
            end = 15 + (size // 16 + 1) * 16
            if end > len(data):
                return False
            try:
                return (
                    len(unpad(AES.new(key, AES.MODE_ECB).decrypt(data[15:end]), 16))
                    == size
                )
            except ValueError:
                return False
        try:
            decode_image(data, key)
            return True
        except ValueError:
            return False

    def get(self, data: bytes) -> bytes:
        with _scan_lock:
            if not self.active():
                raise MediaNotReady("接待权限已暂停，停止读取媒体")
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                candidate = bytes.fromhex(saved["key"])
                if saved["account"] == self.account and self.verifies(data, candidate):
                    self.xor_key = saved.get("xor")
                    return candidate
            except (OSError, ValueError, KeyError, TypeError):
                pass
            pid = int(json.loads(self.key_file.read_text(encoding="utf-8"))["pid"])
            memory = ReadOnlyMemory(pid, timeout=30)
            seen: set[bytes] = set()
            try:
                regions = memory.regions()
                for address, length in regions:
                    overlap = b""
                    for offset in range(0, length, 2 * 1024**2):
                        if not self.active():
                            raise MediaNotReady("接待权限已暂停，停止读取媒体")
                        prefix = overlap
                        block = prefix + memory.read(
                            address + offset, min(2 * 1024**2, length - offset)
                        )
                        overlap = block[-512:]
                        candidate = self._from_config(
                            memory, block, address + offset - len(prefix), data
                        )
                        if candidate is not None:
                            return self._save(candidate)
                        if memory.read_bytes > 2 * 1024**3:
                            break
                    if memory.read_bytes > 2 * 1024**3:
                        break
                for region in regions:
                    address, length = region
                    overlap = b""
                    for offset in range(0, length, 2 * 1024**2):
                        if not self.active():
                            raise MediaNotReady("接待权限已暂停，停止读取媒体")
                        block = overlap + memory.read(
                            address + offset, min(2 * 1024**2, length - offset)
                        )
                        overlap = block[-64:]
                        values = [
                            match.group()
                            for match in re.finditer(rb"[0-9a-zA-Z]{16,64}", block)
                        ]
                        values += [
                            match.group()[::2]
                            for match in re.finditer(rb"(?:[0-9a-zA-Z]\x00){32}", block)
                        ]
                        for value in values:
                            candidates = [value[:16]]
                            if len(value) >= 32 and re.fullmatch(
                                rb"[0-9a-fA-F]{32}", value[:32]
                            ):
                                candidates.append(
                                    bytes.fromhex(value[:32].decode("ascii"))
                                )
                            for candidate in candidates:
                                if candidate in seen:
                                    continue
                                seen.add(candidate)
                                if self.verifies(data, candidate):
                                    return self._save(candidate)
                        if memory.read_bytes > 4 * 1024**3:
                            raise MediaNotReady("本轮图片取钥已达到读取上限，稍后重试")
            except (OSError, TimeoutError) as error:
                raise MediaNotReady("图片密钥暂未就绪，等待微信下载并打开图片") from error
            finally:
                memory.close()
        raise MediaNotReady("尚未找到可核验的图片密钥，等待微信图片就绪")
