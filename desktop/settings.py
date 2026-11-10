"""Settings dialog (theme aware, no external UI toolkit)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from . import autostart, config as config_module
from .taskbar import IS_WINDOWS
from .theme import stylesheet

ICON_STYLES = [
    ("环形仪表", "ring"),
    ("三色条", "bars"),
    ("纯数字", "text"),
]

ICON_METRICS = [
    ("自动（优先 GPU）", "auto"),
    ("GPU 使用率", "gpu"),
    ("CPU 使用率", "cpu"),
    ("内存占用", "ram"),
]

THEMES = [("深色", "dark"), ("浅色", "light")]


class SettingsDialog(QDialog):
    def __init__(self, config: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = dict(config)
        self.setWindowTitle("GPU Monitor 设置")
        self.setModal(True)
        self.setMinimumWidth(380)
        self.setStyleSheet(stylesheet(self.config.get("panel_theme", "dark")))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setSpacing(9)

        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.5, 10.0)
        self.interval.setSingleStep(0.5)
        self.interval.setSuffix(" 秒")
        self.interval.setValue(float(self.config.get("interval", 1.0)))
        form.addRow("刷新间隔", self.interval)

        self.style = QComboBox()
        for label, value in ICON_STYLES:
            self.style.addItem(label, value)
        self._select(self.style, self.config.get("icon_style", "ring"))
        form.addRow("托盘图标", self.style)

        self.metric = QComboBox()
        for label, value in ICON_METRICS:
            self.metric.addItem(label, value)
        self._select(self.metric, self.config.get("icon_metric", "auto"))
        form.addRow("图标数值", self.metric)

        self.theme = QComboBox()
        for label, value in THEMES:
            self.theme.addItem(label, value)
        self._select(self.theme, self.config.get("panel_theme", "dark"))
        form.addRow("面板主题", self.theme)

        self.disk = QLineEdit(self.config.get("disk_path") or "")
        self.disk.setPlaceholderText("留空则自动选择系统盘")
        form.addRow("磁盘路径", self.disk)

        layout.addLayout(form)

        layout.addWidget(self._divider())
        layout.addWidget(self._hint("ComfyUI（可选）"))

        comfy_form = QFormLayout()
        comfy_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        comfy_form.setSpacing(9)

        self.comfy_enabled = QCheckBox("显示 ComfyUI 状态与实时进度")
        self.comfy_enabled.setChecked(bool(self.config.get("comfy_enabled", True)))
        comfy_form.addRow("", self.comfy_enabled)

        self.comfy_url = QLineEdit(self.config.get("comfy_url", "http://127.0.0.1:8188"))
        self.comfy_url.setPlaceholderText("http://127.0.0.1:8188")
        comfy_form.addRow("服务地址", self.comfy_url)
        layout.addLayout(comfy_form)

        layout.addWidget(self._divider())

        if IS_WINDOWS:
            self.taskbar_widget = QCheckBox("在任务栏空白处显示 GPU / CPU / 内存信息")
            self.taskbar_widget.setChecked(bool(self.config.get("taskbar_widget", True)))
            layout.addWidget(self.taskbar_widget)

        self.autostart = QCheckBox("开机自动启动（登录时最小化到托盘）")
        self.autostart.setChecked(autostart.is_installed())
        layout.addWidget(self.autostart)

        self.autostart_hint = QLabel(autostart.describe_target())
        self.autostart_hint.setObjectName("dim")
        self.autostart_hint.setWordWrap(True)
        layout.addWidget(self.autostart_hint)

        layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _select(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _divider(self) -> QWidget:
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background: palette(mid);")
        return line

    def _hint(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("section")
        return label

    # -- actions ---------------------------------------------------------
    def _save(self) -> None:
        interval = float(self.interval.value())
        updates = {
            "interval": interval,
            "icon_style": self.style.currentData(),
            "icon_metric": self.metric.currentData(),
            "panel_theme": self.theme.currentData(),
            "disk_path": self.disk.text().strip(),
            "comfy_enabled": self.comfy_enabled.isChecked(),
            "comfy_url": self.comfy_url.text().strip() or "http://127.0.0.1:8188",
        }
        if IS_WINDOWS:
            updates["taskbar_widget"] = self.taskbar_widget.isChecked()
        self.config.update(updates)
        config_module.save(self.config)

        if self.autostart.isChecked():
            autostart.install()
        else:
            autostart.uninstall()

        self.accept()
