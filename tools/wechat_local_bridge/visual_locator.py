"""Locate the approved conversation and composer from fresh pixels."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from .rpa import RpaNotReady


@dataclass(frozen=True)
class VisualTarget:
    header: tuple[int, int, int, int]
    toolbar: tuple[int, int, int, int]
    input_box: tuple[int, int, int, int]
    input_point: tuple[int, int]
    confidence: float


class VisualLocator:
    def __init__(
        self,
        reference: Image.Image,
        title_box: tuple[int, int, int, int],
        toolbar_box: tuple[int, int, int, int],
    ):
        self.title = np.asarray(reference.crop(title_box).convert("L"))
        self.toolbar = np.asarray(reference.crop(toolbar_box).convert("L"))
        if min(float(self.title.std()), float(self.toolbar.std())) < 8:
            raise ValueError("校准锚点必须包含有辨识度的标题文字和工具栏")

    @staticmethod
    def _match(
        image: np.ndarray,
        template: np.ndarray,
        region: tuple[int, int, int, int],
        label: str,
    ) -> tuple[tuple[int, int, int, int], float, float]:
        left, top, right, bottom = region
        search = image[top:bottom, left:right]
        candidates: list[tuple[float, int, int, int, int, float]] = []
        for scale in (0.8, 0.9, 1.0, 1.1, 1.25, 1.5):
            width, height = (
                round(template.shape[1] * scale),
                round(template.shape[0] * scale),
            )
            if width > search.shape[1] or height > search.shape[0]:
                continue
            sample = cv2.resize(template, (width, height))
            scores = cv2.matchTemplate(search, sample, cv2.TM_CCOEFF_NORMED)
            for _ in range(2):
                _, score, _, point = cv2.minMaxLoc(scores)
                x, y = point
                candidates.append(
                    (float(score), x + left, y + top, width, height, scale)
                )
                scores[
                    max(0, y - height // 2) : y + height // 2 + 1,
                    max(0, x - width // 2) : x + width // 2 + 1,
                ] = -1
        if not candidates:
            raise RpaNotReady(f"{label}超出可见范围")
        candidates.sort(reverse=True)
        score, x, y, width, height, scale = candidates[0]
        if score < 0.93:
            raise RpaNotReady(f"{label}未匹配，停止输入")
        for other_score, ox, oy, _, _, _ in candidates[1:]:
            if (
                abs(ox - x) > width // 2 or abs(oy - y) > height // 2
            ) and other_score > score - 0.035:
                raise RpaNotReady(f"{label}不唯一，停止输入")
        return (x, y, width, height), score, scale

    def locate(self, image: Image.Image) -> VisualTarget:
        pixels = np.asarray(image.convert("L"))
        height, width = pixels.shape
        header, title_score, scale = self._match(
            pixels,
            self.title,
            (width // 5, 20, width - 15, height // 4),
            "文件传输助手标题",
        )
        toolbar, toolbar_score, toolbar_scale = self._match(
            pixels,
            self.toolbar,
            (width // 5, height * 3 // 5, width - 15, height - 10),
            "输入工具栏",
        )
        if (
            abs(scale - toolbar_scale) > 0.11
            or abs(header[0] - toolbar[0]) > 80 * scale
        ):
            raise RpaNotReady("标题与输入工具栏布局不一致")
        left, right = max(0, toolbar[0] - round(16 * scale)), width - round(25 * scale)
        top = None
        for y in range(
            toolbar[1] - round(12 * scale),
            max(height // 2, toolbar[1] - round(350 * scale)),
            -1,
        ):
            line = pixels[y, left + round(24 * scale) : right - round(20 * scale)]
            if len(line) > 200 and float(np.mean((line >= 205) & (line <= 235))) > 0.95:
                top = y + round(8 * scale)
                break
        bottom = toolbar[1] - round(9 * scale)
        if top is None or bottom - top < 15 * scale:
            raise RpaNotReady("未确认输入框边界，停止输入")
        point = (left + round(35 * scale), top + round(12 * scale))
        return VisualTarget(
            header,
            toolbar,
            (left, top, right, bottom),
            point,
            min(title_score, toolbar_score),
        )

    @staticmethod
    def is_blank(image: Image.Image, target: VisualTarget) -> bool:
        pixels = np.asarray(image.crop(target.input_box).convert("L"))
        # Current light theme has grey placeholder text and a green caret.
        # Only treat a failed copy as empty when no dark draft glyphs are visible.
        return int(np.count_nonzero(pixels < 100)) <= 10
