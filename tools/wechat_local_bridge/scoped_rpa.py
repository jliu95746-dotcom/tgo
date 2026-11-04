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
from PIL import Image, ImageOps

from .draft_probe import DraftProbe
from .filehelper_navigation import SearchNavigation
from .reader import SessionUnread
from .rpa import RpaNotReady
from .rpa_windows import NativeRpa
from .visual_locator import VisualLocator
from .window_control import WindowControl
from .window_ocr import TextLine, check_ocr, normalized_title, read_lines, title_text_region


def matches_enterprise_title(actual: str, expected: str) -> bool:
    return bool(expected) and (actual == expected or actual.startswith(expected + "@"))


def matches_header(lines: list[TextLine], title: str, *, group: bool, enterprise: bool = False) -> bool:
    if enterprise and len(lines) == 2 and normalized_title(lines[1].text).startswith("@"):
        lines = lines[:1]
    if len(lines) != 1:
        return False
    actual, expected = normalized_title(lines[0].text), normalized_title(title)
    if enterprise:
        return matches_enterprise_title(actual, expected)
    if group and actual != expected:
        actual = re.sub(r"\([0-9]{1,6}\)$", "", actual)
    return bool(expected) and actual == expected


def result_line(
    lines: list[TextLine], title: str, sections: list[TextLine] | None = None, *,
    candidate: int | None = None,
) -> TextLine:
    headings = {"联系人", "群聊", "最近使用", "最近聊天", "常用联系人", "最常使用", "好友", "聊天", "最佳匹配",
                "企业微信联系人", "企业微信好友"}
    ignored = {"聊天记录", "网络结果", "搜索网络结果", "网页结果", "公众号", "搜一搜", "功能", "小程序"}

    def category(line: TextLine) -> str:
        value = normalized_title(line.text)
        # Windows OCR confuses 使 with 便 in the small gray section heading.
        # Apply this only to the left category label, never to a contact name.
        return "最常使用" if value == "最常便用" and line.x < 100 else value

    regions = [line for line in (lines if sections is None else sections)
               if category(line) in headings | ignored]
    matches = []
    for row in lines:
        actual, expected = normalized_title(row.text), normalized_title(title)
        if not (actual == expected or candidate is not None and matches_enterprise_title(actual, expected)):
            continue
        previous = [line for line in regions if line.y < row.y]
        if previous and category(max(previous, key=lambda line: line.y)) in headings:
            matches.append(row)
        elif not previous and any(
            line.y > row.y and category(line) in {"网络结果", "搜索网络结果", "网页结果"}
            for line in regions
        ):
            # Some WeChat builds omit the local heading above the network section.
            # Selection still has to pass the independent header and draft checks.
            matches.append(row)
    if candidate is not None and 0 <= candidate < len(matches) <= 2:
        return sorted(matches, key=lambda line: line.y)[candidate]
    if candidate is not None:
        raise RpaNotReady(f"微信搜索可核对的同名结果为 {len(matches)} 条，回复待人工处理")
    if len(matches) != 1:
        exact = sum(normalized_title(row.text) == normalized_title(title) for row in lines)
        raise RpaNotReady(f"未找到唯一的本地聊天结果（本地 {len(matches)} 项，同名 {exact} 项），稍后重试")
    return matches[0]


class ScopedRpa(NativeRpa):
    def __init__(
        self, window: WindowControl, runtime: Path, chat_id: str, title: str,
        authorize: Callable[[], None], scope_lock: AbstractContextManager,
        draft_matches: Callable[[str], bool],
        *, unread_state: Callable[[], SessionUnread | None] | None = None,
    ):
        self._configure_desktop(window, runtime, chat_id, authorize, scope_lock)
        self.title, self.draft_matches = title, draft_matches
        self.probe = DraftProbe(runtime, chat_id)
        self.unread_state = unread_state
        self._observed_unread: SessionUnread | None = None
        self._header_box: tuple[int, int, int, int] | None = None
        self._header: bytes | None = None
        self._verified_header: bytes | None = None
        self._verified_context: bytes | None = None
        self._verified_marker: str | None = None
        with Image.open(Path(__file__).with_name("wechat-controls.png")) as assets:
            self.locator = VisualLocator(assets, None, (0, 0, 114, 29),
                                         toolbar_markers=((0, 0, 27, 29), (199, 0, 226, 29)))
            self.navigation = SearchNavigation(assets.crop((70, 36, 93, 64)), assets.crop((100, 36, 135, 71)))
            self.send_template = np.asarray(assets.crop((0, 36, 63, 72)).convert("L"))
            self.contacts_icon = np.asarray(assets.crop((140, 36, 168, 68)).convert("L"))
            self.chat_icon = np.asarray(assets.crop((178, 36, 195, 63)).convert("L"))

    def _visible_bounds(self) -> tuple[int, int, int, int]:
        screen = self.gui.size()
        return self.window.display_bounds(screen.width, screen.height)

    def _click_control(self, x: int, y: int, *, popup: bool = False) -> None:
        self.authorize()
        self.window.require_point(x, y, search_result=popup)
        try:
            self.gui.mouseDown(x, y, button="primary")
            time.sleep(0.06)
        finally:
            self.gui.mouseUp(button="primary")

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
        if digest != self._header:
            label = title_text_region(header)
            for scale in (2, 4):
                if matches_header(read_lines(label, self.runtime, scale=scale), self.title,
                                  group=self.chat_id.endswith("@chatroom"), enterprise=self.unread_state is not None):
                    break
            else:
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
        if self._verified_context is not None and self._context_digest(frame) != self._verified_context:
            raise RpaNotReady("同名企微会话的聊天区域已变化，停止发送")
        super()._guard_input()
        if self._verified_header is not None and self._header != self._verified_header:
            raise RpaNotReady("已核对的微信会话发生变化")

    def _context_digest(self, frame: Image.Image) -> bytes:
        if self.target is None or self._header_box is None:
            raise RpaNotReady("同名企微会话的输入区域无法核对")
        left, top = self.target.toolbar[0], self._header_box[3]
        # The toolbar can sit below the editor. Use the detected editor edge.
        right, bottom = frame.width - 24, self.target.input_box[1] - 12
        if left >= right or top >= bottom:
            raise RpaNotReady("同名企微会话的消息区域无法核对")
        return sha256(frame.crop((left, top, right, bottom)).tobytes()).digest()

    def ensure_conversation(self) -> None:
        self.authorize()
        self.window.recover()
        try:
            self.check_target()
        except RpaNotReady as target_error:
            try:
                self._sidebar()
                self._wait_target()
                return
            except RpaNotReady as sidebar_error:
                try:
                    self._search()
                except RpaNotReady as search_error:
                    # Closing a search popup can uncover the already open chat.
                    # It still must pass the header and scoped draft checks.
                    try:
                        self._wait_target()
                        return
                    except RpaNotReady:
                        pass
                    raise RpaNotReady(f"{search_error}；会话检查：{target_error}；列表检查：{sidebar_error}") from search_error
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

    def _sidebar(self, candidate: int | None = None) -> None:
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
        expected = normalized_title(self.title)
        rows = [row for row in read_lines(sidebar, self.runtime)
                if normalized_title(row.text) == expected
                or candidate is not None and matches_enterprise_title(normalized_title(row.text), expected)]
        if (candidate is None and len(rows) != 1
                or candidate is not None and not 0 <= candidate < len(rows) <= 2):
            raise RpaNotReady("侧栏未找到唯一会话")
        row = sorted(rows, key=lambda item: item.y)[candidate or 0]
        self.authorize()
        if self._visible_bounds() != bounds or self.gui.screenshot(region=bounds).crop(box).tobytes() != sidebar.tobytes():
            raise RpaNotReady("微信侧栏发生变化")
        point = bounds[0] + box[0] + row.x, bounds[1] + box[1] + row.y
        self.window.require_point(*point)
        self._click_control(*point)
        time.sleep(0.25)

    def _search(self, candidate: int | None = None) -> None:
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
        search_opened = False
        result_clicked = False
        try:
            point = self.navigation.search_point(frame())
            guard(point)
            self._click_control(left + point[0], top + point[1])
            search_opened = True
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
            self.gui.press("right")
            for attempt in range(3):
                search = frame()
                x, y, scale = self.navigation.focused_search(search)
                # One OCR region keeps headings and results in the same layout
                # and avoids clipping the category or longer contact names.
                crop = (x + round(5 * scale), y + round(36 * scale),
                        min(search.width, x + round(382 * scale)), search.height)
                try:
                    # WeChat renders category headings in low contrast gray.
                    # Preserve exact matching instead of guessing a category.
                    rows = read_lines(search.crop(crop), self.runtime)
                    try:
                        row = result_line(rows, self.title, candidate=candidate)
                    except RpaNotReady:
                        labels = ImageOps.grayscale(search.crop(crop)).point(lambda pixel: 0 if pixel < 216 else 255)
                        sections = read_lines(labels, self.runtime)
                        row = result_line(rows, self.title, rows + sections, candidate=candidate)
                    if frame().crop(crop).tobytes() != search.crop(crop).tobytes():
                        raise RpaNotReady("微信搜索结果尚在更新，稍后重试")
                except RpaNotReady:
                    if attempt == 2:
                        raise
                    time.sleep(0.35)
                    continue
                result = (crop[0] + row.x, crop[1] + row.y)
                guard(result, popup=True)
                self._click_control(left + result[0], top + result[1], popup=True)
                result_clicked = True
                break
            time.sleep(0.3)
            if self.window.search_popup_at(left + result[0], top + result[1]):
                current = frame()
                self.navigation.focused_search(current)
                if current.crop(crop).tobytes() != search.crop(crop).tobytes():
                    raise RpaNotReady("选中后的搜索结果已变化，停止确认")
                guard(point, focus=True, popup=True)
                if not self.window.search_popup_at(left + result[0], top + result[1]):
                    raise RpaNotReady("搜索弹层已关闭，停止确认")
                self.gui.press("enter")
                time.sleep(0.3)
        finally:
            if search_opened and not result_clicked and point is not None:
                try:
                    self.navigation.focused_search(frame())
                    guard(point, focus=True)
                    self.gui.press("esc")
                except (RpaNotReady, OSError):
                    pass
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
        self._click_control(*point)

    def ensure_ready(self) -> None:
        self._verified_header = None
        self._verified_context = None
        self._verified_marker = None
        original = self.get_clipboard()
        marker = "桥接自检-" + uuid.uuid4().hex
        marker_started = False
        try:
            if self.unread_state is not None:
                before = self.unread_state()
                if before is not None and before.count > 0:
                    self._observed_unread = before
                    self._open_unread_same_title(before)
                elif self._observed_unread is not None and self._matches_read_transition(self._observed_unread, before):
                    self.window.recover()
                    self.check_target()
                else:
                    raise RpaNotReady("同名企微会话缺少可核对的未读消息，回复待人工处理")
            if pending := self.probe.pending():
                self.ensure_conversation()
                if self.read_input() == pending and not self.draft_matches(pending):
                    self._contacts_tab()
                    self._chat_tab()
                    self.ensure_conversation()
                if self.probe.recover(self.read_input, self.draft_matches, self._clear_marker,
                                      keep_marker=self.unread_state is not None):
                    if self.unread_state is None:
                        super().ensure_ready()
                    else:
                        self._verified_marker, self._observed_unread = pending, None
                    self._verified_header = self._header
                    return
            super().ensure_ready()
            if self.draft_matches(marker):
                raise RpaNotReady("微信草稿状态无法核对")
            self.probe.remember(marker)
            marker_started = True
            self._probe_editor(marker)
        except RpaNotReady as exc:
            if marker_started:
                raise RpaNotReady(f"本机校验尚未完成，稍后自动恢复：{exc}") from exc
            raise
        except RuntimeError as exc:
            if marker_started:
                raise RpaNotReady("本机校验草稿等待恢复，稍后重试") from exc
            raise
        except (OSError, sqlite3.Error, StopIteration) as exc:
            if marker_started:
                raise RpaNotReady("本机校验草稿等待恢复，稍后重试") from exc
            raise RpaNotReady("微信窗口或草稿库无法核对，回复待人工处理") from exc
        finally:
            self.restore_clipboard(original)

    def _probe_editor(self, marker: str) -> None:
        super().paste(marker)
        if self.read_input() != marker:
            raise RpaNotReady("微信草稿回读未通过")
        self._contacts_tab()
        self._chat_tab()
        deadline = time.monotonic() + 5
        while not self.draft_matches(marker):
            if time.monotonic() >= deadline:
                raise RpaNotReady("微信尚未保存授权会话的校验草稿")
            time.sleep(0.2)
        self.ensure_conversation()
        if self.unread_state is None:
            self._clear_marker(marker)
            self.probe.complete(marker)
        else:
            if self.read_input() != marker:
                raise RpaNotReady("当前输入框未保留已核对的会话标记")
            self._verified_marker = marker
            self._observed_unread = None
        self._verified_header = self._header

    def _open_unread_same_title(self, before: SessionUnread) -> None:
        assert self.unread_state is not None
        self.window.recover()
        for candidate in range(2):
            try:
                self._sidebar(candidate)
                self._wait_target()
            except RpaNotReady:
                self._search(candidate)
                self._wait_target()
            try:
                self._confirm_unread_transition(before)
            except RpaNotReady:
                if candidate == 0 and self.unread_state() == before:
                    continue
                raise
            return

    def _confirm_unread_transition(self, before: SessionUnread) -> None:
        assert self.unread_state is not None
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            after = self.unread_state()
            if self._matches_read_transition(before, after):
                return
            time.sleep(0.15)
        raise RpaNotReady("微信未确认打开的是这条企微会话，回复待人工处理")

    @staticmethod
    def _matches_read_transition(before: SessionUnread, after: SessionUnread | None) -> bool:
        return bool(after and after.count == 0 and after.last_cleared > before.last_cleared
                    and after.last_message == before.last_message)

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
        if self._verified_marker is None:
            super().paste(text)
            return
        with self.scope_lock:
            if self.read_input() != self._verified_marker:
                raise RpaNotReady("同名企微会话的草稿标记已变化，停止填写")
            self.clipboard.copy(text)
            self._guard_input()
            self.gui.hotkey("ctrl", "a")
            self._guard_input()
            self.gui.hotkey("ctrl", "v")
            time.sleep(0.3)
            if self.read_input() != text:
                raise RpaNotReady("客服回复未通过输入框回读")
            self.probe.complete(self._verified_marker)
            self._verified_context = self._context_digest(self.gui.screenshot(region=self.bounds).convert("RGB"))
            self._verified_marker = None

    def send_point(self, expected: str) -> tuple[int, int]:
        if self._verified_header is None:
            if self.unread_state is not None:
                raise RpaNotReady("同名企微会话需要重新核对，保留草稿")
            # A filled outbox survives a restart. Rebuild its proof from the
            # unique authorized title, exact editor text and internal chat ID.
            self.ensure_conversation()
            original = self.get_clipboard()
            try:
                if self.read_input() != expected or not self.draft_matches(expected):
                    raise RpaNotReady("已填写回复与授权会话草稿不一致，保留原内容")
                self.check_target()
                self._verified_header = self._header
            finally:
                self.restore_clipboard(original)
        point = super().send_point(expected)
        if self._header != self._verified_header:
            raise RpaNotReady("微信会话已变化，回复需要人工核对")
        return point


def check_window(window: WindowControl) -> None:
    window.use_physical_coordinates()
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
