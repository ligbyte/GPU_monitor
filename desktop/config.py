"""Small JSON config stored in the user's home directory."""

from __future__ import annotations

import json
import os
from pathlib import Path

APP_DIR_NAME = ".gpu-monitor"
CONFIG_FILE = "config.json"

DEFAULTS: dict = {
    # sampling
    "interval": 1.0,              # seconds between samples
    # tray icon
    "icon_style": "ring",         # ring | bars | text
    "icon_metric": "auto",        # auto | gpu | cpu | ram
    "panel_theme": "dark",        # dark | light
    "show_temperature": True,
    "taskbar_widget": True,       # embed text readout into the Windows taskbar
    # ComfyUI link (optional)
    "comfy_enabled": True,
    "comfy_url": "http://127.0.0.1:8188",
    # disk
    "disk_path": "",
    # misc
    "launch_at_login": False,
    "start_minimized": True,
}


def config_dir() -> Path:
    return Path.home() / APP_DIR_NAME


def config_path() -> Path:
    return config_dir() / CONFIG_FILE


def load() -> dict:
    data = dict(DEFAULTS)
    path = config_path()
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                for key, value in stored.items():
                    if key in DEFAULTS:
                        data[key] = value
    except Exception:
        pass
    return data


def save(data: dict) -> None:
    try:
        config_dir().mkdir(parents=True, exist_ok=True)
        tmp = config_path().with_suffix(".tmp")
        payload = {key: data.get(key, DEFAULTS.get(key)) for key in DEFAULTS}
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, config_path())
    except Exception:
        pass
