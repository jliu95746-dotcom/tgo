"""Dynamic visual RPA with ordinary window recovery and filehelper targeting."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

from .filehelper_navigation import FilehelperNavigation
from .rpa import RpaNotReady
from .visual_locator import VisualLocator, VisualTarget
from .window_control import WindowControl


class NativeRpa:
    def __init__(
        self, binding_file: Path, runtime: Path, *,
        chat_id: str = "filehelper", authorize: Callable[[], None] | None = None,
        scope_lock: AbstractContextManager | None = None,
    ):
        from PIL import Image

        self.binding = json.loads(binding_file.read_text(encoding="utf-8"))
        if self.binding.get("chat_id") != chat_id or (chat_id != "filehelper" and authorize is None):
            raise ValueError("微信窗口校准与授权会话不一致")
        self.reference = Image.open(self.binding["reference_image"]).convert("RGB")
        if (
            "title_anchor_box" not in self.binding
            or "toolbar_anchor_box" not in self.binding
        ):
            raise ValueError("需要微信会话标题与输入工具栏的视觉锚点校准")
        self.locator = VisualLocator(
            self.reference,
            tuple(self.binding["title_anchor_box"]),
            tuple(self.binding["toolbar_anchor_box"]),
        )
        self._configure_desktop(
            WindowControl(int(self.binding["window_id"]), int(self.binding["pid"])),
            runtime, chat_id, authorize or (lambda: None), scope_lock,
        )
        search_reference = self.binding.get("search_reference_image")
        self.navigation = (
            FilehelperNavigation(self.reference, Path(search_reference))
            if search_reference and chat_id == "filehelper"
            else None
        )
        self.send_template = None
        if "send_reference_image" in self.binding and "send_anchor_box" in self.binding:
            import numpy as np

            with Image.open(self.binding["send_reference_image"]) as reference:
                self.send_template = np.asarray(
                    reference.crop(tuple(self.binding["send_anchor_box"])).convert("L")
                )

    def _configure_desktop(
        self, window: WindowControl, runtime: Path, chat_id: str,
        authorize: Callable[[], None], scope_lock: AbstractContextManager | None,
    ) -> None:
        window.use_physical_coordinates()
        import pyautogui
        import pyperclip

        self.gui, self.clipboard = pyautogui, pyperclip
        self.window, self.runtime = window, runtime
        self.chat_id, self.authorize, self.scope_lock = chat_id, authorize, scope_lock
        self.bounds = (0, 0, 0, 0)
        self.target: VisualTarget | None = None

    def _visible_bounds(self) -> tuple[int, int, int, int]:
        bounds = self.window.bounds()
        screen = self.gui.size()
        if (
            bounds[0] < 0
            or bounds[1] < 0
            or bounds[0] + bounds[2] > screen.width
            or bounds[1] + bounds[3] > screen.height
        ):
            raise RpaNotReady("微信未完整显示在当前主屏，停止输入")
        return bounds

    def check_target(self) -> None:
        self.authorize()
        bounds = self._visible_bounds()
        image = self.gui.screenshot(region=bounds).convert("RGB")
        target = self.locator.locate(image)
        x, y = target.input_point
        self.window.require_point(bounds[0] + x, bounds[1] + y)
        if self.window.bounds() != bounds:
            raise RpaNotReady("定位期间窗口移动，等待稳定后重试")
        self.bounds, self.target = bounds, target

    def ensure_conversation(self) -> None:
        self.authorize()
        self.window.recover()
        try:
            self.check_target()
            return
        except RpaNotReady:
            if self.navigation is None:
                raise
        bounds = self._visible_bounds()

        def frame():
            if self.window.bounds() != bounds:
                raise RpaNotReady("搜索期间微信窗口位置变化")
            left, top, width, height = bounds
            return self.gui.screenshot(
                region=(left, top, min(width, 465), min(height, 400))
            )

        def guard(point, *, focus=False, search_result=False):
            if self.window.bounds() != bounds:
                raise RpaNotReady("搜索期间微信窗口位置变化")
            self.window.require_point(
                bounds[0] + point[0],
                bounds[1] + point[1],
                focus=focus,
                search_result=search_result,
            )

        original = self.get_clipboard()
        try:
            point = self.navigation.search_point(frame())
            guard(point)
            self.gui.click(bounds[0] + point[0], bounds[1] + point[1])
            time.sleep(0.25)
            self.navigation.focused_search(frame())
            guard(point, focus=True)
            self.gui.hotkey("ctrl", "a")
            self.navigation.focused_search(frame())
            guard(point, focus=True)
            self.clipboard.copy("文件传输助手")
            self.gui.hotkey("ctrl", "v")
            deadline = time.monotonic() + 3
            while True:
                try:
                    selected = self.navigation.result_point(frame())
                    break
                except RpaNotReady:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.2)
            guard(selected, search_result=True)
            self.gui.click(bounds[0] + selected[0], bounds[1] + selected[1])
            deadline = time.monotonic() + 4
            while True:
                try:
                    self.check_target()
                    break
                except RpaNotReady:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.2)
        finally:
            self.restore_clipboard(original)

    def _point(self) -> tuple[int, int]:
        if self.target is None:
            raise RpaNotReady("尚未确认输入框位置")
        x, y = self.target.input_point
        return self.bounds[0] + x, self.bounds[1] + y

    def _guard_input(self) -> None:
        self.authorize()
        if self._visible_bounds() != self.bounds:
            raise RpaNotReady("输入期间窗口位置变化")
        self.window.require_point(*self._point(), focus=True)

    def _focus_input(self) -> None:
        self.check_target()
        self.gui.click(*self._point())
        time.sleep(0.1)
        self._guard_input()

    def ensure_ready(self) -> None:
        self.ensure_conversation()
        clipboard = self.get_clipboard()
        try:
            existing = self.read_input()
        finally:
            self.restore_clipboard(clipboard)
        if existing:
            raise RpaNotReady("输入框已有草稿，保留原内容")

    def get_clipboard(self) -> str:
        return self.clipboard.paste()

    def restore_clipboard(self, text: str) -> None:
        self.clipboard.copy(text)

    def paste(self, text: str) -> None:
        with self.scope_lock or nullcontext():
            self._focus_input()
            self.clipboard.copy(text)
            self._guard_input()
            self.gui.hotkey("ctrl", "v")
        time.sleep(0.3)

    def read_input(self) -> str:
        self._focus_input()
        sentinel = "RPA-READBACK-" + uuid.uuid4().hex
        self.clipboard.copy(sentinel)
        self._guard_input()
        self.gui.hotkey("ctrl", "a")
        self._guard_input()
        self.gui.hotkey("ctrl", "c")
        time.sleep(0.15)
        self._guard_input()
        result = self.clipboard.paste()
        self.gui.press("right")
        if result == sentinel:
            image = self.gui.screenshot(region=self.bounds)
            if self.target is not None and self.locator.is_blank(image, self.target):
                return ""
            raise RuntimeError("输入框未能复制回读，保留内容并停止")
        return result

    def save_proof(self) -> None:
        self.check_target()
        if self.chat_id == "filehelper":
            self.gui.screenshot(region=self.bounds).save(
                self.runtime / "wechat-rpa-verified.png"
            )

    def clear_owned_draft(self, expected: str) -> None:
        with self.scope_lock or nullcontext():
            self.ensure_conversation()
            original = self.get_clipboard()
            try:
                if self.read_input() != expected:
                    raise RpaNotReady("草稿已被人工修改，保留现有内容")
                self._guard_input()
                self.gui.hotkey("ctrl", "a")
                self._guard_input()
                self.gui.press("backspace")
                if self.read_input():
                    raise RpaNotReady("旧草稿未能确认清除，停止后续填写")
            finally:
                self.restore_clipboard(original)

    def send_point(self, expected: str) -> tuple[int, int]:
        """Verify the exact draft and identify the enabled Send button."""
        import numpy as np

        if self.send_template is None:
            raise RpaNotReady("发送按钮尚未完成视觉校准")
        self.ensure_conversation()
        original = self.get_clipboard()
        try:
            if self.read_input() != expected:
                raise RpaNotReady("草稿与客服回复不一致，保留现有内容")
        finally:
            self.restore_clipboard(original)
        self.check_target()
        screenshot = self.gui.screenshot(region=self.bounds).convert("RGB")
        width, height = screenshot.size
        box, _, _ = self.locator._match(
            np.asarray(screenshot.convert("L")),
            self.send_template,
            (width * 3 // 4, height * 3 // 4, width - 8, height - 8),
            "已启用的发送按钮",
        )
        left, top, button_width, button_height = box
        button = np.asarray(
            screenshot.crop((left, top, left + button_width, top + button_height))
        )
        green = (
            (button[:, :, 0] < 60) & (button[:, :, 1] > 130) & (button[:, :, 2] < 155)
        )
        if float(green.mean()) < 0.45:
            raise RpaNotReady("发送按钮未启用，等待核对")
        point = (
            self.bounds[0] + left + button_width // 2,
            self.bounds[1] + top + button_height // 2,
        )
        self.window.require_point(*point, focus=True)
        return point

    def click_send(self, point: tuple[int, int], expected: str) -> None:
        with self.scope_lock or nullcontext():
            if self.send_point(expected) != point:
                raise RpaNotReady("发送前按钮位置变化，停止点击")
            self._guard_input()
            self.window.require_point(*point, focus=True)
            self.gui.click(*point)
