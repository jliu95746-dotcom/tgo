"""Locate an Annex-B HEVC parameter-set chain inside a WXGF container."""

import re

START_CODE = re.compile(b"\x00\x00\x00?\x01")


def hevc_offset(content: bytes) -> int:
    if not content.startswith(b"wxgf"):
        raise ValueError("微信图片容器无效")
    # Container metadata may contain start-code lookalikes. A real image starts
    # with valid VPS/SPS/PPS headers followed by a coded picture, in that order.
    markers = list(START_CODE.finditer(content, 4, min(len(content), 262144)))
    for index, marker in enumerate(markers):
        chain = markers[index : index + 4]
        if len(chain) < 4:
            break
        types = []
        for unit in chain:
            position = unit.end()
            if position + 2 >= len(content):
                break
            first, second = content[position : position + 2]
            if first & 128 or not second & 7:
                break
            types.append((first >> 1) & 63)
        if len(types) == 4 and types[:3] == [32, 33, 34] and types[3] <= 31:
            return marker.start()
    raise ValueError("微信高清图片码流尚未完整下载")
