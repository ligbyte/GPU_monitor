"""Palette + stylesheet for the tray popup, in dark and light variants."""

from __future__ import annotations

THEMES = {
    "dark": {
        "bg": "#15181d",
        "bg_alt": "#1c2027",
        "border": "#2b313b",
        "text": "#eef1f6",
        "text_dim": "#9aa3b2",
        "track": "#262c35",
        "hover": "#252b34",
        "accent": "#4f9cf9",
        "good": "#31c48d",
        "warn": "#f0b429",
        "hot": "#ef5350",
    },
    "light": {
        "bg": "#fbfcfe",
        "bg_alt": "#f1f3f8",
        "border": "#dde2ec",
        "text": "#1b2028",
        "text_dim": "#69717f",
        "track": "#e4e8f0",
        "hover": "#e9edf5",
        "accent": "#2f7ae5",
        "good": "#1f9d6b",
        "warn": "#c98a06",
        "hot": "#d6362f",
    },
}


def palette(theme: str) -> dict:
    return THEMES.get(theme, THEMES["dark"])


def stylesheet(theme: str) -> str:
    c = palette(theme)
    return f"""
    QWidget {{
        color: {c['text']};
        font-family: "Segoe UI", "SF Pro Text", "Ubuntu", "Noto Sans", "PingFang SC", "Microsoft YaHei", sans-serif;
        font-size: 12px;
    }}
    QLabel#title {{ font-size: 13px; font-weight: 600; }}
    QLabel#dim {{ color: {c['text_dim']}; }}
    QLabel#section {{ font-size: 11px; font-weight: 600; color: {c['text_dim']}; letter-spacing: 0.4px; }}
    QLabel#value {{ font-size: 11px; color: {c['text_dim']}; }}
    QFrame#sep {{ background: {c['border']}; max-height: 1px; border: none; }}
    QPushButton {{
        background: {c['bg_alt']};
        border: 1px solid {c['border']};
        border-radius: 7px;
        padding: 5px 10px;
        color: {c['text']};
    }}
    QPushButton:hover {{ background: {c['hover']}; }}
    QPushButton:pressed {{ background: {c['track']}; }}
    QPushButton#primary {{
        background: {c['accent']};
        border: 1px solid {c['accent']};
        color: #ffffff;
        font-weight: 600;
    }}
    QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
        background: {c['bg_alt']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        padding: 4px 6px;
        min-height: 18px;
    }}
    QComboBox QAbstractItemView {{
        background: {c['bg_alt']};
        border: 1px solid {c['border']};
        selection-background-color: {c['accent']};
        color: {c['text']};
    }}
    QCheckBox {{ spacing: 6px; }}
    QToolTip {{
        background: {c['bg_alt']};
        color: {c['text']};
        border: 1px solid {c['border']};
        padding: 4px;
    }}
    """
