"""Tray application wiring: one background sampler thread + one tray icon."""

from __future__ import annotations

import platform
import sys
import threading

from PySide6.QtCore import QLockFile, QObject, QStandardPaths, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QCursor, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import autostart, config as config_module
from .comfy import ComfyClient
from .hardware import HardwareSampler, format_bytes, format_percent, format_temp
from .icon import app_icon, make_icon
from .panel import Panel
from .settings import SettingsDialog
from .taskbar import IS_WINDOWS, TaskbarWidget


def system_prefers_dark() -> bool:
    """Best-effort detection of the OS theme so the icon stays readable."""
    try:
        if platform.system() == "Windows":
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            value, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
            return int(value) == 0
        if platform.system() == "Darwin":
            import subprocess

            out = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            return "dark" in (out.stdout or "").lower()
        if platform.system() == "Linux":
            import subprocess

            out = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            return "dark" in (out.stdout or "").lower()
    except Exception:
        pass
    return True


class SamplerThread(QThread):
    sampled = Signal(dict, dict, dict)

    def __init__(self, sampler: HardwareSampler, comfy: ComfyClient, interval: float) -> None:
        super().__init__()
        self.sampler = sampler
        self.comfy = comfy
        self.interval = max(0.5, float(interval))
        self.comfy_enabled = True
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:  # noqa: D401 - QThread entry point
        # tiny warm-up so the first CPU percentage is meaningful
        try:
            self.sampler.sample()
        except Exception:
            pass
        while not self._stop.is_set():
            try:
                sample = self.sampler.sample()
            except Exception as exc:  # never let the thread die
                sample = {"error": str(exc), "gpus": [], "cpu": {}, "ram": {}, "disk": {}}
            comfy_state: dict = {}
            progress: dict = {}
            if self.comfy_enabled and self.comfy.enabled:
                try:
                    comfy_state = self.comfy.poll()
                    progress = self.comfy.progress()
                except Exception:
                    comfy_state = {"ok": False, "error": "连接失败"}
            try:
                self.sampled.emit(sample, comfy_state, progress)
            except Exception:
                pass
            self._stop.wait(self.interval)


class TrayApp(QObject):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.config = config_module.load()
        self.sampler = HardwareSampler(self.config.get("disk_path") or None)
        self.comfy = ComfyClient(self.config.get("comfy_url", "") if self.config.get("comfy_enabled") else "")
        self.dark_tray = system_prefers_dark()
        self._icon_cache: dict = {}
        self._last_sample: dict = {}

        self.panel = Panel(
            self.config,
            on_refresh=self.refresh_now,
            on_settings=self.open_settings,
            on_quit=self.quit,
        )

        self.taskbar: TaskbarWidget | None = None
        if IS_WINDOWS:
            self.taskbar = TaskbarWidget(
                self.config,
                dark_taskbar=self.dark_tray,
                on_click=self.toggle_panel,
                on_menu=self._show_menu,
            )
            self.taskbar.set_enabled(bool(self.config.get("taskbar_widget", True)))

        self.tray = QSystemTrayIcon(self._icon({}, force=True))
        self.tray.setToolTip("GPU Monitor")
        self._build_menu()
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

        self.worker = SamplerThread(self.sampler, self.comfy, self.config.get("interval", 1.0))
        self.worker.comfy_enabled = bool(self.config.get("comfy_enabled", True))
        self.worker.sampled.connect(self._on_sample)
        self.worker.start()

        self.clock = QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self._on_clock)
        self.clock.start()

    # -- menu ------------------------------------------------------------
    def _build_menu(self) -> None:
        menu = QMenu()
        self.show_action = QAction("显示面板", menu)
        self.show_action.triggered.connect(self.toggle_panel)

        self.refresh_action = QAction("立即刷新", menu)
        self.refresh_action.triggered.connect(self.refresh_now)

        self.autostart_action = QAction("开机自动启动", menu)
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(autostart.is_installed())
        self.autostart_action.toggled.connect(self._toggle_autostart)

        self.settings_action = QAction("设置…", menu)
        self.settings_action.triggered.connect(self.open_settings)

        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.quit)

        menu.addAction(self.show_action)
        menu.addAction(self.refresh_action)
        if IS_WINDOWS:
            self.taskbar_action = QAction("在任务栏显示监控信息", menu)
            self.taskbar_action.setCheckable(True)
            self.taskbar_action.setChecked(bool(self.config.get("taskbar_widget", True)))
            self.taskbar_action.toggled.connect(self._toggle_taskbar)
            menu.addAction(self.taskbar_action)
        menu.addSeparator()
        menu.addAction(self.autostart_action)
        menu.addAction(self.settings_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        self.menu = menu
        self.tray.setContextMenu(menu)

    def _show_menu(self) -> None:
        if self.menu is not None:
            self.menu.exec(QCursor.pos())

    def _toggle_taskbar(self, checked: bool) -> None:
        if self.taskbar is None:
            return
        self.config["taskbar_widget"] = bool(checked)
        config_module.save(self.config)
        self.taskbar.set_enabled(checked)

    # -- icon ------------------------------------------------------------
    def _icon(self, sample: dict, force: bool = False) -> QIcon:
        style = self.config.get("icon_style", "ring")
        metric = self.config.get("icon_metric", "auto")
        if force or not sample:
            key = (style, metric, -2)
        else:
            gpus = sample.get("gpus") or []
            gpu = max((g.get("utilization", -1) or -1) for g in gpus) if gpus else -1
            cpu = sample.get("cpu", {}).get("percent", -1) or -1
            ram = sample.get("ram", {}).get("percent", -1) or -1
            if metric == "gpu":
                value = gpu
            elif metric == "cpu":
                value = cpu
            elif metric == "ram":
                value = ram
            else:
                value = gpu if gpu >= 0 else cpu
            key = (style, metric, int(round(value)))

        cached = self._icon_cache.get(key)
        if cached is not None:
            return cached

        icon = make_icon(style, sample, metric, self.dark_tray)
        if len(self._icon_cache) > 160:
            self._icon_cache.clear()
        self._icon_cache[key] = icon
        return icon

    def _update_icon(self, sample: dict) -> None:
        self.tray.setIcon(self._icon(sample))
        self.tray.setToolTip(self._tooltip(sample))

    @staticmethod
    def _tooltip(sample: dict) -> str:
        if not sample:
            return "GPU Monitor"
        lines = []
        for gpu in sample.get("gpus") or []:
            parts = [gpu.get("name", "GPU")]
            if gpu.get("utilization", -1) >= 0:
                parts.append(format_percent(gpu.get("utilization")))
            if gpu.get("temperature", -1) >= 0:
                parts.append(format_temp(gpu.get("temperature")))
            if gpu.get("memory_percent", -1) >= 0:
                parts.append(f"显存 {format_percent(gpu.get('memory_percent'))}")
            lines.append(" · ".join(parts))
        cpu = sample.get("cpu", {})
        if cpu:
            lines.append(f"CPU {format_percent(cpu.get('percent'))}")
        ram = sample.get("ram", {})
        if ram:
            lines.append(
                f"内存 {format_percent(ram.get('percent'))} "
                f"({format_bytes(ram.get('used'))}/{format_bytes(ram.get('total'))})"
            )
        return "\n".join(lines) or "GPU Monitor"

    # -- events ----------------------------------------------------------
    def _on_sample(self, sample: dict, comfy: dict, progress: dict) -> None:
        if sample.get("error"):
            return
        self._last_sample = sample
        self._update_icon(sample)
        self.panel.update_data(sample, comfy, progress)
        if self.taskbar is not None and self.taskbar.isVisible():
            self.taskbar.update_sample(sample)

    def _on_clock(self) -> None:
        if self.panel.isVisible():
            self.panel.tick_clock()

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.toggle_panel()

    def toggle_panel(self) -> None:
        if self.panel.isVisible():
            self.panel.hide()
            return
        geometry = None
        try:
            geometry = self.tray.geometry()
        except Exception:
            geometry = None
        self.panel.show_near(geometry)

    def refresh_now(self) -> None:
        try:
            sample = self.sampler.sample()
            comfy_state = self.comfy.poll() if self.worker.comfy_enabled else {}
            progress = self.comfy.progress() if comfy_state else {}
        except Exception:
            return
        self._on_sample(sample, comfy_state, progress)

    def _toggle_autostart(self, checked: bool) -> None:
        ok = autostart.install() if checked else autostart.uninstall()
        if not ok:
            self.autostart_action.blockSignals(True)
            self.autostart_action.setChecked(not checked)
            self.autostart_action.blockSignals(False)

    def open_settings(self) -> None:
        self.panel.hide()
        dialog = SettingsDialog(self.config)
        if dialog.exec() == SettingsDialog.Accepted:
            self.apply_config(dialog.config)

    def apply_config(self, new_config: dict) -> None:
        self.config = new_config
        config_module.save(self.config)

        self.panel.config = self.config
        self.panel.apply_theme(self.config.get("panel_theme", "dark"))

        if self.taskbar is not None:
            self.taskbar.set_enabled(bool(self.config.get("taskbar_widget", True)))
            self.taskbar.dark_taskbar = self.dark_tray

        self.worker.interval = max(0.5, float(self.config.get("interval", 1.0)))
        self.worker.comfy_enabled = bool(self.config.get("comfy_enabled", True))
        if self.worker.comfy_enabled:
            self.comfy.configure(self.config.get("comfy_url", ""))
        else:
            self.comfy.stop_ws()

        disk_path = self.config.get("disk_path") or None
        if disk_path != self.sampler.disk_path:
            self.sampler.disk_path = disk_path or self.sampler.disk_path

        self._icon_cache.clear()
        if self._last_sample:
            self._update_icon(self._last_sample)
        self.refresh_now()

    # -- lifecycle -------------------------------------------------------
    def quit(self) -> None:
        try:
            self.worker.stop()
            self.worker.wait(1500)
        except Exception:
            pass
        try:
            self.sampler.close()
            self.comfy.stop_ws()
        except Exception:
            pass
        if self.taskbar is not None:
            self.taskbar.cleanup()
        self.tray.hide()
        self.app.quit()


def _acquire_lock(app: QApplication) -> QLockFile | None:
    location = QStandardPaths.writableLocation(QStandardPaths.TempLocation) or "."
    lock = QLockFile(str(__import__("pathlib").Path(location) / "gpu-monitor.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        return None
    return lock


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("GPU Monitor")
    app.setApplicationDisplayName("GPU Monitor")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(app_icon(128)))

    lock = _acquire_lock(app)
    if lock is None:
        QMessageBox.information(None, "GPU Monitor", "监控已经在运行中（请看系统托盘）。")
        return 0

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.warning(
            None,
            "GPU Monitor",
            "当前桌面环境没有可用的系统托盘。\n"
            "Linux (GNOME) 需要安装 AppIndicator 扩展后重新启动。",
        )
        return 1

    tray_app = TrayApp(app)
    app.aboutToQuit.connect(lambda: tray_app.worker.stop())
    code = app.exec()
    lock.unlock()
    return code
