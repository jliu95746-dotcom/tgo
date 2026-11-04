"""Normalize WeChat 4.x image containers using an isolated, bounded decoder."""

import subprocess
import sys
from app.services.wechat_hevc import hevc_offset

MAX_IMAGE_BYTES = 10 * 1024 * 1024
DECODER = """
import os, sys
import imageio_ffmpeg
if sys.platform != 'win32':
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
binary = imageio_ffmpeg.get_ffmpeg_exe()
scale = "scale='min(4096,iw)':'min(4096,ih)'"
scale += ':force_original_aspect_ratio=decrease'
os.execv(binary, [binary, '-hide_banner', '-loglevel', 'error',
    '-max_alloc', '67108864',
    '-threads', '1', '-f', 'hevc', '-i', 'pipe:0', '-frames:v', '1',
    '-vf', scale, '-q:v', '2',
    '-threads', '1', '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1'])
"""


class WechatImageError(ValueError):
    pass


def decode_wechat_image(content: bytes) -> bytes:
    if (
        not 0 < len(content) <= MAX_IMAGE_BYTES
        or not content.startswith(b"wxgf")
    ):
        raise WechatImageError("微信图片文件尚未完整下载")
    try:
        start = hevc_offset(content)
        result = subprocess.run(
            [sys.executable, "-I", "-c", DECODER],
            input=content[start:],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW
            if sys.platform == "win32"
            else 0,
        )
        image = result.stdout
        if (
            not 4 < len(image) <= MAX_IMAGE_BYTES
            or not image.startswith(b"\xff\xd8\xff")
            or not image.endswith(b"\xff\xd9")
        ):
            raise ValueError("invalid image output")
        return image
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise WechatImageError("微信图片转换失败，请重发图片或补充文字") from error
