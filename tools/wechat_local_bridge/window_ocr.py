"""Recognize small WeChat UI regions locally with the Windows language pack."""

import json
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from .rpa import RpaNotReady


def normalized_title(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


@dataclass(frozen=True)
class TextLine:
    text: str
    x: int
    y: int


def _run(image_path: Path | None = None) -> list[TextLine]:
    command = [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-File",
        str(Path(__file__).with_name("window-ocr.ps1")),
    ]
    if image_path is not None:
        command.extend(("-ImagePath", str(image_path)))
    try:
        result = subprocess.run(command, capture_output=True, check=True, timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        rows = json.loads(result.stdout.decode("utf-8-sig"))
        return [TextLine(row["text"], row["x"], row["y"]) for row in rows]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        raise RpaNotReady("Windows 中文文字识别暂不可用") from exc


def check_ocr() -> None:
    _run()


def read_lines(image: Image.Image, runtime: Path) -> list[TextLine]:
    with TemporaryDirectory(dir=runtime) as directory:
        path = Path(directory) / "region.png"
        # Search results use smaller glyphs than the chat header.
        enlarged = image.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
        enlarged.save(path)
        return [TextLine(row.text, row.x // 2, row.y // 2) for row in _run(path)]
