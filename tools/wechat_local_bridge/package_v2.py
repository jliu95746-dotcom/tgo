"""Build the downloadable Windows bridge without local data or dev tooling."""

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main() -> None:
    package = Path(__file__).resolve().parent
    repo = package.parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=(
        repo / "repos/tgo-web/public/downloads/wechat-local-bridge-v2.zip"
    ))
    output = parser.parse_args().output
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.write(package / "start-wechat-bridge.cmd", "start-wechat-bridge.cmd")
        for path in sorted(package.iterdir()):
            if path.is_file() and path.name != "package_v2.py" and (
                path.suffix in {".py", ".html", ".js"}
                or path.name in {"requirements.txt", "NOTICE.txt", "window-ocr.ps1", "wechat-controls.png"}
            ):
                archive.write(path, Path("tools/wechat_local_bridge") / path.name)
    print(output)


if __name__ == "__main__":
    main()
