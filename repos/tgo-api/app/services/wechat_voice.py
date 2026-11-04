"""Convert bounded WeChat SILK outside the API worker."""

import io
import struct
import subprocess
import sys
import wave

MAX_SILK_BYTES = 2 * 1024 * 1024
MAX_WAV_BYTES = 12 * 1024 * 1024

DECODER = """
import sys
if sys.platform != 'win32':
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024**2, 256 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
import pysilk
data = sys.stdin.buffer.read(2 * 1024**2 + 1)
sys.stdout.buffer.write(pysilk.decode(data, to_wav=True))
"""


class WechatVoiceError(ValueError):
    pass


def validate_silk(data: bytes) -> None:
    if not 0 < len(data) <= MAX_SILK_BYTES:
        raise WechatVoiceError("微信语音文件大小不适合识别")
    header = b"\x02#!SILK_V3" if data.startswith(b"\x02") else b"#!SILK_V3"
    if not data.startswith(header):
        raise WechatVoiceError("不支持的微信语音格式")
    position, frames = len(header), 0
    while position < len(data):
        if position + 2 > len(data):
            raise WechatVoiceError("微信语音尚未完整下载")
        size = struct.unpack_from("<h", data, position)[0]
        position += 2
        if size == -1 and position == len(data):
            break
        if not 0 < size <= 4096 or position + size > len(data):
            raise WechatVoiceError("微信语音帧格式无效")
        position += size
        frames += 1
        if frames > 6000:
            raise WechatVoiceError("微信语音过长，请分段发送")
    if frames == 0:
        raise WechatVoiceError("微信语音没有可识别的音频")


def decode_wechat_voice(data: bytes) -> bytes:
    validate_silk(data)
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", DECODER],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW
            if sys.platform == "win32"
            else 0,
        )
        audio = result.stdout
        if not 44 < len(audio) <= MAX_WAV_BYTES:
            raise ValueError("invalid audio length")
        with wave.open(io.BytesIO(audio), "rb") as decoded:
            if (
                decoded.getnchannels() != 1
                or decoded.getsampwidth() != 2
                or decoded.getframerate()
                not in (8000, 12000, 16000, 24000, 48000)
                or not 0 < decoded.getnframes() / decoded.getframerate() <= 120
                or len(decoded.readframes(decoded.getnframes()))
                != decoded.getnframes() * 2
            ):
                raise ValueError("invalid decoded audio")
        return audio
    except (
        OSError,
        ValueError,
        EOFError,
        wave.Error,
        subprocess.SubprocessError,
    ) as error:
        raise WechatVoiceError("微信语音转换失败，请重发语音或补充文字") from error
