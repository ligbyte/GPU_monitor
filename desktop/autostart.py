"""Launch-at-login helpers for Windows, macOS and Linux.

All three implementations simply start ``run.py`` with the current interpreter
(or ``pythonw.exe`` on Windows so no console window flashes up).
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "GPU Monitor"
APP_ID = "gpu-monitor"
BUNDLE_ID = "com.local.gpumonitor"

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def launcher_script() -> Path:
    return project_root() / "main.py"


def python_executable() -> list[str]:
    """Interpreter used by the autostart entry."""
    exe = Path(sys.executable)
    if IS_WINDOWS:
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.exists():
            return [str(pythonw)]
    return [str(exe)]


def command() -> list[str]:
    return python_executable() + [str(launcher_script())]


# ---------------------------------------------------------------------------
# per-platform paths
# ---------------------------------------------------------------------------
def _windows_startup_dir() -> Path:
    appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _windows_shortcut() -> Path:
    return _windows_startup_dir() / f"{APP_ID}.vbs"


def _macos_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"


def _linux_desktop() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(config_home) / "autostart" / f"{APP_ID}.desktop"


def target_path() -> Path | None:
    if IS_WINDOWS:
        return _windows_shortcut()
    if IS_MACOS:
        return _macos_plist()
    if IS_LINUX:
        return _linux_desktop()
    return None


def describe_target() -> str:
    path = target_path()
    if path is None:
        return "当前系统不支持开机自启设置。"
    return f"自启位置：{path}"


def is_installed() -> bool:
    path = target_path()
    return bool(path and path.exists())


# ---------------------------------------------------------------------------
# install / uninstall
# ---------------------------------------------------------------------------
def install() -> bool:
    try:
        if IS_WINDOWS:
            return _install_windows()
        if IS_MACOS:
            return _install_macos()
        if IS_LINUX:
            return _install_linux()
    except Exception:
        return False
    return False


def uninstall() -> bool:
    path = target_path()
    removed = False
    try:
        if path and path.exists():
            path.unlink()
            removed = True
        if IS_MACOS:
            _launchctl(["unload", str(path)]) if path else None
        if IS_LINUX:
            _refresh_linux_desktop_cache()
    except Exception:
        return False
    return removed or not is_installed()


def _install_windows() -> bool:
    startup = _windows_startup_dir()
    startup.mkdir(parents=True, exist_ok=True)
    exe, script = command()
    content = (
        'Set shell = CreateObject("WScript.Shell")\n'
        f'shell.Run """{exe}"" ""{script}""", 0, False\n'
    )
    _windows_shortcut().write_text(content, encoding="utf-8")
    _create_start_menu_shortcut()
    return _windows_shortcut().exists()


def _create_start_menu_shortcut() -> None:
    """Add a Start-menu entry so the app is easy to find/pin manually."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return
    programs = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    link = programs / f"{APP_NAME}.lnk"
    if link.exists():
        return
    icon = project_root() / "desktop" / "assets" / "icon.ico"
    exe, script = command()
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{link}'); "
        f"$s.TargetPath = '{exe}'; "
        f"$s.Arguments = '\"{script}\"'; "
        f"$s.WorkingDirectory = '{project_root()}'; "
        f"$s.IconLocation = '{icon}'; "
        "$s.Save()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except Exception:
        pass


def _install_macos() -> bool:
    plist_path = _macos_plist()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    exe, script = command()
    log_dir = Path.home() / "Library" / "Logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{APP_ID}.log"

    args = "".join(
        f"        <string>{value}</string>\n" for value in (exe, script)
    )
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{BUNDLE_ID}</string>
    <key>ProgramArguments</key>
    <array>
{args}    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>StandardOutPath</key>
    <string>{log_file}</string>
    <key>StandardErrorPath</key>
    <string>{log_file}</string>
</dict>
</plist>
"""
    plist_path.write_text(plist, encoding="utf-8")
    _launchctl(["unload", str(plist_path)])
    _launchctl(["load", "-w", str(plist_path)])
    return plist_path.exists()


def _install_linux() -> bool:
    desktop = _linux_desktop()
    desktop.parent.mkdir(parents=True, exist_ok=True)
    exe, script = command()
    icon = project_root() / "desktop" / "assets" / "icon.png"
    entry = f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
Comment=GPU / CPU / memory monitor in the system tray
Exec={shlex_quote(exe)} {shlex_quote(script)}
Path={project_root()}
Icon={icon}
Terminal=false
X-GNOME-Autostart-enabled=true
StartupNotify=false
"""
    desktop.write_text(entry, encoding="utf-8")
    desktop.chmod(0o755)
    _refresh_linux_desktop_cache()
    return desktop.exists()


def shlex_quote(value: str) -> str:
    if " " in value or "'" in value:
        escaped = value.replace("'", "'\\''")
        return f"'{escaped}'"
    return value


def _refresh_linux_desktop_cache() -> None:
    if shutil.which("update-desktop-database"):
        try:
            subprocess.run(
                ["update-desktop-database", str(Path.home() / ".local/share/applications")],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except Exception:
            pass


def _launchctl(args: list[str]) -> None:
    if not shutil.which("launchctl"):
        return
    try:
        subprocess.run(["launchctl", *args], capture_output=True, timeout=15, check=False)
    except Exception:
        pass
