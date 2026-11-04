"""Calibrate a user's own File Transfer Assistant window without sending."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from PIL import Image, ImageTk

from .rpa import RpaNotReady
from .rpa_windows import NativeRpa
from .visual_locator import VisualLocator
from .window_control import WindowControl

CALIBRATION_DRAFT = "个人微信校准"


def _select_anchors(image: Image.Image) -> list[tuple[int, int, int, int]]:
    import tkinter as tk

    prompts = (
        "框选会话顶部的「文件传输助手」标题文字",
        "框选输入区左下角的工具栏图标",
        "框选右下角绿色发送按钮的边缘和文字",
    )
    app = tk.Tk()
    app.title("个人微信窗口校准")
    scale = min(1.0, (app.winfo_screenwidth() - 80) / image.width,
                (app.winfo_screenheight() - 140) / image.height)
    preview = ImageTk.PhotoImage(image.resize((round(image.width * scale), round(image.height * scale))))
    label = tk.Label(app, text=prompts[0], padx=14, pady=12)
    label.pack()
    canvas = tk.Canvas(app, width=preview.width(), height=preview.height(), highlightthickness=0)
    canvas.pack()
    canvas.create_image(0, 0, anchor="nw", image=preview)
    boxes: list[tuple[int, int, int, int]] = []
    start: tuple[int, int] | None = None
    rectangle: int | None = None

    def begin(event):
        nonlocal start, rectangle
        start = (event.x, event.y)
        rectangle = canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#15803d", width=2)

    def move(event):
        if start is not None and rectangle is not None:
            canvas.coords(rectangle, *start, event.x, event.y)

    def finish(event):
        nonlocal start, rectangle
        if start is None or rectangle is None:
            return
        x1, x2 = sorted((start[0], event.x))
        y1, y2 = sorted((start[1], event.y))
        box = (
            max(0, round(x1 / scale)), max(0, round(y1 / scale)),
            min(image.width, round(x2 / scale)), min(image.height, round(y2 / scale)),
        )
        start = None
        if box[2] - box[0] < 12 or box[3] - box[1] < 10:
            canvas.delete(rectangle)
            label.configure(text="选框太小，请重画。"+prompts[len(boxes)])
            return
        boxes.append(box)
        if len(boxes) == len(prompts):
            app.destroy()
        else:
            label.configure(text=prompts[len(boxes)])

    canvas.bind("<ButtonPress-1>", begin)
    canvas.bind("<B1-Motion>", move)
    canvas.bind("<ButtonRelease-1>", finish)
    app.bind("<Escape>", lambda _: app.destroy())
    app.mainloop()
    if len(boxes) != len(prompts):
        raise RpaNotReady("用户取消窗口校准")
    return boxes


def calibrate(runtime: Path, pid: int, window_id: int) -> None:
    import pyautogui

    window = WindowControl(window_id, pid)
    window.recover()
    left, top, width, height = window.bounds()
    screen = pyautogui.size()
    if left < 0 or top < 0 or left + width > screen.width or top + height > screen.height:
        raise RpaNotReady("请把微信窗口完整移到主屏，再开始校准")
    image = pyautogui.screenshot(region=(left, top, width, height)).convert("RGB")
    title, toolbar, send = _select_anchors(image)
    VisualLocator(image, title, toolbar).locate(image)

    # The original binding stays available until the exact draft and button pass.
    with TemporaryDirectory(dir=runtime) as directory:
        path = Path(directory)
        snapshot = path / "reference.bmp"
        image.save(snapshot)
        binding = {
            "chat_id": "filehelper", "pid": pid, "window_id": window_id,
            "reference_image": str(snapshot), "send_reference_image": str(snapshot),
            "title_anchor_box": title, "toolbar_anchor_box": toolbar, "send_anchor_box": send,
        }
        temporary_binding = path / "rpa-binding.private.json"
        temporary_binding.write_text(json.dumps(binding), encoding="utf-8")
        backend = NativeRpa(temporary_binding, runtime)
        backend.send_point(CALIBRATION_DRAFT)
        reference = runtime.resolve() / f"calibration-{uuid4().hex}.private.bmp"
        snapshot.replace(reference)
        binding.update(reference_image=str(reference), send_reference_image=str(reference))
        temporary_binding.write_text(json.dumps(binding, indent=2), encoding="utf-8")
        temporary_binding.replace(runtime / "rpa-binding.private.json")
