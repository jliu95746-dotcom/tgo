import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from tools.wechat_local_bridge import calibration


class CalibrationTests(unittest.TestCase):
    def test_calibration_checks_draft_and_saves_binding_without_clicking_send(self):
        image = Image.new("RGB", (720, 600))
        gui = SimpleNamespace(
            size=lambda: SimpleNamespace(width=1920, height=1080),
            screenshot=Mock(return_value=image),
        )
        boxes = [(240, 20, 340, 55), (240, 470, 300, 500), (640, 545, 700, 580)]
        with (
            tempfile.TemporaryDirectory() as path,
            patch.dict(sys.modules, {"pyautogui": gui}),
            patch.object(calibration, "WindowControl") as window,
            patch.object(calibration, "_select_anchors", return_value=boxes),
            patch.object(calibration, "VisualLocator"),
            patch.object(calibration, "NativeRpa") as rpa,
        ):
            runtime = Path(path)
            window.return_value.bounds.return_value = (100, 100, 720, 600)
            calibration.calibrate(runtime, 123, 456)
            binding = json.loads((runtime/"rpa-binding.private.json").read_text())
            self.assertEqual(binding["chat_id"], "filehelper")
            self.assertEqual(binding["window_id"], 456)
            self.assertTrue(Path(binding["reference_image"]).is_file())
            rpa.return_value.send_point.assert_called_once_with(calibration.CALIBRATION_DRAFT)
            rpa.return_value.click_send.assert_not_called()
