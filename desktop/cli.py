"""Command line entry point: ``python run.py [options]``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__


def _assets_dir() -> Path:
    return Path(__file__).resolve().parent / "assets"


def make_icons() -> int:
    """Render the app icon assets (png / ico) used by autostart entries."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from .icon import app_icon

    app = QApplication.instance() or QApplication([])
    assets = _assets_dir()
    assets.mkdir(parents=True, exist_ok=True)

    pixmap = app_icon(512)
    png_path = assets / "icon.png"
    pixmap.save(str(png_path), "PNG")

    # multi-resolution .ico for Windows shortcuts / exe icon
    try:
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        from PySide6.QtGui import QImage

        from PIL import Image

        frames = []
        for size in (16, 24, 32, 48, 64, 128, 256):
            image = app_icon(size).toImage().convertToFormat(QImage.Format_RGBA8888)  # RGBA8888
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.WriteOnly)
            image.save(buffer, "PNG")
            buffer.close()
            frames.append(Image.open(__import__("io").BytesIO(bytes(data))))
        ico_path = assets / "icon.ico"
        frames[-1].save(
            ico_path,
            format="ICO",
            sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)],
        )
        for frame in frames:
            frame.close()
    except Exception as exc:
        print(f"ICO 生成跳过: {exc}")

    print(f"图标已生成: {png_path}")
    _ = app
    return 0


def preview_icon(style: str, metric: str, out: str | None) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtWidgets import QApplication

    from .icon import render_pixmap

    QApplication.instance() or QApplication([])
    sample = {
        "cpu": {"percent": 41, "name": "CPU"},
        "ram": {"percent": 63},
        "gpus": [{"utilization": 78, "name": "GPU"}],
    }
    sizes = (16, 22, 32, 64)
    sheet = QPixmap(sum(sizes) + 20 * len(sizes), 96)
    sheet.fill(0)
    p = QPainter(sheet)
    x = 10
    for size in sizes:
        pm = render_pixmap(style, sample, metric, size, True)
        p.drawPixmap(x, (96 - size) // 2, pm)
        pm2 = render_pixmap(style, sample, metric, size, False)
        p.drawPixmap(x, 96 - size - 6, pm2)
        x += size + 20
    p.end()

    target = Path(out) if out else _assets_dir() / "icon-preview.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(str(target), "PNG")
    print(f"预览已保存: {target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gpu-monitor",
        description="跨平台任务栏 / 菜单栏 GPU 资源监控（来自 ComfyUI-Crystools）",
    )
    parser.add_argument("--version", action="version", version=f"GPU Monitor {__version__}")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--install", action="store_true", help="安装开机自启动")
    group.add_argument("--uninstall", action="store_true", help="移除开机自启动")
    group.add_argument("--make-icons", action="store_true", help="生成应用图标资源")
    group.add_argument("--preview-icon", action="store_true", help="导出托盘图标预览图")
    parser.add_argument("--style", default="ring", choices=["ring", "bars", "text"])
    parser.add_argument("--metric", default="auto", choices=["auto", "gpu", "cpu", "ram"])
    parser.add_argument("--out", default=None)
    parser.add_argument("--debug", action="store_true")

    args = parser.parse_args(argv)

    if args.make_icons:
        return make_icons()

    if args.preview_icon:
        return preview_icon(args.style, args.metric, args.out)

    if args.install:
        from . import autostart

        if autostart.install():
            print(f"已安装开机自启: {autostart.target_path()}")
            print("下次登录系统时会自动在托盘启动。")
            return 0
        print("安装开机自启失败，请检查权限。", file=sys.stderr)
        return 1

    if args.uninstall:
        from . import autostart

        autostart.uninstall()
        print("已移除开机自启。")
        return 0

    from .app import run

    return run()
