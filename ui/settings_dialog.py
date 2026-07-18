"""任务路径、资源上限和并发设置窗口。"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from task_queue import AppSettings


class SettingsDialog:
    def __init__(
        self,
        parent: tk.Misc,
        settings: AppSettings,
        on_save: Callable[[AppSettings], None],
    ) -> None:
        self.parent = parent
        self.on_save = on_save
        self.window = tk.Toplevel(parent)
        self.window.title("设置")
        self.window.resizable(False, False)
        self.window.transient(parent)

        self.work_root_var = tk.StringVar(value=settings.work_root)
        self.output_root_var = tk.StringVar(value=settings.output_root)
        self.winrar_var = tk.StringVar(value=settings.winrar_path)
        self.max_files_var = tk.StringVar(
            value=str(settings.max_files) if settings.max_files else ""
        )
        self.max_total_gb_var = tk.StringVar(
            value=str(settings.max_total_gb) if settings.max_total_gb else ""
        )
        self.max_archives_var = tk.StringVar(
            value=str(settings.max_archives) if settings.max_archives else ""
        )
        self.unlimited_files_var = tk.BooleanVar(value=settings.max_files == 0)
        self.unlimited_total_var = tk.BooleanVar(value=settings.max_total_gb == 0)
        self.unlimited_archives_var = tk.BooleanVar(value=settings.max_archives == 0)
        self.max_concurrency_var = tk.StringVar(value=str(settings.max_concurrency))

        self._build()
        self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)
        self.window.grab_set()
        self.window.focus_set()

    def _build(self) -> None:
        content = ttk.Frame(self.window, padding=18)
        content.grid(row=0, column=0, sticky="nsew")
        content.columnconfigure(1, weight=1)

        self._add_directory_row(content, 0, "工作目录", self.work_root_var, "选择工作目录")
        self._add_directory_row(content, 1, "输出目录", self.output_root_var, "选择输出目录")
        self._add_winrar_row(content, 2)

        ttk.Separator(content).grid(row=3, column=0, columnspan=3, sticky="ew", pady=12)

        self.max_files_entry = self._add_limit_row(
            content,
            4,
            "最大文件数",
            self.max_files_var,
            self.unlimited_files_var,
            "个",
        )
        self.max_total_entry = self._add_limit_row(
            content,
            5,
            "最大累计容量",
            self.max_total_gb_var,
            self.unlimited_total_var,
            "GB",
        )
        self.max_archives_entry = self._add_limit_row(
            content,
            6,
            "最大压缩包数",
            self.max_archives_var,
            self.unlimited_archives_var,
            "个",
        )

        ttk.Label(content, text="同时运行任务").grid(
            row=7, column=0, sticky="w", padx=(0, 12), pady=5
        )
        ttk.Combobox(
            content,
            textvariable=self.max_concurrency_var,
            values=("1", "2", "3", "4", "5"),
            state="readonly",
            width=13,
        ).grid(row=7, column=1, sticky="w", pady=5)
        ttk.Label(content, text="个").grid(row=7, column=2, sticky="w", padx=(8, 0), pady=5)

        actions = ttk.Frame(content)
        actions.grid(row=8, column=0, columnspan=4, sticky="e", pady=(16, 0))
        ttk.Button(actions, text="取消", command=self.window.destroy).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(actions, text="保存", command=self._save).grid(row=0, column=1)

        self._sync_limit_entry(self.max_files_entry, self.unlimited_files_var)
        self._sync_limit_entry(self.max_total_entry, self.unlimited_total_var)
        self._sync_limit_entry(self.max_archives_entry, self.unlimited_archives_var)

    def _add_limit_row(
        self,
        content: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        unlimited_variable: tk.BooleanVar,
        unit: str,
    ) -> ttk.Entry:
        ttk.Label(content, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
        entry = ttk.Entry(content, textvariable=variable, width=16)
        entry.grid(row=row, column=1, sticky="w", pady=5)
        ttk.Label(content, text=unit).grid(row=row, column=2, sticky="w", padx=(8, 12), pady=5)
        ttk.Checkbutton(
            content,
            text="不限制",
            variable=unlimited_variable,
            command=lambda: self._sync_limit_entry(entry, unlimited_variable),
        ).grid(row=row, column=3, sticky="w", pady=5)
        return entry

    @staticmethod
    def _sync_limit_entry(entry: ttk.Entry, unlimited_variable: tk.BooleanVar) -> None:
        entry.state(["disabled"] if unlimited_variable.get() else ["!disabled"])

    def _add_directory_row(
        self,
        content: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        title: str,
    ) -> None:
        ttk.Label(content, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
        ttk.Entry(content, textvariable=variable, width=58).grid(
            row=row, column=1, sticky="ew", pady=5
        )
        ttk.Button(
            content,
            text="选择",
            command=lambda: self._choose_directory(variable, title),
        ).grid(row=row, column=2, padx=(10, 0), pady=5)

    def _add_winrar_row(self, content: ttk.Frame, row: int) -> None:
        ttk.Label(content, text="WinRAR").grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
        ttk.Entry(content, textvariable=self.winrar_var, width=58).grid(
            row=row, column=1, sticky="ew", pady=5
        )
        ttk.Button(content, text="选择", command=self._choose_winrar).grid(
            row=row, column=2, padx=(10, 0), pady=5
        )

    def _choose_directory(self, variable: tk.StringVar, title: str) -> None:
        current = variable.get().strip()
        selected = filedialog.askdirectory(
            title=title,
            initialdir=current if Path(current).is_dir() else None,
            parent=self.window,
        )
        if selected:
            variable.set(selected)

    def _choose_winrar(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择 WinRAR.exe",
            filetypes=(("WinRAR", "WinRAR.exe"), ("程序", "*.exe")),
            parent=self.window,
        )
        if selected:
            self.winrar_var.set(selected)

    def _save(self) -> None:
        try:
            settings = AppSettings(
                work_root=self.work_root_var.get().strip(),
                output_root=self.output_root_var.get().strip(),
                winrar_path=self.winrar_var.get().strip(),
                max_files=_optional_positive_int(
                    self.max_files_var.get(), self.unlimited_files_var.get(), "最大文件数"
                ),
                max_total_gb=_optional_positive_int(
                    self.max_total_gb_var.get(), self.unlimited_total_var.get(), "最大累计容量"
                ),
                max_archives=_optional_positive_int(
                    self.max_archives_var.get(),
                    self.unlimited_archives_var.get(),
                    "最大压缩包数",
                ),
                max_concurrency=_positive_int(self.max_concurrency_var.get(), "同时运行任务数"),
            )
            settings.validate()
        except ValueError as error:
            messagebox.showwarning("设置无效", str(error), parent=self.window)
            return

        self.on_save(settings)
        self.window.destroy()


def _positive_int(value: str, label: str) -> int:
    try:
        number = int(value.strip())
    except ValueError as error:
        raise ValueError(f"{label}必须填写整数。") from error
    if number <= 0:
        raise ValueError(f"{label}必须大于 0。")
    return number


def _optional_positive_int(value: str, unlimited: bool, label: str) -> int:
    return 0 if unlimited else _positive_int(value, label)
