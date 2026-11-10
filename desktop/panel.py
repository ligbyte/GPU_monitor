"""The frameless popup panel that drops out of the tray icon."""

from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .hardware import format_bytes, format_percent, format_temp
from .theme import palette, stylesheet
from .widgets import Badge, ElidedLabel, HistoryChart, LabeledBar, UsageBar, section_label, temp_color


def _uptime(seconds: float) -> str:
    if seconds is None or seconds < 0:
        return "—"
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}天 {hours}小时"
    if hours:
        return f"{hours}小时 {minutes}分"
    return f"{minutes}分"


class GPUBlock(QWidget):
    def __init__(self, theme: str = "dark", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        c = palette(theme)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self.tag = QLabel("GPU")
        self.tag.setObjectName("section")
        self.name = ElidedLabel("—")
        self.name.setStyleSheet("font-weight: 600;")
        self.temp = Badge("—")
        head.addWidget(self.tag)
        head.addWidget(self.name, 1)
        head.addWidget(self.temp)
        layout.addLayout(head)

        self.util = LabeledBar("使用率", c["good"])
        self.vram = LabeledBar("显存", c["good"], label_width=46)
        layout.addWidget(self.util)
        layout.addWidget(self.vram)

        self.util.set_track(QColor(c["track"]))
        self.vram.set_track(QColor(c["track"]))

    def apply_theme(self, theme: str) -> None:
        c = palette(theme)
        self.util.set_track(QColor(c["track"]))
        self.vram.set_track(QColor(c["track"]))

    def update_data(self, gpu: dict, index: int) -> None:
        vendor_tag = {"nvidia": "NVIDIA", "amd": "AMD", "apple": "Apple"}.get(gpu.get("vendor", ""), "GPU")
        self.tag.setText(f"{vendor_tag} {index}")
        self.name.setText(gpu.get("name") or "—")

        temperature = gpu.get("temperature", -1)
        power = gpu.get("power", -1)
        if temperature is not None and temperature >= 0:
            text = format_temp(temperature)
            if power is not None and power > 0:
                text += f" · {power:.0f}W"
            self.temp.set_state(text, temp_color(temperature))
        elif power is not None and power > 0:
            self.temp.set_state(f"{power:.0f}W", "#9aa3b2")
        else:
            self.temp.set_state("—", "#9aa3b2")

        util = gpu.get("utilization", -1)
        self.util.update_values(util, format_percent(util))

        used = gpu.get("memory_used", -1)
        total = gpu.get("memory_total", -1)
        percent = gpu.get("memory_percent", -1)
        if total and total > 0:
            label = f"{format_bytes(used)} / {format_bytes(total)}"
        else:
            label = "—"
        self.vram.update_values(percent, label, tooltip=f"显存 {label}")


class Panel(QWidget):
    WIDTH = 356

    def __init__(self, config: dict, on_refresh=None, on_settings=None, on_quit=None) -> None:
        super().__init__(None)
        self.config = config
        self._on_refresh = on_refresh
        self._on_settings = on_settings
        self._on_quit = on_quit
        self._theme = config.get("panel_theme", "dark")
        self._gpu_blocks: list[GPUBlock] = []
        self._last_sample: dict | None = None
        self._last_update = 0.0

        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(self.WIDTH)
        self.setStyleSheet(stylesheet(self._theme))

        self._build()
        self.apply_theme(self._theme)

    # -- construction ----------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(9)

        # header
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = QLabel("GPU Monitor")
        self.title.setObjectName("title")
        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #31c48d; font-size: 10px;")
        head.addWidget(self.title)
        head.addWidget(self.status_dot)
        head.addStretch(1)
        self.updated = QLabel("—")
        self.updated.setObjectName("dim")
        head.addWidget(self.updated)
        outer.addLayout(head)

        self.subtitle = ElidedLabel("—")
        self.subtitle.setObjectName("dim")
        outer.addWidget(self.subtitle)

        outer.addWidget(self._sep())

        # GPU area (rows are created on demand)
        self.gpu_container = QWidget()
        self.gpu_layout = QVBoxLayout(self.gpu_container)
        self.gpu_layout.setContentsMargins(0, 0, 0, 0)
        self.gpu_layout.setSpacing(10)
        outer.addWidget(self.gpu_container)
        self.no_gpu_hint = QLabel("未检测到可监控的 GPU")
        self.no_gpu_hint.setObjectName("dim")
        self.gpu_layout.addWidget(self.no_gpu_hint)

        outer.addWidget(self._sep())

        # CPU
        cpu_head = QHBoxLayout()
        cpu_head.setSpacing(8)
        self.cpu_tag = section_label("CPU")
        self.cpu_name = ElidedLabel("—")
        self.cpu_name.setStyleSheet("font-weight: 600;")
        self.cpu_temp = Badge("—")
        cpu_head.addWidget(self.cpu_tag)
        cpu_head.addWidget(self.cpu_name, 1)
        cpu_head.addWidget(self.cpu_temp)
        outer.addLayout(cpu_head)

        c = palette(self._theme)
        self.cpu_bar = LabeledBar("占用", c["accent"])
        self.cpu_bar.set_track(QColor(c["track"]))
        outer.addWidget(self.cpu_bar)

        self.cpu_detail = QLabel("—")
        self.cpu_detail.setObjectName("dim")
        outer.addWidget(self.cpu_detail)

        self.chart = HistoryChart(42)
        self.chart.set_colors(QColor(c["border"]), c["accent"], c["good"])
        outer.addWidget(self.chart)

        outer.addWidget(self._sep())

        # RAM / disk
        self.ram_bar = LabeledBar("内存", c["warn"], label_width=38)
        self.ram_bar.set_track(QColor(c["track"]))
        self.disk_bar = LabeledBar("磁盘", "#a78bfa", label_width=38)
        self.disk_bar.set_track(QColor(c["track"]))
        outer.addWidget(self.ram_bar)
        outer.addWidget(self.disk_bar)

        # ComfyUI
        self.comfy_box = QWidget()
        comfy_layout = QVBoxLayout(self.comfy_box)
        comfy_layout.setContentsMargins(0, 2, 0, 0)
        comfy_layout.setSpacing(6)

        comfy_head = QHBoxLayout()
        comfy_head.setSpacing(8)
        comfy_head.addWidget(section_label("COMFYUI"))
        self.comfy_dot = QLabel("●")
        self.comfy_dot.setStyleSheet("color: #9aa3b2; font-size: 10px;")
        comfy_head.addWidget(self.comfy_dot)
        self.comfy_url = ElidedLabel("—")
        self.comfy_url.setObjectName("dim")
        comfy_head.addWidget(self.comfy_url, 1)
        comfy_layout.addLayout(comfy_head)

        self.comfy_info = ElidedLabel("—")
        self.comfy_info.setObjectName("dim")
        comfy_layout.addWidget(self.comfy_info)

        self.comfy_progress = UsageBar(c["accent"], 5)
        self.comfy_progress.set_track(QColor(c["track"]))
        self.comfy_progress.setVisible(False)
        comfy_layout.addWidget(self.comfy_progress)

        outer.addWidget(self.comfy_box)
        outer.addWidget(self._sep())

        # footer
        footer = QHBoxLayout()
        footer.setSpacing(6)
        self.refresh_btn = QPushButton("刷新")
        self.settings_btn = QPushButton("设置")
        self.quit_btn = QPushButton("退出")
        self.quit_btn.setObjectName("secondary")
        for btn in (self.refresh_btn, self.settings_btn, self.quit_btn):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            footer.addWidget(btn)
        outer.addLayout(footer)

        self.refresh_btn.clicked.connect(lambda: self._on_refresh and self._on_refresh())
        self.settings_btn.clicked.connect(lambda: self._on_settings and self._on_settings())
        self.quit_btn.clicked.connect(lambda: self._on_quit and self._on_quit())

        # click-away / esc closes
        self.setFocusPolicy(Qt.StrongFocus)

    def _sep(self) -> QFrame:
        line = QFrame()
        line.setObjectName("sep")
        line.setFixedHeight(1)
        line.setFrameShape(QFrame.NoFrame)
        return line

    # -- theming ---------------------------------------------------------
    def apply_theme(self, theme: str) -> None:
        self._theme = theme
        c = palette(theme)
        self.setStyleSheet(stylesheet(theme))
        self.status_dot.setStyleSheet(f"color: {c['good']}; font-size: 10px;")
        self.cpu_bar.set_track(QColor(c["track"]))
        self.ram_bar.set_track(QColor(c["track"]))
        self.disk_bar.set_track(QColor(c["track"]))
        self.comfy_progress.set_track(QColor(c["track"]))
        self.chart.set_colors(QColor(c["border"]), c["accent"], c["good"])
        for block in self._gpu_blocks:
            block.apply_theme(theme)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        c = palette(self._theme)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(c["border"]), 1))
        p.setBrush(QColor(c["bg"]))
        p.drawRoundedRect(rect, 12, 12)
        p.end()

    # -- data ------------------------------------------------------------
    def _ensure_gpu_blocks(self, count: int) -> None:
        while len(self._gpu_blocks) < count:
            block = GPUBlock(self._theme)
            self._gpu_blocks.append(block)
            self.gpu_layout.addWidget(block)
        for index, block in enumerate(self._gpu_blocks):
            block.setVisible(index < count)

    def update_data(self, sample: dict, comfy: dict | None = None, progress: dict | None = None) -> None:
        if not sample:
            return
        self._last_sample = sample
        self._last_update = time.time()

        gpus = sample.get("gpus") or []
        self.no_gpu_hint.setVisible(not gpus)
        self._ensure_gpu_blocks(len(gpus))
        for index, gpu in enumerate(gpus):
            self._gpu_blocks[index].update_data(gpu, index)

        cpu = sample.get("cpu", {})
        self.cpu_name.setText(cpu.get("name") or "—")
        cpu_percent = cpu.get("percent", -1)
        self.cpu_bar.update_values(cpu_percent, format_percent(cpu_percent))

        temp = cpu.get("temp", -1)
        self.cpu_temp.set_state(format_temp(temp), temp_color(temp))

        freq = cpu.get("freq", -1)
        freq_text = f"{freq / 1000:.2f} GHz" if freq and freq > 0 else "—"
        self.cpu_detail.setText(
            f"{cpu.get('cores', 0)} 核 · {cpu.get('threads', 0)} 线程 · {freq_text}"
        )

        gpu_util = max((g.get("utilization", -1) or -1) for g in gpus) if gpus else -1
        self.chart.push(cpu_percent, gpu_util)

        ram = sample.get("ram", {})
        self.ram_bar.update_values(
            ram.get("percent", -1),
            f"{format_bytes(ram.get('used'))} / {format_bytes(ram.get('total'))}",
        )

        disk = sample.get("disk", {})
        path = disk.get("path") or ""
        self.disk_bar.label.setText(path if len(path) <= 6 else path[:5])
        self.disk_bar.setToolTip(path)
        self.disk_bar.update_values(
            disk.get("percent", -1),
            f"{format_bytes(disk.get('used'))} / {format_bytes(disk.get('total'))}",
        )

        self.subtitle.setText(
            f"{sample.get('host', '')} · {sample.get('os', '')} · 已运行 {_uptime(sample.get('uptime', -1))}"
        )
        self.updated.setText("刚刚更新")

        if comfy is not None:
            self.update_comfy(comfy, progress or {})

        self._relayout()

    def update_comfy(self, comfy: dict, progress: dict) -> None:
        c = palette(self._theme)
        enabled = bool(self.config.get("comfy_enabled", True))
        self.comfy_box.setVisible(enabled)
        if not enabled:
            return

        self.comfy_url.setText(self.config.get("comfy_url", "—"))
        ok = bool(comfy.get("ok"))

        if ok:
            self.comfy_dot.setStyleSheet(f"color: {c['good']}; font-size: 10px;")
            parts = [f"运行 {comfy.get('running', 0)}", f"等待 {comfy.get('pending', 0)}"]
            version = comfy.get("version") or ""
            if version:
                parts.append(f"v{version}")
            if comfy.get("device"):
                parts.append(str(comfy["device"]))
            self.comfy_info.setText(" · ".join(parts))
            self.comfy_info.setToolTip(str(comfy.get("device", "")))
        else:
            self.comfy_dot.setStyleSheet(f"color: {c['hot']}; font-size: 10px;")
            error = comfy.get("error") or "未连接"
            self.comfy_info.setText(f"未连接 · {error}")
            self.comfy_info.setToolTip(error)

        maximum = progress.get("max") or 0
        active = bool(progress.get("active")) and maximum > 0 and ok
        self.comfy_progress.setVisible(active)
        if active:
            value = progress.get("value", 0)
            self.comfy_progress.set_value(max(1.0, value / maximum * 100.0))
            node = progress.get("node") or ""
            label = f"步骤 {value}/{maximum}"
            if node:
                label += f" · 节点 {node}"
            self.comfy_info.setText(label)

    def tick_clock(self) -> None:
        if not self._last_update:
            return
        age = time.time() - self._last_update
        if age < 2:
            self.updated.setText("刚刚更新")
        else:
            self.updated.setText(f"{int(age)} 秒前")

    # -- placement -------------------------------------------------------
    def _relayout(self) -> None:
        self.layout().activate()
        self.adjustSize()
        self.setFixedWidth(self.WIDTH)

    def show_near(self, tray_geometry=None) -> None:
        self._relayout()
        screen = None
        anchor = None
        if tray_geometry is not None and not tray_geometry.isNull():
            anchor = tray_geometry
            screen = QGuiApplication.screenAt(anchor.center())
        if screen is None:
            screen = QGuiApplication.screenAt(self.cursor().pos()) or QGuiApplication.primaryScreen()
            anchor = None
        if screen is None:
            self.show()
            return

        available = screen.availableGeometry()
        width, height = self.width(), self.height()

        if anchor is not None:
            x = anchor.center().x() - width // 2
            if anchor.center().y() > available.center().y():
                y = anchor.top() - height - 6
            else:
                y = anchor.bottom() + 6
        else:
            x = available.right() - width - 12
            y = available.bottom() - height - 12

        x = max(available.left() + 6, min(x, available.right() - width - 6))
        y = max(available.top() + 6, min(y, available.bottom() - height - 6))

        self.move(QPoint(x, y))
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.PopupFocusReason)

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        # close when the user clicks elsewhere (Qt.Tool windows do not do it)
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            QTimer.singleShot(120, self._hide_if_inactive)
        super().changeEvent(event)

    def _hide_if_inactive(self) -> None:
        if self.isVisible() and not self.isActiveWindow():
            self.hide()
