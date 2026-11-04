import unittest

import numpy as np
from PIL import Image, ImageDraw

from tools.wechat_local_bridge.rpa import RpaNotReady
from tools.wechat_local_bridge.visual_locator import VisualLocator


class VisualLocatorTests(unittest.TestCase):
    def setUp(self):
        random = np.random.default_rng(7)
        self.title = Image.fromarray(
            random.integers(20, 230, (25, 110), dtype=np.uint8)
        )
        self.toolbar = Image.fromarray(
            random.integers(20, 230, (25, 180), dtype=np.uint8)
        )
        self.reference = self.scene(1000, 800, 350, 80)
        self.locator = VisualLocator(
            self.reference, (350, 80, 460, 105), (350, 740, 530, 765)
        )

    def scene(self, width, height, left, title_y):
        image = Image.new("L", (width, height), 250)
        image.paste(self.title, (left, title_y))
        image.paste(self.toolbar, (left, height - 60))
        ImageDraw.Draw(image).line(
            (left + 4, height - 120, width - 35, height - 120), fill=220
        )
        return image.convert("RGB")

    def test_editor_is_relocated_after_window_resize_and_sidebar_change(self):
        target = self.locator.locate(self.scene(1250, 950, 460, 60))
        self.assertEqual(target.header[:2], (460, 60))
        self.assertEqual(target.toolbar[:2], (460, 890))
        self.assertGreater(target.input_point[1], 830)
        self.assertLess(target.input_point[1], 890)
        self.assertGreater(target.input_point[0], 460)

    def test_missing_conversation_title_fails_closed(self):
        image = self.scene(1000, 800, 350, 80)
        ImageDraw.Draw(image).rectangle((350, 80, 460, 105), fill="white")
        with self.assertRaisesRegex(RpaNotReady, "标题"):
            self.locator.locate(image)

    def test_changed_middle_toolbar_icons_use_two_independent_aligned_markers(self):
        locator = VisualLocator(self.reference, (350, 80, 460, 105), (350, 740, 530, 765),
                                toolbar_markers=((350, 740, 374, 765), (506, 740, 530, 765)))
        image = self.scene(1250, 950, 460, 60)
        image.paste("white", (484, 890, 616, 915))
        target = locator.locate(image)
        self.assertEqual(target.toolbar[:2], (460, 890))
        self.assertGreater(target.input_point[0], 460)

    def test_toolbar_fallback_rejects_a_missing_or_misaligned_second_marker(self):
        locator = VisualLocator(self.reference, (350, 80, 460, 105), (350, 740, 530, 765),
                                toolbar_markers=((350, 740, 374, 765), (506, 740, 530, 765)))
        image = self.scene(1250, 950, 460, 60)
        image.paste("white", (484, 890, 640, 915))
        image.paste(self.toolbar.crop((156, 0, 180, 25)), (616, 850))
        with self.assertRaises(RpaNotReady):
            locator.locate(image)

    def test_duplicate_header_or_missing_editor_border_is_rejected(self):
        image = self.scene(1000, 800, 350, 80)
        image.paste(self.title, (650, 80))
        with self.assertRaisesRegex(RpaNotReady, "不唯一"):
            self.locator.locate(image)
        image = self.scene(1000, 800, 350, 80)
        ImageDraw.Draw(image).line((354, 680, 965, 680), fill="white")
        with self.assertRaisesRegex(RpaNotReady, "边界"):
            self.locator.locate(image)

    def test_grey_placeholder_is_empty_but_dark_draft_is_preserved(self):
        image = self.scene(1000, 800, 350, 80)
        target = self.locator.locate(image)
        ImageDraw.Draw(image).text(
            target.input_point, "type a message", fill=(155, 155, 165)
        )
        self.assertTrue(self.locator.is_blank(image, target))
        ImageDraw.Draw(image).text(target.input_point, "user draft", fill=(40, 40, 40))
        self.assertFalse(self.locator.is_blank(image, target))
