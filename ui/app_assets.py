"""定位并应用源码环境和打包环境中的程序资源。"""

from __future__ import annotations

import ctypes
import os
import sys
import tkinter as tk
from contextlib import suppress
from pathlib import Path


APP_USER_MODEL_ID = "NestedExtractionAssistant.Desktop"
ASSET_DIRECTORY = "assets"
APP_ICON_NAME = "app.ico"
APP_LOGO_NAME = "app-logo.png"


def resource_path(*parts: str) -> Path:
    """返回源码目录或 PyInstaller 解包目录中的资源路径。"""
    bundle_root = getattr(sys, "_MEIPASS", None)
    root = Path(bundle_root) if bundle_root else Path(__file__).resolve().parent.parent
    return root.joinpath(*parts)


def configure_windows_app_identity() -> None:
    """设置稳定的 Windows 应用标识，确保任务栏使用正确图标。"""
    if os.name != "nt":
        return
    with suppress(AttributeError, OSError):
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)


def apply_window_icon(window: tk.Tk | tk.Toplevel) -> Path | None:
    """应用多尺寸 ICO，并在成功时返回图标路径。"""
    icon_path = resource_path(ASSET_DIRECTORY, APP_ICON_NAME)
    if not icon_path.is_file():
        return None
    try:
        window.iconbitmap(default=str(icon_path))
    except tk.TclError:
        return None
    return icon_path
