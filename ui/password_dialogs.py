"""密码池管理窗口。"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, simpledialog, ttk

from password_vault import MAX_SAVED_PASSWORDS, normalize_passwords, parse_password_text


PASSWORD_POOL_PREFERRED_WIDTH = 620
PASSWORD_POOL_PREFERRED_HEIGHT = 500


class PasswordPoolDialog:
    def __init__(
        self,
        parent: tk.Misc,
        passwords: list[str],
        on_save: Callable[[list[str]], bool],
    ) -> None:
        self.passwords = list(passwords)
        self.on_save = on_save
        self.window = tk.Toplevel(parent)
        self.window.title("密码池")
        self.window.transient(parent)
        self.window.grab_set()

        container = ttk.Frame(self.window, padding=14)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="常用密码", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        ttk.Label(
            container,
            text="第一项是默认密码。解压时按表格顺序尝试，密码内容不会写入任务历史。",
        ).pack(anchor="w", pady=(4, 10))

        self.table = ttk.Treeview(
            container,
            columns=("order", "role", "password"),
            show="headings",
            selectmode="browse",
            height=10,
        )
        self.table.heading("order", text="顺序")
        self.table.heading("role", text="用途")
        self.table.heading("password", text="密码")
        self.table.column("order", width=60, anchor="center", stretch=False)
        self.table.column("role", width=90, anchor="center", stretch=False)
        self.table.column("password", width=380, stretch=True)
        self.table.pack(fill="both", expand=True)
        self.table.bind("<Double-1>", lambda _event: self._edit())

        actions = ttk.Frame(container)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="添加", command=self._add).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="批量添加", command=self._bulk_add).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="编辑", command=self._edit).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="设为默认", command=self._make_default).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="删除", command=self._delete).pack(side="left")

        footer = ttk.Frame(container)
        footer.pack(fill="x", pady=(12, 0))
        self.count_var = tk.StringVar()
        ttk.Label(footer, textvariable=self.count_var).pack(side="left")
        ttk.Button(footer, text="取消", command=self.window.destroy).pack(side="right")
        ttk.Button(footer, text="明文保存", command=self._save).pack(side="right", padx=(0, 8))

        self._render()
        self._fit_window_to_content()

    def _fit_window_to_content(self) -> None:
        self.window.update_idletasks()
        required_width = self.window.winfo_reqwidth()
        required_height = self.window.winfo_reqheight()
        width = max(PASSWORD_POOL_PREFERRED_WIDTH, required_width)
        height = max(PASSWORD_POOL_PREFERRED_HEIGHT, required_height)
        self.window.minsize(required_width, required_height)
        self.window.geometry(f"{width}x{height}")

    def _selected_index(self) -> int | None:
        selected = self.table.selection()
        return int(selected[0]) if selected else None

    def _render(self, selected_index: int | None = None) -> None:
        for item in self.table.get_children():
            self.table.delete(item)
        for index, password in enumerate(self.passwords):
            self.table.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    index + 1,
                    "默认" if index == 0 else "备用",
                    _display_password(password),
                ),
            )
        self.count_var.set(f"已保存 {len(self.passwords)} / {MAX_SAVED_PASSWORDS} 个")
        if selected_index is not None and 0 <= selected_index < len(self.passwords):
            self.table.selection_set(str(selected_index))
            self.table.see(str(selected_index))

    def _add(self) -> None:
        value = simpledialog.askstring("添加密码", "输入新密码：", parent=self.window)
        if value is not None:
            self._append_values([value])

    def _bulk_add(self) -> None:
        _BulkPasswordDialog(self.window, self._append_values)

    def _append_values(self, values: list[str]) -> bool:
        try:
            merged = normalize_passwords([*self.passwords, *values])
        except ValueError as error:
            messagebox.showwarning("密码无效", str(error), parent=self.window)
            return False
        self.passwords = merged
        self._render(len(self.passwords) - 1 if self.passwords else None)
        return True

    def _edit(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        value = simpledialog.askstring(
            "编辑密码",
            "修改所选密码：",
            initialvalue=self.passwords[index],
            parent=self.window,
        )
        if value is None:
            return
        others = self.passwords[:index] + self.passwords[index + 1 :]
        try:
            validated = normalize_passwords([value])
        except ValueError as error:
            messagebox.showwarning("密码无效", str(error), parent=self.window)
            return
        if not validated:
            messagebox.showwarning("密码为空", "请使用删除按钮移除密码。", parent=self.window)
            return
        if value in others:
            messagebox.showwarning("密码重复", "密码池中已经存在相同密码。", parent=self.window)
            return
        self.passwords[index] = value
        self._render(index)

    def _make_default(self) -> None:
        index = self._selected_index()
        if index is None or index == 0:
            return
        password = self.passwords.pop(index)
        self.passwords.insert(0, password)
        self._render(0)

    def _delete(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        self.passwords.pop(index)
        self._render(min(index, len(self.passwords) - 1))

    def _save(self) -> None:
        if self.on_save(self.passwords):
            self.window.destroy()


class _BulkPasswordDialog:
    def __init__(self, parent: tk.Misc, on_add: Callable[[list[str]], bool]) -> None:
        self.on_add = on_add
        self.window = tk.Toplevel(parent)
        self.window.title("批量添加密码")
        self.window.transient(parent)
        self.window.grab_set()

        container = ttk.Frame(self.window, padding=14)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="每行一个密码，也可使用 | 分隔。第一项优先级最高。").pack(anchor="w")
        ttk.Label(container, text="密码会直接显示，并以明文保存在本机密码池文件中。").pack(
            anchor="w", pady=(2, 8)
        )
        self.text = tk.Text(container, width=64, height=10, wrap="none")
        self.text.pack(fill="both", expand=True)
        self.text.focus_set()

        buttons = ttk.Frame(container)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="取消", command=self.window.destroy).pack(side="right")
        ttk.Button(buttons, text="添加", command=self._submit).pack(side="right", padx=(0, 8))

    def _submit(self) -> None:
        try:
            passwords = parse_password_text(self.text.get("1.0", "end-1c"))
        except ValueError as error:
            messagebox.showwarning("密码无效", str(error), parent=self.window)
            return
        if not passwords:
            messagebox.showwarning("没有密码", "没有识别到可添加的密码。", parent=self.window)
            return
        if self.on_add(passwords):
            self.window.destroy()


def _display_password(password: str) -> str:
    return password
