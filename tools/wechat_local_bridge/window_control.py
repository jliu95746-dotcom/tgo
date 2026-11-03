"""Ordinary Win32 window recovery and input guards, without memory writes."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time
from pathlib import PureWindowsPath

from .rpa import RpaNotReady


class GuiThreadInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("flags", wt.DWORD),
        ("hwndActive", wt.HWND),
        ("hwndFocus", wt.HWND),
        ("hwndCapture", wt.HWND),
        ("hwndMenuOwner", wt.HWND),
        ("hwndMoveSize", wt.HWND),
        ("hwndCaret", wt.HWND),
        ("rcCaret", wt.RECT),
    ]


class WindowControl:
    def __init__(self, hwnd: int, pid: int):
        self.hwnd, self.pid = hwnd, pid
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "IsWindow": ([wt.HWND], wt.BOOL),
            "IsIconic": ([wt.HWND], wt.BOOL),
            "GetForegroundWindow": ([], wt.HWND),
            "GetWindowThreadProcessId": ([wt.HWND, ctypes.POINTER(wt.DWORD)], wt.DWORD),
            "GetWindowRect": ([wt.HWND, ctypes.POINTER(wt.RECT)], wt.BOOL),
            "ShowWindow": ([wt.HWND, ctypes.c_int], wt.BOOL),
            "SetForegroundWindow": ([wt.HWND], wt.BOOL),
            "BringWindowToTop": ([wt.HWND], wt.BOOL),
            "WindowFromPoint": ([wt.POINT], wt.HWND),
            "GetAncestor": ([wt.HWND, wt.UINT], wt.HWND),
            "GetClassNameW": ([wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetGUIThreadInfo": ([wt.DWORD, ctypes.POINTER(GuiThreadInfo)], wt.BOOL),
            "OpenInputDesktop": ([wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            "CloseDesktop": ([wt.HANDLE], wt.BOOL),
            "GetUserObjectInformationW": (
                [
                    wt.HANDLE,
                    ctypes.c_int,
                    wt.LPVOID,
                    wt.DWORD,
                    ctypes.POINTER(wt.DWORD),
                ],
                wt.BOOL,
            ),
        }
        for name, (arguments, result) in signatures.items():
            method = getattr(self.user, name)
            method.argtypes, method.restype = arguments, result
        self.kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        self.kernel.OpenProcess.restype = wt.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [
            wt.HANDLE,
            wt.DWORD,
            wt.LPWSTR,
            ctypes.POINTER(wt.DWORD),
        ]
        self.kernel.CloseHandle.argtypes = [wt.HANDLE]

    def validate(self) -> None:
        pid = wt.DWORD()
        self.user.GetWindowThreadProcessId(self.hwnd, ctypes.byref(pid))
        if not self.user.IsWindow(self.hwnd) or pid.value != self.pid:
            raise RpaNotReady("微信窗口或进程身份已改变")
        handle = self.kernel.OpenProcess(0x1000, False, self.pid)
        if not handle:
            raise RpaNotReady("无法核对微信程序路径")
        try:
            path, size = ctypes.create_unicode_buffer(32768), wt.DWORD(32768)
            if (
                not self.kernel.QueryFullProcessImageNameW(
                    handle, 0, path, ctypes.byref(size)
                )
                or PureWindowsPath(path.value).name.lower() != "weixin.exe"
            ):
                raise RpaNotReady("目标进程不是 Weixin.exe")
        finally:
            self.kernel.CloseHandle(handle)

    def require_desktop(self) -> None:
        desktop = self.user.OpenInputDesktop(0, False, 1)
        if not desktop:
            raise RpaNotReady("桌面不可交互，等待解锁或恢复会话")
        try:
            name, needed = ctypes.create_unicode_buffer(256), wt.DWORD()
            if (
                not self.user.GetUserObjectInformationW(
                    desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed)
                )
                or name.value.lower() != "default"
            ):
                raise RpaNotReady("当前不是普通交互桌面，停止输入")
        finally:
            self.user.CloseDesktop(desktop)

    def recover(self) -> None:
        self.require_desktop()
        self.validate()
        if self.user.IsIconic(self.hwnd):
            self.user.ShowWindow(self.hwnd, 9)
        if self.user.GetForegroundWindow() != self.hwnd:
            self.user.BringWindowToTop(self.hwnd)
            self.user.SetForegroundWindow(self.hwnd)
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            if self.user.GetForegroundWindow() == self.hwnd and not self.user.IsIconic(
                self.hwnd
            ):
                time.sleep(0.2)
                return
            time.sleep(0.05)
        raise RpaNotReady("系统未允许微信恢复前台，本轮不填写")

    def bounds(self) -> tuple[int, int, int, int]:
        self.validate()
        if self.user.GetForegroundWindow() != self.hwnd or self.user.IsIconic(
            self.hwnd
        ):
            raise RpaNotReady("微信已不在可见前台")
        rect = wt.RECT()
        if not self.user.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            raise RpaNotReady("无法读取微信窗口边界")
        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top

    def display_bounds(self, screen_width: int, screen_height: int) -> tuple[int, int, int, int]:
        left, top, width, height = self.bounds()
        if 0 <= left < left + width <= screen_width and 0 <= top < top + height <= screen_height:
            return left, top, width, height
        # A snapped window can have invisible resize borders outside the screen.
        frame = wt.RECT()
        status = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wt.HWND(self.hwnd), 9, ctypes.byref(frame), ctypes.sizeof(frame),
        )
        if status == 0 and (
            0 <= frame.left < frame.right <= screen_width
            and 0 <= frame.top < frame.bottom <= screen_height
        ):
            return frame.left, frame.top, frame.right - frame.left, frame.bottom - frame.top
        raise RpaNotReady("请将微信窗口完整放到主屏后重试")

    def require_point(
        self, x: int, y: int, *, focus: bool = False, search_result: bool = False
    ) -> None:
        self.require_desktop()
        self.bounds()
        hit = self.user.WindowFromPoint(wt.POINT(x, y))
        root = self.user.GetAncestor(hit, 2)
        if root != self.hwnd and not (search_result and self._is_search_popup(root)):
            raise RpaNotReady("输入位置被其他窗口遮挡，停止输入")
        if focus:
            thread = self.user.GetWindowThreadProcessId(self.hwnd, None)
            info = GuiThreadInfo(cbSize=ctypes.sizeof(GuiThreadInfo))
            if (
                not self.user.GetGUIThreadInfo(thread, ctypes.byref(info))
                or self.user.GetAncestor(info.hwndFocus, 2) != self.hwnd
            ):
                raise RpaNotReady("输入焦点不属于微信，停止输入")

    def _is_search_popup(self, hwnd: int) -> bool:
        pid = wt.DWORD()
        name = ctypes.create_unicode_buffer(260)
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return (
            pid.value == self.pid
            and bool(self.user.GetClassNameW(hwnd, name, len(name)))
            and name.value == "Qt51514QWindowToolSaveBits"
        )
