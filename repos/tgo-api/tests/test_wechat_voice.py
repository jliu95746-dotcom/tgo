import io
import struct
import subprocess
import wave
from types import SimpleNamespace

import pytest

from app.services.wechat_voice import (
    WechatVoiceError,
    decode_wechat_voice,
    validate_silk,
)

SILK = b"\x02#!SILK_V3" + struct.pack("<h", 3) + b"abc"


@pytest.mark.parametrize(
    "data",
    [b"", b"not audio", b"#!SILK_V3", SILK[:-1], SILK + b"\x01", SILK + b"\xfe\xff"],
)
def test_incomplete_or_invalid_frames_never_reach_decoder(data, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: pytest.fail("invalid data reached native decoder"),
    )
    with pytest.raises(WechatVoiceError):
        decode_wechat_voice(data)


def test_valid_frames_and_pcm_output(monkeypatch):
    validate_silk(SILK)
    validate_silk(SILK + b"\xff\xff")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x00\x00" * 2400)

    def decode(args, **kwargs):
        assert "-I" in args and kwargs["input"] == SILK and kwargs["timeout"] == 15
        return SimpleNamespace(stdout=buffer.getvalue())

    monkeypatch.setattr(subprocess, "run", decode)
    assert decode_wechat_voice(SILK) == buffer.getvalue()


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.TimeoutExpired("decoder", 15),
        subprocess.CalledProcessError(1, "decoder"),
    ],
)
def test_decoder_failures_are_bounded_and_actionable(monkeypatch, failure):
    def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(WechatVoiceError, match="重发"):
        decode_wechat_voice(SILK)
