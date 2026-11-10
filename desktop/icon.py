"""Tray icon rendering: a tiny live gauge drawn with QPainter.

Three looks are available, all readable at 16 px:
  * ``ring``  - circular gauge + percentage
  * ``bars``  - three stacked bars (CPU / GPU / RAM)
  * ``text``  - plain percentage on a rounded chip
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPen, QPixmap

from .hardware import format_percent

# accent colours
CPU_COLOR = "#4f9cf9"
GPU_COLOR = "#31c48d"
RAM_COLOR = "#f0a02a"
DISK_COLOR = "#a78bfa"

GOOD = "#31c48d"
WARN = "#f0b429"
HOT = "#ef5350"


def usage_color(value: float) -> QColor:
    if value is None or value < 0:
        return QColor("#78808f")
    if value >= 85:
        return QColor(HOT)
    if value >= 60:
        return QColor(WARN)
    return QColor(GOOD)


def _metric_value(sample: dict, metric: str) -> tuple[float, QColor]:
    gpus = sample.get("gpus") or []
    gpu_util = max((g.get("utilization", -1) or -1) for g in gpus) if gpus else -1
    cpu = sample.get("cpu", {}).get("percent", -1) or -1
    ram = sample.get("ram", {}).get("percent", -1) or -1

    if metric == "gpu":
        return gpu_util, QColor(GPU_COLOR)
    if metric == "cpu":
        return cpu, QColor(CPU_COLOR)
    if metric == "ram":
        return ram, QColor(RAM_COLOR)

    # auto: prefer the GPU when there is one, otherwise CPU
    if gpu_util >= 0:
        return gpu_util, QColor(GPU_COLOR)
    return cpu, QColor(CPU_COLOR)


def _draw_ring(p: QPainter, rect: QRectF, value: float, color: QColor, text_color: QColor) -> None:
    width = max(2.0, rect.width() * 0.14)
    inner = rect.adjusted(width / 2, width / 2, -width / 2, -width / 2)

    track = QPen(QColor(255, 255, 255, 46) if text_color.lightness() > 127 else QColor(0, 0, 0, 38))
    track.setWidthF(width)
    track.setCapStyle(Qt.RoundCap)
    p.setPen(track)
    p.drawArc(inner, 0, 360 * 16)

    if value > 0:
        arc = QPen(color)
        arc.setWidthF(width)
        arc.setCapStyle(Qt.RoundCap)
        p.setPen(arc)
        p.drawArc(inner, 90 * 16, -int(360 * 16 * min(value, 100) / 100))

    if rect.width() >= 24:
        font = QFont()
        font.setPixelSize(max(7, int(rect.width() * 0.40)))
        font.setBold(True)
        p.setFont(font)
        p.setPen(text_color)
        label = str(int(round(max(value, 0)))) if value >= 0 else "–"
        p.drawText(rect, Qt.AlignCenter, label)


def _draw_bars(p: QPainter, rect: QRectF, sample: dict, text_color: QColor) -> None:
    rows = [
        (sample.get("cpu", {}).get("percent", -1), QColor(CPU_COLOR)),
        (
            max((g.get("utilization", -1) or -1) for g in sample.get("gpus") or []) if sample.get("gpus") else -1,
            QColor(GPU_COLOR),
        ),
        (sample.get("ram", {}).get("percent", -1), QColor(RAM_COLOR)),
    ]
    gap = max(1.0, rect.height() * 0.10)
    bar_h = (rect.height() - gap * 2) / 3.0
    radius = bar_h / 2.0
    for i, (value, color) in enumerate(rows):
        y = rect.top() + i * (bar_h + gap)
        track = QRectF(rect.left(), y, rect.width(), bar_h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 42) if text_color.lightness() > 127 else QColor(0, 0, 0, 34))
        p.drawRoundedRect(track, radius, radius)
        if value > 0:
            filled = QRectF(track.left(), y, max(bar_h, track.width() * min(value, 100) / 100.0), bar_h)
            p.setBrush(color)
            p.drawRoundedRect(filled, radius, radius)


def _draw_text(p: QPainter, rect: QRectF, value: float, color: QColor, text_color: QColor) -> None:
    radius = rect.width() * 0.30
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color.red(), color.green(), color.blue(), 46))
    p.drawRoundedRect(rect, radius, radius)

    font = QFont()
    font.setPixelSize(max(7, int(rect.width() * 0.46)))
    font.setBold(True)
    p.setFont(font)
    p.setPen(color if value >= 0 else text_color)
    label = str(int(round(max(value, 0)))) if value >= 0 else "–"
    p.drawText(rect, Qt.AlignCenter, label)


def render_pixmap(
    style: str,
    sample: dict,
    metric: str = "auto",
    size: int = 32,
    dark_tray: bool = True,
) -> QPixmap:
    """Render one tray icon size."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)

    text_color = QColor("#f2f4f8") if dark_tray else QColor("#1c1f26")
    value, color = _metric_value(sample, metric)

    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)

    pad = size * 0.06
    rect = QRectF(pad, pad, size - pad * 2, size - pad * 2)

    if style == "bars":
        _draw_bars(p, rect, sample, text_color)
    elif style == "text":
        _draw_text(p, rect, value, color, text_color)
    else:
        _draw_ring(p, rect, value, color, text_color)

    p.end()
    return pm


def make_icon(style: str, sample: dict, metric: str = "auto", dark_tray: bool = True) -> QIcon:
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64):
        icon.addPixmap(render_pixmap(style, sample, metric, size, dark_tray))
    return icon


def app_icon(size: int = 256, sample: dict | None = None) -> QPixmap:
    """A larger, branded icon used for windows / autostart entries."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)

    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, QColor("#1f2937"))
    grad.setColorAt(1.0, QColor("#0f1115"))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    radius = size * 0.22
    p.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    if sample is None:
        sample = {"cpu": {"percent": 34}, "ram": {"percent": 58}, "gpus": [{"utilization": 72}]}
    inner = QRectF(size * 0.15, size * 0.15, size * 0.70, size * 0.70)
    _draw_ring(p, inner, sample.get("gpus", [{}])[0].get("utilization", 0) or 0, QColor(GPU_COLOR), QColor("#f2f4f8"))
    p.end()
    return pm
