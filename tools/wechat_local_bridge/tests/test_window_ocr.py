import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image, ImageDraw

from tools.wechat_local_bridge.window_ocr import TextLine, read_lines, title_text_region


class TitleRegionTests(unittest.TestCase):
    def test_high_resolution_ocr_returns_original_image_coordinates(self):
        def recognize(path):
            with Image.open(path) as image:
                self.assertEqual(image.size, (400, 160))
            return [TextLine("标题", 120, 80)]

        with TemporaryDirectory() as folder, patch("tools.wechat_local_bridge.window_ocr._run", side_effect=recognize):
            rows = read_lines(Image.new("RGB", (100, 40), "white"), Path(folder), scale=4)
        self.assertEqual(rows, [TextLine("标题", 30, 20)])

    def test_title_is_kept_while_separated_action_buttons_are_excluded(self):
        header = Image.new("RGB", (573, 48), "white")
        draw = ImageDraw.Draw(header)
        for left in (18, 33, 48, 63, 78, 93, 108):
            draw.rectangle((left, 19, left + 10, 31), fill="black")
        draw.ellipse((499, 18, 519, 35), outline="black")
        region = title_text_region(header)
        self.assertGreaterEqual(region.width, 119)
        self.assertLess(region.width, 499)
        self.assertEqual(region.height, header.height)
        self.assertEqual(region.crop((0, 0, 119, 48)).tobytes(), header.crop((0, 0, 119, 48)).tobytes())

    def test_enterprise_second_line_is_kept_with_the_title(self):
        header = Image.new("RGB", (600, 60), "white")
        draw = ImageDraw.Draw(header)
        draw.rectangle((18, 10, 120, 25), fill="black")
        draw.rectangle((18, 35, 240, 49), fill="black")
        draw.ellipse((530, 20, 549, 40), outline="black")
        region = title_text_region(header)
        self.assertGreater(region.width, 240)
        self.assertLess(region.width, 530)
        self.assertEqual(region.height, 60)
