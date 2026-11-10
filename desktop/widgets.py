"""Small custom widgets used by the popup panel."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget


def _color(value: str) -> QColor:
    return QColor(value)


class ElidedLabel(QLabel):
    """A QLabel that shortens its text instead of growing the window."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        self._full = text or ""
        super().setText(self._full)
        self._apply()

    def _apply(self) -> None:
        metrics = self.fontMetrics()
        available = max(0, self.width() - 2)
        super().setText(metrics.elidedText(self._full, Qt.ElideRight, available))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply()


class UsageBar(QWidget):
    """Thin rounded progress bar with an optional threshold colour."""

    def __init__(self, color: str = "#4f9cf9", height: int = 6, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = -1.0
        self._color = _color(color)
        self._track = QColor(255, 255, 255, 30)
        self.setFixedHeight(height)
        self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_track(self, color: QColor) -> None:
        self._track = color
        self.update()

    def set_color(self, color: str) -> None:
        self._color = _color(color)
        self.update()

    def set_value(self, value: float) -> None:
        self._value = float(value) if value is not None else -1.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        radius = self.height() / 2.0
        rect = QRectF(0, 0, self.width(), self.height())
        p.setPen(Qt.NoPen)
        p.setBrush(self._track)
        p.drawRoundedRect(rect, radius, radius)
        if self._value > 0:
            width = max(self.height(), rect.width() * min(self._value, 100.0) / 100.0)
            p.setBrush(self._color)
            p.drawRoundedRect(QRectF(0, 0, width, rect.height()), radius, radius)
        p.end()


class HistoryChart(QWidget):
    """Compact CPU / GPU history line chart (no axes, no labels)."""

    def __init__(self, height: int = 40, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cpu: list[float] = []
        self._gpu: list[float] = []
        self._cpu_color = QColor("#4f9cf9")
        self._gpu_color = QColor("#31c48d")
        self._grid = QColor(255, 255, 255, 16)
        self.setFixedHeight(height)
        self.setMinimumWidth(80)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_colors(self, grid: QColor, cpu: str = "#4f9cf9", gpu: str = "#31c48d") -> None:
        self._grid = grid
        self._cpu_color = QColor(cpu)
        self._gpu_color = QColor(gpu)
        self.update()

    def push(self, cpu: float, gpu: float, limit: int = 60) -> None:
        self._cpu.append(max(0.0, min(100.0, cpu if cpu >= 0 else 0.0)))
        self._gpu.append(gpu if gpu is not None else -1.0)
        if len(self._cpu) > limit:
            self._cpu.pop(0)
            self._gpu.pop(0)
        self.update()

    def _points(self, series: list[float]) -> list[QPointF]:
        if len(series) < 2:
            return []
        step = self.width() / (len(series) - 1)
        return [QPointF(i * step, self.height() - (v / 100.0) * (self.height() - 2) - 1) for i, v in enumerate(series)]

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        pen = QPen(self._grid)
        pen.setWidth(1)
        p.setPen(pen)
        for ratio in (0.0, 0.5, 1.0):
            y = 1 + ratio * (self.height() - 2)
            p.drawLine(QPointF(0, y), QPointF(self.width(), y))

        for series, color in ((self._cpu, self._cpu_color), (self._gpu, self._gpu_color)):
            points = self._points(series)
            if not points:
                continue
            path = QPainterPath(points[0])
            for pt in points[1:]:
                path.lineTo(pt)

            fill = QPainterPath(path)
            fill.lineTo(QPointF(points[-1].x(), self.height()))
            fill.lineTo(QPointF(points[0].x(), self.height()))
            fill.closeSubpath()
            tint = QColor(color)
            tint.setAlpha(38)
            p.setPen(Qt.NoPen)
            p.setBrush(tint)
            p.drawPath(fill)

            stroke = QPen(color)
            stroke.setWidthF(1.4)
            p.setPen(stroke)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

        p.end()


class LabeledBar(QWidget):
    """``label  [=====   ]  value`` row."""

    def __init__(
        self,
        label: str,
        color: str = "#4f9cf9",
        label_width: int = 46,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.label = QLabel(label)
        self.label.setObjectName("dim")
        self.label.setFixedWidth(label_width)
        self.label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.bar = UsageBar(color, 6)
        self.value = QLabel("—")
        self.value.setObjectName("value")
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value.setMinimumWidth(74)

        layout.addWidget(self.label)
        layout.addWidget(self.bar, 1)
        layout.addWidget(self.value)

    def update_values(self, percent: float, text: str, tooltip: str = "") -> None:
        self.bar.set_value(percent)
        self.value.setText(text)
        if tooltip:
            self.setToolTip(tooltip)

    def set_color(self, color: str) -> None:
        self.bar.set_color(color)

    def set_track(self, color: QColor) -> None:
        self.bar.set_track(color)


class Badge(QLabel):
    """Small pill used for temperatures / status."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self._apply("#31c48d")

    def _apply(self, color: str) -> None:
        c = QColor(color)
        self.setStyleSheet(
            "QLabel {"
            f"color: {color};"
            f"background: rgba({c.red()},{c.green()},{c.blue()},40);"
            "border-radius: 6px; padding: 1px 6px; font-size: 11px; font-weight: 600;"
            "}"
        )

    def set_state(self, text: str, color: str) -> None:
        self.setText(text)
        self._apply(color)


def temp_color(value: float) -> str:
    if value is None or value < 0:
        return "#9aa3b2"
    if value >= 82:
        return "#ef5350"
    if value >= 70:
        return "#f0b429"
    return "#31c48d"


def severity_color(value: float) -> str:
    if value is None or value < 0:
        return "#9aa3b2"
    if value >= 85:
        return "#ef5350"
    if value >= 60:
        return "#f0b429"
    return "#31c48d"


def section_label(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("section")
    font = QFont()
    font.setPixelSize(10)
    font.setBold(True)
    label.setFont(font)
    return label
