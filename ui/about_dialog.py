"""项目名称、版本和项目地址说明窗口。"""

from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import ttk

from ui.app_assets import APP_LOGO_NAME, ASSET_DIRECTORY, apply_window_icon, resource_path


APP_NAME = "嵌套解压助手"
APP_VERSION = "0.2.0"
PROJECT_URL = "https://github.com/N0924/nested-extraction-assistant"


def open_project_page() -> None:
    """使用系统默认浏览器打开项目主页。"""
    webbrowser.open_new_tab(PROJECT_URL)


class AboutDialog:
    def __init__(self, parent: tk.Misc) -> None:
        self.window = tk.Toplevel(parent)
        self.window.title("关于")
        self.window.resizable(False, False)
        self.window.transient(parent)
        apply_window_icon(self.window)

        content = ttk.Frame(self.window, padding=20)
        content.grid(row=0, column=0, sticky="nsew")

        intro = ttk.Frame(content)
        intro.grid(row=0, column=0, sticky="ew")
        intro.columnconfigure(1, weight=1)

        self.logo_image: tk.PhotoImage | None = None
        logo_path = resource_path(ASSET_DIRECTORY, APP_LOGO_NAME)
        if logo_path.is_file():
            try:
                self.logo_image = tk.PhotoImage(file=str(logo_path))
            except tk.TclError:
                self.logo_image = None

        text_column = 0
        if self.logo_image is not None:
            ttk.Label(intro, image=self.logo_image).grid(
                row=0,
                column=0,
                rowspan=4,
                padx=(0, 18),
                sticky="n",
            )
            text_column = 1

        ttk.Label(intro, text=APP_NAME, font=("Microsoft YaHei UI", 16, "bold")).grid(
            row=0, column=text_column, sticky="w"
        )
        ttk.Label(intro, text=f"版本 {APP_VERSION}").grid(
            row=1, column=text_column, sticky="w", pady=(3, 6)
        )
        project_link = ttk.Label(
            intro,
            text=f"GitHub：{PROJECT_URL}",
            foreground="#0563c1",
            cursor="hand2",
            wraplength=360,
            justify="left",
        )
        project_link.grid(row=2, column=text_column, sticky="w", pady=(0, 12))
        project_link.bind("<Button-1>", lambda _event: open_project_page())
        ttk.Label(
            intro,
            text="Windows 本地嵌套压缩包处理工具，源文件始终只读。",
            wraplength=300,
            justify="left",
        ).grid(row=3, column=text_column, sticky="w")

        ttk.Button(content, text="关闭", command=self.window.destroy).grid(
            row=1, column=0, sticky="e", pady=(18, 0)
        )
        self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)
        self.window.grab_set()
        self.window.focus_set()
