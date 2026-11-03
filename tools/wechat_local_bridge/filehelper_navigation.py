"""Calibrated navigation through WeChat's built-in File Transfer Assistant result."""

from pathlib import Path

import numpy as np
from PIL import Image

from .rpa import RpaNotReady
from .visual_locator import VisualLocator


class SearchNavigation:
    def __init__(self, idle: Image.Image, active: Image.Image):
        self.search_icon = np.asarray(idle.convert("L"))
        self.active_search = np.asarray(active.convert("L"))

    def search_point(self, image: Image.Image) -> tuple[int, int]:
        try:
            x, y, scale = self.focused_search(image)
            return x + round(68 * scale), y + round(17 * scale)
        except RpaNotReady:
            pixels = np.asarray(image.convert("L"))
            box, _, scale = VisualLocator._match(
                pixels, self.search_icon, (45, 25, 260, 125), "微信全局搜索框"
            )
            x, y, _, height = box
            return x + round(52 * scale), y + height // 2

    def focused_search(self, image: Image.Image) -> tuple[int, int, float]:
        pixels = np.asarray(image.convert("L"))
        box, _, scale = VisualLocator._match(
            pixels, self.active_search, (45, 25, 260, 125), "已激活的微信搜索框"
        )
        x, y, width, height = box
        colors = np.asarray(image.crop((x, y, x + width, y + height)).convert("RGB"))
        green = (
            (colors[:, :, 0] < 110) & (colors[:, :, 1] > 135) & (colors[:, :, 2] < 170)
        )
        if int(green.sum()) < 12:
            raise RpaNotReady("微信全局搜索框尚未获得焦点")
        return x, y, scale


class FilehelperNavigation(SearchNavigation):
    def __init__(self, normal: Image.Image, search_result: Path):
        with Image.open(search_result) as result:
            super().__init__(normal.crop((104, 57, 127, 85)), result.crop((17, 14, 52, 49)))
            self.category = np.asarray(result.crop((31, 62, 69, 84)).convert("L"))
            self.icon = np.asarray(result.crop((33, 108, 80, 155)).convert("L"))
            self.label = np.asarray(result.crop((87, 119, 197, 146)).convert("L"))

    def result_point(self, image: Image.Image) -> tuple[int, int]:
        x, y, scale = self.focused_search(image)
        pixels = np.asarray(image.convert("L"))
        left = x + round(14 * scale)
        margin = round(8 * scale)
        category, _, found_scale = VisualLocator._match(
            pixels,
            self.category,
            (
                left - margin,
                y + round(36 * scale),
                left + round(self.category.shape[1] * scale) + margin,
                min(image.height, y + round(245 * scale)),
            ),
            "功能分类",
        )
        if abs(found_scale - scale) > 0.11:
            raise RpaNotReady("搜索结果分类布局不一致")
        expected = [
            (self.icon, 2, 46, "系统文件传输图标"),
            (self.label, 56, 57, "文件传输助手搜索结果"),
        ]
        matches = []
        for template, dx, dy, label in expected:
            left, top = category[0] + round(dx * scale), category[1] + round(dy * scale)
            box, _, found_scale = VisualLocator._match(
                pixels,
                template,
                (
                    left - margin,
                    top - margin,
                    left + round(template.shape[1] * scale) + margin,
                    top + round(template.shape[0] * scale) + margin,
                ),
                label,
            )
            if abs(found_scale - scale) > 0.11:
                raise RpaNotReady("搜索结果布局不一致")
            matches.append(box)
        left, top, width, height = matches[-1]
        return left + width // 2, top + height // 2
