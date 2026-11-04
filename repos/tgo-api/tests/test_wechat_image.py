from types import SimpleNamespace
import subprocess

import pytest

from app.services.wechat_image import WechatImageError, decode_wechat_image

CONTAINER = b"wxgf-fixture" + b"\x00\x00\x00\x01\x40\x01" + b"fixture" * 10


@pytest.mark.parametrize(
    "content", [b"", b"JPEG", b"wxgf-incomplete", CONTAINER.replace(b"\x40", b"\x10")]
)
def test_unsupported_container_does_not_start_decoder(monkeypatch, content):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: pytest.fail("invalid container reached decoder"),
    )
    with pytest.raises(WechatImageError):
        decode_wechat_image(content)


def test_normalized_image_and_timeout(monkeypatch):
    jpeg = b"\xff\xd8\xfffixture\xff\xd9"

    def decode(args, **kwargs):
        assert (
            "-I" in args
            and kwargs["timeout"] == 15
            and kwargs["input"].startswith(b"\x00\x00\x00\x01")
        )
        return SimpleNamespace(stdout=jpeg)

    monkeypatch.setattr(subprocess, "run", decode)
    assert decode_wechat_image(CONTAINER) == jpeg

    def fail(*_a, **_k):
        raise subprocess.TimeoutExpired("decoder", 15)

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(WechatImageError, match="重发"):
        decode_wechat_image(CONTAINER)
