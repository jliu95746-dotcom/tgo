"""Read-only UIA diagnostics. Never calls SetValue, SetFocus or legacy actions."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TypedDict

from .window_control import WindowControl


class UiaNode(TypedDict):
    depth: int
    control_type: int
    class_name: str
    name_length: int
    value_writable: bool
    patterns: list[str]


def probe_uia(binding_file: Path, runtime: Path) -> Path:
    import comtypes.client

    binding = json.loads(binding_file.read_text(encoding="utf-8"))
    if binding.get("chat_id") != "filehelper":
        raise ValueError("本轮只诊断文件传输助手所在微信窗口")
    window = WindowControl(binding["window_id"], binding["pid"])
    window.validate()
    uia = comtypes.client.GetModule("UIAutomationCore.dll")
    api = comtypes.client.CreateObject(uia.CUIAutomation8, interface=uia.IUIAutomation2)
    api.AutoSetFocus = False
    api.ConnectionTimeout = 1500
    api.TransactionTimeout = 1500
    before = window.user.GetForegroundWindow()
    minimized = bool(window.user.IsIconic(window.hwnd))
    root = api.ElementFromHandle(window.hwnd)
    views: dict[str, list[UiaNode]] = {}
    deadline = time.monotonic() + 30
    truncated = False
    for label, walker in (
        ("raw", api.RawViewWalker),
        ("control", api.ControlViewWalker),
    ):
        queue = [(root, 0)]
        rows: list[UiaNode] = []
        while queue and len(rows) < 200 and time.monotonic() < deadline:
            node, depth = queue.pop(0)
            patterns: list[str] = []
            writable = False
            for name, pattern in (
                ("Value", uia.UIA_ValuePatternId),
                ("Text", uia.UIA_TextPatternId),
                ("LegacyIAccessible", uia.UIA_LegacyIAccessiblePatternId),
            ):
                try:
                    value = node.GetCurrentPattern(pattern)
                    if value:
                        patterns.append(name)
                        if name == "Value":
                            writable = not value.QueryInterface(
                                uia.IUIAutomationValuePattern
                            ).CurrentIsReadOnly
                except comtypes.COMError:
                    pass
            rows.append(
                UiaNode(
                    depth=depth,
                    control_type=node.CurrentControlType,
                    class_name=node.CurrentClassName,
                    name_length=len(node.CurrentName or ""),
                    value_writable=writable,
                    patterns=patterns,
                )
            )
            if depth < 12:
                child = walker.GetFirstChildElement(node)
                while child and len(queue) + len(rows) < 200:
                    queue.append((child, depth + 1))
                    child = walker.GetNextSiblingElement(child)
        truncated = truncated or bool(queue)
        views[label] = rows
    report = {
        "minimized": minimized,
        "foreground": before == window.hwnd,
        "foreground_unchanged": window.user.GetForegroundWindow() == before,
        "minimized_unchanged": bool(window.user.IsIconic(window.hwnd)) == minimized,
        "auto_set_focus": False,
        "editable_value_candidates": sum(
            row["value_writable"] and row["control_type"] in (50004, 50030)
            for row in views["raw"]
        ),
        "truncated": truncated,
        "views": views,
    }
    output = runtime / "uia-capabilities.json"
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output
