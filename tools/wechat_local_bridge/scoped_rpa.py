"""Reuse window controls across authorized conversations with local draft proof."""

from __future__ import annotations

import re
import sqlite3
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from hashlib import sha256
from pathlib import Path

import numpy as np
from PIL import Image

from .filehelper_navigation import SearchNavigation
from .rpa import RpaNotReady
from .rpa_windows import NativeRpa
from .visual_locator import VisualLocator
from .window_control import WindowControl
from .window_ocr import TextLine, check_ocr, normalized_title, read_lines


def matches_header(lines: list[TextLine], title: str, *, group: bool) -> bool:
    if len(lines) != 1:
        return False
    actual, expected = normalized_title(lines[0].text), normalized_title(title)
    if group and actual != expected:
        actual = re.sub(r"\([0-9]{1,6}\)$", "", actual)
    return bool(expected) and actual == expected


def result_line(lines: list[TextLine], title: str, sections: list[TextLine] | None = None) -> TextLine:
    headings = {"联系人", "群聊", "最近使用", "最近聊天", "常用联系人", "最常使用", "好友", "聊天", "最佳匹配"}
    ignored = {"聊天记录", "网络结果", "搜索网络结果", "网页结果", "公众号", "搜一搜", "功能", "小程序"}
    regions = [line for line in (lines if sections is None else sections)
               if normalized_title(line.text) in headings | ignored]
    matches = []
    for row in lines:
        if normalized_title(row.text) != normalized_title(title):
            continue
        previous = [line for line in regions if line.y < row.y]
        if previous and normalized_title(max(previous, key=lambda line: line.y).text) in headings:
            matches.append(row)
        elif not previous and any(
            line.y > row.y and normalized_title(line.text) in {"网络结果", "搜索网络结果", "网页结果"}
            for line in regions
        ):
            # Some WeChat builds omit the local heading above the network section.
            # Selection still has to pass the independent header and draft checks.
            matches.append(row)
    if len(matches) != 1:
        raise RpaNotReady("未找到唯一的本地聊天结果，回复待人工处理")
    return matches[0]


class ScopedRpa(NativeRpa):
    def __init__(
        self, window: WindowControl, runtime: Path, chat_id: str, title: str,
        authorize: Callable[[], None], scope_lock: AbstractContextManager,
        draft_matches: Callable[[str], bool],
    ):
        self._configure_desktop(window, runtime, chat_id, authorize, scope_lock)
        self.title, self.draft_matches = title, draft_matches
        self._header_box: tuple[int, int, int, int] | None = None
        self._header: bytes | None = None
        self._verified_header: bytes | None = None
        with Image.open(Path(__file__).with_name("wechat-controls.png")) as assets:
            self.locator = VisualLocator(assets, None, (0, 0, 114, 29))
            self.navigation = SearchNavigation(assets.crop((70, 36, 93, 64)), assets.crop((100, 36, 135, 71)))
            self.send_template = np.asarray(assets.crop((0, 36, 63, 72)).convert("L"))
            self.contacts_icon = np.asarray(assets.crop((140, 36, 168, 68)).convert("L"))
            self.chat_icon = np.asarray(assets.crop((178, 36, 195, 63)).convert("L"))

    def _visible_bounds(self) -> tuple[int, int, int, int]:
        screen = self.gui.size()
        return self.window.display_bounds(screen.width, screen.height)

    def check_target(self) -> None:
        self.authorize()
        bounds = self._visible_bounds()
        frame = self.gui.screenshot(region=bounds).convert("RGB")
        target, scale = self.locator.locate_composer(frame)
        left = max(0, target.toolbar[0] - round(24 * scale))
        box = (left, round(43 * scale), frame.width - round(95 * scale), round(91 * scale))
        if box[2] - left < 80 or box[3] > frame.height:
            raise RpaNotReady("微信会话标题显示不完整")
        self._confirm_header(frame.crop(box))
        self.window.require_point(bounds[0] + target.input_point[0], bounds[1] + target.input_point[1])
        if self._visible_bounds() != bounds:
            raise RpaNotReady("定位期间窗口移动")
        self.bounds, self.target, self._header_box = bounds, target, box

    def _confirm_header(self, header: Image.Image) -> None:
        digest = sha256(header.tobytes()).digest()
        if digest != self._header and not matches_header(
            read_lines(header, self.runtime), self.title, group=self.chat_id.endswith("@chatroom")
        ):
            raise RpaNotReady("当前微信会话标题无法核对")
        if self._verified_header is not None and self._verified_header == self._header:
            self._verified_header = digest
        self._header = digest

    def _guard_input(self) -> None:
        super()._guard_input()
        if self._header_box is None:
            raise RpaNotReady("尚未核对当前微信会话")
        frame = self.gui.screenshot(region=self.bounds).convert("RGB")
        self._confirm_header(frame.crop(self._header_box))
        super()._guard_input()
        if self._verified_header is not None and self._header != self._verified_header:
            raise RpaNotReady("已核对的微信会话发生变化")

    def ensure_conversation(self) -> None:
        self.authorize()
        self.window.recover()
        try:
            self.check_target()
        except RpaNotReady:
            try:
                self._sidebar()
                self._wait_target()
                return
            except RpaNotReady:
                pass
            self._search()
            self._wait_target()

    def _wait_target(self) -> None:
        deadline = time.monotonic() + 2
        while True:
            try:
                self.check_target()
                return
            except RpaNotReady:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)

    def _sidebar(self) -> None:
        self._chat_tab()
        bounds = self._visible_bounds()
        frame = self.gui.screenshot(region=bounds)
        search = frame.crop((0, 0, min(frame.width, 570), min(frame.height, 395)))
        try:
            self.navigation.focused_search(search)
        except RpaNotReady:
            pass
        else:
            raise RpaNotReady("微信搜索框仍处于激活状态")
        try:
            target, scale = self.locator.locate_composer(frame)
            right = target.toolbar[0] - round(28 * scale)
        except RpaNotReady:
            point = self.navigation.search_point(search)
            # The chat list is still available when the main pane has no open chat.
            scale = 1.0
            right = min(frame.width * 2 // 5, point[0] + 190)
        box = (round(108 * scale), round(104 * scale),
               right, frame.height - 16)
        if box[2] - box[0] < 100:
            raise RpaNotReady("微信会话列表不可用")
        sidebar = frame.crop(box)
        rows = [row for row in read_lines(sidebar, self.runtime)
                if normalized_title(row.text) == normalized_title(self.title)]
        if len(rows) != 1:
            raise RpaNotReady("侧栏未找到唯一会话")
        self.authorize()
        if self._visible_bounds() != bounds or self.gui.screenshot(region=bounds).crop(box).tobytes() != sidebar.tobytes():
            raise RpaNotReady("微信侧栏发生变化")
        point = bounds[0] + box[0] + rows[0].x, bounds[1] + box[1] + rows[0].y
        self.window.require_point(*point)
        self.gui.click(*point)
        time.sleep(0.25)

    def _search(self) -> None:
        bounds = self._visible_bounds()
        left, top, width, height = bounds

        def frame() -> Image.Image:
            if self._visible_bounds() != bounds:
                raise RpaNotReady("搜索期间微信窗口位置变化")
            return self.gui.screenshot(region=(left, top, min(width, 570), min(height, 395)))

        def guard(point: tuple[int, int], *, focus: bool = False, popup: bool = False) -> None:
            self.authorize()
            if self._visible_bounds() != bounds:
                raise RpaNotReady("搜索期间微信窗口位置变化")
            self.window.require_point(left + point[0], top + point[1], focus=focus, search_result=popup)

        original = self.get_clipboard()
        point = None
        query_confirmed = False
        try:
            point = self.navigation.search_point(frame())
            guard(point)
            self.gui.click(left + point[0], top + point[1])
            time.sleep(0.22)
            self.navigation.focused_search(frame())
            guard(point, focus=True)
            self.gui.hotkey("ctrl", "a")
            self.navigation.focused_search(frame())
            guard(point, focus=True)
            self.clipboard.copy(self.title)
            self.gui.hotkey("ctrl", "v")
            time.sleep(0.45)
            self.navigation.focused_search(frame())
            guard(point, focus=True)
            self.clipboard.copy("SEARCH-READBACK-" + uuid.uuid4().hex)
            self.gui.hotkey("ctrl", "a")
            guard(point, focus=True)
            self.gui.hotkey("ctrl", "c")
            time.sleep(0.12)
            guard(point, focus=True)
            if self.clipboard.paste() != self.title:
                raise RpaNotReady("微信搜索词未回读确认")
            query_confirmed = True
            self.gui.press("right")
            search = frame()
            x, y, scale = self.navigation.focused_search(search)
            crop = (x + round(40 * scale), y + round(36 * scale),
                    min(search.width, x + round(382 * scale)), search.height)
            heading_box = (x + round(5 * scale), crop[1], x + round(152 * scale), crop[3])
            sections = read_lines(search.crop(heading_box), self.runtime)
            row = result_line(read_lines(search.crop(crop), self.runtime), self.title, sections)
            result = (crop[0] + row.x, crop[1] + row.y)
            latest = frame().crop(crop)
            if latest.tobytes() != search.crop(crop).tobytes():
                raise RpaNotReady("微信搜索结果发生变化，等待人工核对")
            guard(result, popup=True)
            self.gui.click(left + result[0], top + result[1])
            time.sleep(0.3)
        except RpaNotReady:
            if query_confirmed and point is not None:
                try:
                    self.navigation.focused_search(frame())
                    guard(point, focus=True)
                    self.gui.press("esc")
                except RpaNotReady:
                    pass
            raise
        finally:
            self.restore_clipboard(original)

    def _contacts_tab(self) -> None:
        self._click_tab(self.contacts_icon, 150, 320, "微信通讯录按钮")

    def _chat_tab(self) -> None:
        try:
            self._click_tab(self.chat_icon, 105, 190, "微信聊天按钮")
        except RpaNotReady:
            # The inactive icon is absent when the chat list is already open.
            return
        time.sleep(0.2)

    def _click_tab(self, template: np.ndarray, start: int, end: int, label: str) -> None:
        self.authorize()
        bounds = self._visible_bounds()
        x, y, width, height = bounds
        frame = self.gui.screenshot(region=(x, y, min(width, 66), min(height, end)))
        box, _, _ = self.locator._match(np.asarray(frame.convert("L")), template,
                                        (7, start, frame.width, frame.height), label)
        left, top, w, h = box
        point = x + left + w // 2, y + top + h // 2
        if self._visible_bounds() != bounds:
            raise RpaNotReady("微信窗口已移动")
        self.window.require_point(*point)
        self.gui.click(*point)

    def ensure_ready(self) -> None:
        self._verified_header = None
        original = self.get_clipboard()
        marker = "桥接自检-" + uuid.uuid4().hex
        marker_started = False
        try:
            super().ensure_ready()
            if self.draft_matches(marker):
                raise RpaNotReady("微信草稿状态无法核对")
            marker_started = True
            super().paste(marker)
            if self.read_input() != marker:
                raise RpaNotReady("微信草稿回读未通过")
            self._contacts_tab()
            deadline = time.monotonic() + 3
            while not self.draft_matches(marker):
                if time.monotonic() >= deadline:
                    raise RpaNotReady("微信未确认草稿属于授权会话，回复待人工处理")
                time.sleep(0.2)
            self._chat_tab()
            self.ensure_conversation()
            self._clear_marker(marker)
            self._verified_header = self._header
        except RpaNotReady as exc:
            if marker_started:
                raise RuntimeError("微信会话未完成草稿核对，请人工处理") from exc
            raise
        except (OSError, sqlite3.Error, StopIteration) as exc:
            if marker_started:
                raise RuntimeError("微信会话未完成草稿核对，请人工处理") from exc
            raise RpaNotReady("微信窗口或草稿库无法核对，回复待人工处理") from exc
        finally:
            self.restore_clipboard(original)

    def _clear_marker(self, marker: str) -> None:
        if self.read_input() != marker:
            raise RpaNotReady("草稿已变化，保留原内容")
        self._guard_input()
        self.gui.hotkey("ctrl", "a")
        self._guard_input()
        self.gui.press("backspace")
        if self.read_input():
            raise RpaNotReady("微信输入框未清空")

    def paste(self, text: str) -> None:
        if self._verified_header is None or self._header != self._verified_header:
            raise RpaNotReady("当前会话尚未通过本机草稿核对")
        super().paste(text)

    def send_point(self, expected: str) -> tuple[int, int]:
        if self._verified_header is None:
            raise RpaNotReady("回复需要人工核对")
        point = super().send_point(expected)
        if self._header != self._verified_header:
            raise RpaNotReady("微信会话已变化，回复需要人工核对")
        return point


def check_window(window: WindowControl) -> None:
    import pyautogui

    check_ocr()
    window.recover()
    screen = pyautogui.size()
    x, y, width, height = window.display_bounds(screen.width, screen.height)
    with Image.open(Path(__file__).with_name("wechat-controls.png")) as assets:
        navigation = SearchNavigation(assets.crop((70, 36, 93, 64)), assets.crop((100, 36, 135, 71)))
        screenshot = pyautogui.screenshot(region=(x, y, min(width, 570), min(height, 395)))
        point = navigation.search_point(screenshot)
    window.require_point(x + point[0], y + point[1])
