"""Windows taskbar widget: live GPU / CPU / RAM text displayed on the taskbar.

A frameless, always-on-top window floats exactly over the taskbar's empty
area (the same approach TrafficMonitor uses on Windows 11, whose XAML taskbar
no longer accepts child windows). The position is re-asserted on every sample
and re-checked periodically, so it survives explorer restarts, DPI changes and
taskbar moves. On non-Windows systems the widget simply stays hidden.
"""

from __future__ import annotations

import ctypes
import platform
from ctypes import wintypes

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from .hardware import format_percent, format_temp
from .widgets import severity_color, temp_color

IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    _user32 = ctypes.windll.user32

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    _GWL_EXSTYLE = -20
    _WS_EX_TOPMOST = 0x00000008
    _WS_EX_NOACTIVATE = 0x08000000
    _HWND_TOPMOST = -1
    _SWP_NOACTIVATE = 0x0010

    # precise signatures avoid ctypes OverflowError on 64-bit values
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
    _user32.FindWindowW.restype = wintypes.HWND
    _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.FindWindowExW.restype = wintypes.HWND
    _user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    if hasattr(_user32, "GetWindowLongPtrW"):
        _user32.GetWindowLongPtrW.restype = ctypes.c_longlong
        _user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        _user32.SetWindowLongPtrW.restype = ctypes.c_longlong
        _user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
    else:
        _user32.GetWindowLongW.restype = ctypes.c_long
        _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        _user32.SetWindowLongW.restype = ctypes.c_long
        _user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]

    def _get_window_long(hwnd: int, index: int) -> int:
        try:
            return int(_user32.GetWindowLongPtrW(hwnd, index)) & 0xFFFFFFFFFFFFFFFF
        except AttributeError:
            return int(_user32.GetWindowLongW(hwnd, index)) & 0xFFFFFFFF

    def _set_window_long(hwnd: int, index: int, value: int) -> None:
        value &= 0xFFFFFFFFFFFFFFFF
        if value >= 0x8000000000000000:
            value -= 0x10000000000000000
        try:
            _user32.SetWindowLongPtrW(hwnd, index, value)
        except AttributeError:
            _user32.SetWindowLongW(hwnd, index, value & 0xFFFFFFFF)


def _find_taskbar() -> tuple[int, _RECT] | None:
    """(hwnd, screen rect) of the primary taskbar, or None."""
    if not IS_WINDOWS:
        return None
    hwnd = _user32.FindWindowW("Shell_TrayWnd", None)
    if not hwnd:
        return None
    rect = _RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    if rect.bottom <= rect.top or rect.right <= rect.left:
        return None
    return hwnd, rect


def _find_tray_notify(taskbar_hwnd: int) -> _RECT | None:
    """Screen rect of the tray notification area (clock etc.)."""
    hwnd = _user32.FindWindowExW(taskbar_hwnd, None, "TrayNotifyWnd", None)
    if not hwnd:
        return None
    rect = _RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect


class TaskbarWidget(QWidget):
    """Compact 'RTX 5060 4% 49°C 显存 26% | CPU 0% | 内存 48%' readout."""

    def __init__(self, config: dict, dark_taskbar: bool = True, on_click=None, on_menu=None) -> None:
        super().__init__(None)
        self.config = config
        self.dark_taskbar = dark_taskbar
        self._on_click = on_click
        self._on_menu = on_menu
        self._sample: dict = {}
        self._items: list[tuple[str, str, str]] = []

        self.setWindowFlags(Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self._font = QFont()
        self._font.setFamily("Microsoft YaHei UI")
        self._font.setPixelSize(13)

        # re-assert placement periodically (explorer restarts, taskbar moves)
        self._watch = QTimer(self)
        self._watch.setInterval(3000)
        self._watch.timeout.connect(self._ensure_placement)
        self._watch.start()

    # -- public API ------------------------------------------------------
    def set_enabled(self, enabled: bool) -> None:
        if enabled and IS_WINDOWS:
            self.show()
            self._apply_noactivate()
            self._ensure_placement(force=True)
        else:
            self.hide()

    def update_sample(self, sample: dict) -> None:
        if not IS_WINDOWS or not sample or sample.get("error"):
            return
        self._sample = sample
        self._rebuild_items()
        self._apply_geometry()
        self._ensure_placement()
        self.update()

    def cleanup(self) -> None:
        self._watch.stop()
        self.close()

    # -- items -----------------------------------------------------------
    def _rebuild_items(self) -> None:
        sample = self._sample
        items: list[tuple[str, str, str]] = []

        gpus = sample.get("gpus") or []
        if gpus:
            gpu = gpus[0]
            util = gpu.get("utilization", -1)
            name = gpu.get("name") or "GPU"
            short = name.replace("NVIDIA GeForce", "").strip() or "GPU"
            if len(short) > 22:
                short = short[:21] + "…"
            items.append((short, format_percent(util), severity_color(util)))
            temperature = gpu.get("temperature", -1)
            if temperature is not None and temperature >= 0:
                items.append(("", format_temp(temperature), temp_color(temperature)))
            memory = gpu.get("memory_percent", -1)
            if memory is not None and memory >= 0:
                items.append(("显存", format_percent(memory), severity_color(memory)))

        cpu = sample.get("cpu", {}) or {}
        items.append(("CPU", format_percent(cpu.get("percent", -1)), severity_color(cpu.get("percent", -1))))

        ram = sample.get("ram", {}) or {}
        items.append(("内存", format_percent(ram.get("percent", -1)), severity_color(ram.get("percent", -1))))

        self._items = items

    # -- geometry --------------------------------------------------------
    def _dpr(self) -> float:
        screen = self.screen() or QGuiApplication.primaryScreen()
        try:
            return float(screen.devicePixelRatio())
        except Exception:
            return 1.0

    def _logical_height(self, physical_height: int) -> int:
        return max(24, min(60, int(physical_height / self._dpr()) - 2))

    def _apply_noactivate(self) -> None:
        """Never steal focus from the user's current app."""
        try:
            hwnd = int(self.winId())
            ex = _get_window_long(hwnd, _GWL_EXSTYLE)
            _set_window_long(hwnd, _GWL_EXSTYLE, ex | _WS_EX_TOPMOST | _WS_EX_NOACTIVATE)
        except Exception:
            pass

    def _ensure_placement(self, force: bool = False) -> None:
        if not self.isVisible() or not IS_WINDOWS:
            return
        if _find_taskbar() is None:
            self.hide()  # explorer is restarting; next tick will bring it back
            QTimer.singleShot(1500, self._revive)
            return
        if force:
            self._apply_noactivate()
        self._position()

    def _revive(self) -> None:
        if not self.isVisible():
            self.show()
            self._ensure_placement(force=True)

    def _position(self) -> None:
        info = _find_taskbar()
        if info is None:
            return
        _hwnd, rect = info
        dpr = self._dpr()

        physical_width = max(120, int(self.width() * dpr))
        physical_height = max(24, int(self.height() * dpr))

        notify = _find_tray_notify(_hwnd)
        if notify is not None:
            right_edge = notify.left - 6
        else:
            right_edge = rect.right - int(280 * dpr)

        left = max(rect.left + 8, right_edge - physical_width)
        top = rect.top + max(0, ((rect.bottom - rect.top) - physical_height) // 2)

        try:
            _user32.SetWindowPos(
                int(self.winId()),
                _HWND_TOPMOST,
                left,
                top,
                physical_width,
                physical_height,
                _SWP_NOACTIVATE,
            )
        except Exception:
            pass
        self.resize(int(physical_width / dpr), int(physical_height / dpr))

    def _apply_geometry(self) -> None:
        """Resize the widget to fit the current text."""
        self._font.setPixelSize(13)
        metrics = self.fontMetrics()
        width = 8
        for index, (label, value, _color) in enumerate(self._items):
            if index:
                width += 14  # gap between groups
            if label:
                width += 10 + metrics.horizontalAdvance(label) + 4
            width += metrics.horizontalAdvance(value)
        width += 8

        info = _find_taskbar()
        height_logical = self._logical_height(info[1].bottom - info[1].top) if info else 30
        self.resize(max(120, width), height_logical)

    # -- painting --------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._items:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        p.setFont(self._font)

        dim = QColor("#9aa3b2") if self.dark_taskbar else QColor("#5c6470")

        metrics = self.fontMetrics()
        baseline = (self.height() + metrics.ascent() - metrics.descent()) // 2
        x = 4.0
        dot_r = 2.5

        for index, (label, value, color) in enumerate(self._items):
            if index:
                x += 14  # gap between groups

            if label:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(color))
                dot_y = baseline - metrics.ascent() / 2 - dot_r
                p.drawEllipse(QRectF(x, dot_y, dot_r * 2, dot_r * 2))
                x += dot_r * 2 + 4
                p.setPen(dim)
                p.drawText(int(x), baseline, label)
                x += metrics.horizontalAdvance(label) + 6

            p.setPen(QColor(color))
            p.drawText(int(x), baseline, value)
            x += metrics.horizontalAdvance(value)

        p.end()

    # -- interaction -----------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._on_click:
            self._on_click()
        elif event.button() == Qt.RightButton and self._on_menu:
            self._on_menu()
        super().mousePressEvent(event)
