"""多任务表格、队列调度和拖拽入口。"""

from __future__ import annotations

import os
import threading
import traceback
import tkinter as tk
from contextlib import suppress
from pathlib import Path
from queue import Empty, Queue
from tkinter import filedialog, messagebox, ttk

from extraction_job import ExtractionJobResult, ExtractionLimits, JobStatus, run_extraction_job
from password_vault import PasswordVaultError, PasswordVaultStore, normalize_passwords
from task_logging import TaskLogWriter
from task_queue import (
    AppSettings,
    HistoryStore,
    QueueScheduler,
    QueueTask,
    SettingsStore,
    TaskCollection,
    TaskState,
    default_state_directory,
)
from ui.about_dialog import AboutDialog
from ui.app_assets import apply_window_icon, configure_windows_app_identity
from ui.password_dialogs import PasswordPoolDialog
from ui.settings_dialog import SettingsDialog
from volume_resolver import expand_root_inputs
from windows_file_drop import WindowsFileDrop
from winrar_adapter import WinRARAdapter


class MainWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("嵌套解压助手")
        self.root.minsize(1080, 650)

        state_directory = default_state_directory()
        self.settings_store = SettingsStore(state_directory / "settings.json")
        self.history_store = HistoryStore(state_directory / "history.json")
        self.password_vault_store = PasswordVaultStore(state_directory / "password-vault.json")
        self.task_log_root = state_directory.parent / "logs"
        self.settings = self.settings_store.load(AppSettings.defaults())
        self.tasks = TaskCollection(self.history_store.load())
        try:
            self.password_pool = self.password_vault_store.load()
            vault_status = "等待添加任务"
        except PasswordVaultError:
            self.password_pool = []
            vault_status = "旧密码池格式已停用，请在设置中重新保存"
        self.scheduler = QueueScheduler(
            self.tasks,
            max_concurrency=self.settings.max_concurrency,
        )

        self.status_var = tk.StringVar(value=vault_status)
        self.queue_summary_var = tk.StringVar()
        self.drag_status_var = tk.StringVar(value="正在启用文件拖拽...")
        self._file_drop: WindowsFileDrop | None = None
        self._ui_events: Queue[tuple[str, str, object]] = Queue()
        self._closing = False
        self._ui_poll_id: str | None = None
        self._password_editor: ttk.Entry | None = None
        self._password_editor_task_id: str | None = None
        self._context_task_id: str | None = None

        self._configure_style()
        self._build_menu()
        self._build_layout()
        self._render_all_tasks()
        self._update_summary()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Control-o>", lambda _event: self._choose_files())
        self.root.bind("<Control-Shift-O>", lambda _event: self._choose_folder())
        self.root.bind("<F5>", lambda _event: self._start_all())
        self._ui_poll_id = self.root.after(50, self._drain_ui_events)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Heading.TLabel", font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("Muted.TLabel", foreground="#555555")
        style.configure("Treeview", rowheight=28)
        style.configure("Primary.TButton", padding=(12, 6))

    def _build_menu(self) -> None:
        menu = tk.Menu(self.root)

        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="添加文件...", accelerator="Ctrl+O", command=self._choose_files)
        file_menu.add_command(
            label="添加文件夹...",
            accelerator="Ctrl+Shift+O",
            command=self._choose_folder,
        )
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)
        menu.add_cascade(label="文件", menu=file_menu)

        task_menu = tk.Menu(menu, tearoff=False)
        task_menu.add_command(label="开始或重新开始所选任务", command=self._start_selected)
        task_menu.add_command(label="开始所有等待任务", accelerator="F5", command=self._start_all)
        task_menu.add_command(label="编辑所选任务密码", command=self._edit_selected_password)
        task_menu.add_command(label="重试所选任务", command=self._retry_selected)
        task_menu.add_command(label="移除所选任务", command=self._remove_selected)
        task_menu.add_command(label="打开所选结果目录", command=self._open_selected_result)
        task_menu.add_command(label="打开全部任务日志", command=self._open_global_log)
        task_menu.add_separator()
        task_menu.add_command(label="清除已结束历史", command=self._clear_finished)
        menu.add_cascade(label="任务", menu=task_menu)

        settings_menu = tk.Menu(menu, tearoff=False)
        settings_menu.add_command(label="密码池...", command=self._open_password_pool)
        settings_menu.add_separator()
        settings_menu.add_command(label="任务与路径设置...", command=self._open_settings)
        menu.add_cascade(label="设置", menu=settings_menu)

        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="关于嵌套解压助手...", command=self._open_about)
        menu.add_cascade(label="帮助", menu=help_menu)

        self.root.configure(menu=menu)

    def _build_layout(self) -> None:
        container = ttk.Frame(self.root, padding=14)
        container.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(2, weight=1)

        heading = ttk.Frame(container)
        heading.grid(row=0, column=0, sticky="ew")
        heading.columnconfigure(0, weight=1)
        ttk.Label(heading, text="任务队列", style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(heading, textvariable=self.queue_summary_var, style="Muted.TLabel").grid(
            row=0, column=1, sticky="e"
        )

        toolbar = ttk.Frame(container)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(12, 10))
        ttk.Button(toolbar, text="添加文件", command=self._choose_files).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(toolbar, text="添加文件夹", command=self._choose_folder).grid(
            row=0, column=1, padx=(0, 8)
        )
        ttk.Button(
            toolbar,
            text="开始所选",
            style="Primary.TButton",
            command=self._start_selected,
        ).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(toolbar, text="开始全部", command=self._start_all).grid(
            row=0, column=3, padx=(0, 8)
        )
        ttk.Button(toolbar, text="编辑密码", command=self._edit_selected_password).grid(
            row=0, column=4, padx=(0, 8)
        )
        ttk.Button(toolbar, text="重试", command=self._retry_selected).grid(
            row=0, column=5, padx=(0, 8)
        )
        ttk.Button(toolbar, text="移除", command=self._remove_selected).grid(
            row=0, column=6, padx=(0, 8)
        )
        ttk.Button(toolbar, text="打开结果", command=self._open_selected_result).grid(
            row=0, column=7, padx=(0, 8)
        )
        ttk.Button(toolbar, text="任务日志", command=self._open_global_log).grid(
            row=0, column=8, padx=(0, 8)
        )
        ttk.Button(toolbar, text="设置", command=self._open_settings).grid(row=0, column=9)

        table_frame = ttk.Frame(container)
        table_frame.grid(row=2, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = ("file", "status", "progress", "password", "result", "created")
        self.table = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
        )
        headings = {
            "file": "文件",
            "status": "状态",
            "progress": "进度",
            "password": "密码",
            "result": "结果目录",
            "created": "添加时间",
        }
        widths = {
            "file": 260,
            "status": 105,
            "progress": 300,
            "password": 125,
            "result": 260,
            "created": 140,
        }
        for column in columns:
            self.table.heading(column, text=headings[column])
            self.table.column(
                column,
                width=widths[column],
                minwidth=70,
                stretch=column in {"file", "progress", "result"},
            )
        self.table.grid(row=0, column=0, sticky="nsew")
        self.table.bind("<Double-1>", self._on_table_double_click)
        self.table.bind("<Button-3>", self._show_task_context_menu)
        self._build_task_context_menu()

        vertical = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)

        self.table.tag_configure(TaskState.RUNNING.value, background="#e8f1fb")
        self.table.tag_configure(TaskState.COMPLETED.value, background="#eaf5eb")
        self.table.tag_configure(TaskState.PASSWORD_REQUIRED.value, background="#fff4d6")
        self.table.tag_configure(TaskState.FAILED.value, background="#fdeaea")
        self.table.tag_configure(TaskState.BLOCKED.value, background="#fdeaea")
        self.table.tag_configure(TaskState.INTERRUPTED.value, background="#eeeeee")
        self.table.tag_configure(TaskState.RESULT_MISSING.value, background="#fff4d6")

        status_bar = ttk.Frame(container)
        status_bar.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        status_bar.columnconfigure(0, weight=1)
        ttk.Label(status_bar, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Label(status_bar, textvariable=self.drag_status_var, style="Muted.TLabel").grid(
            row=0, column=1, sticky="e"
        )

        self._enable_drag_drop()

    def _enable_drag_drop(self) -> None:
        try:
            self._file_drop = WindowsFileDrop(
                self.table,
                lambda paths: self._ui_events.put(("drop", "", paths)),
            )
            self.drag_status_var.set("可将多个文件或文件夹拖入表格")
        except (OSError, RuntimeError):
            self._file_drop = None
            self.drag_status_var.set("拖拽不可用，可使用添加文件或文件夹")

    def _choose_files(self) -> None:
        paths = filedialog.askopenfilenames(title="添加待解压文件", parent=self.root)
        self._add_paths(paths)

    def _choose_folder(self) -> None:
        path = filedialog.askdirectory(title="添加包含待解压文件的文件夹", parent=self.root)
        if path:
            self._add_paths([path])

    def _add_paths(self, paths: tuple[str, ...] | list[str] | tuple[object, ...]) -> None:
        expanded = expand_root_inputs([str(path) for path in paths])
        added = self.tasks.add_files(expanded)
        if not added:
            self.status_var.set("没有添加新任务；空文件夹、不存在路径或重复任务已忽略")
            return
        for task in added:
            self._render_task(task)
        self.table.selection_set(added[0].id)
        self.table.see(added[0].id)
        self.status_var.set(f"已添加 {len(added)} 个任务")
        self._save_history()
        self._update_summary()
        if self.scheduler.enabled:
            self._launch_claimed(self.scheduler.claim_available())

    def _build_task_context_menu(self) -> None:
        self._task_context_menu = tk.Menu(self.root, tearoff=False)
        self._task_context_menu.add_command(label="开始当前任务", command=self._start_context_task)
        self._task_context_menu.add_command(label="开始所选任务", command=self._start_selected)
        self._task_context_menu.add_separator()
        self._task_context_menu.add_command(label="编辑当前任务密码", command=self._edit_context_password)
        self._task_context_menu.add_command(label="重试所选任务", command=self._retry_selected)
        self._task_context_menu.add_command(label="移除所选任务", command=self._remove_selected)
        self._task_context_menu.add_command(label="打开当前结果目录", command=self._open_context_result)
        self._task_context_menu.add_command(label="打开当前任务日志", command=self._open_context_log)
        self._task_context_menu.add_separator()
        self._task_context_menu.add_command(label="全选", command=self._select_all_tasks)

    def _show_task_context_menu(self, event: tk.Event) -> str | None:
        row_id = self.table.identify_row(event.y)
        if not row_id:
            return None
        if row_id not in self.table.selection():
            self.table.selection_set(row_id)
        self.table.focus(row_id)
        self._context_task_id = row_id
        try:
            self._task_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._task_context_menu.grab_release()
        return "break"

    def _start_all(self) -> None:
        self._start_task_ids(None)

    def _start_selected(self) -> None:
        selected_ids = self._prepare_startable_task_ids(self._selected_tasks())
        if not selected_ids:
            self.status_var.set("所选任务正在运行或已经完成")
            return
        self._start_task_ids(selected_ids)

    def _start_context_task(self) -> None:
        if self._context_task_id is None:
            return
        task = self.tasks.get(self._context_task_id)
        task_ids = self._prepare_startable_task_ids([task] if task is not None else [])
        if not task_ids:
            self.status_var.set("当前任务正在运行或已经完成")
            return
        self._start_task_ids(task_ids)

    def _prepare_startable_task_ids(self, tasks: list[QueueTask]) -> list[str]:
        task_ids: list[str] = []
        changed = False
        for task in tasks:
            if task.state is TaskState.PENDING:
                task_ids.append(task.id)
                continue
            if task.state in {TaskState.RUNNING, TaskState.COMPLETED}:
                continue
            if self.tasks.retry(task.id):
                task_ids.append(task.id)
                self._render_task(task)
                changed = True
        if changed:
            self._save_history()
            self._update_summary()
        return task_ids

    def _start_task_ids(self, task_ids: list[str] | None) -> None:
        try:
            self.settings.validate()
        except ValueError as error:
            messagebox.showwarning("设置不完整", str(error), parent=self.root)
            self._open_settings()
            return
        claimed = self.scheduler.begin(task_ids)
        if not claimed:
            self.status_var.set("没有可开始的等待任务")
            return
        self._launch_claimed(claimed)

    def _launch_claimed(self, tasks: list[QueueTask]) -> None:
        for task in tasks:
            self._render_task(task)
            settings = self.settings
            password = task.password
            password_pool = tuple(self.password_pool)
            worker = threading.Thread(
                target=self._run_task,
                args=(task.id, task.source, password, password_pool, settings),
                daemon=True,
            )
            worker.start()
        if tasks:
            self.status_var.set(f"正在运行 {len(self.scheduler.active_ids)} 个任务")
            self._save_history()
            self._update_summary()

    def _run_task(
        self,
        task_id: str,
        source: Path,
        password: str,
        password_pool: tuple[str, ...],
        settings: AppSettings,
    ) -> None:
        logger = TaskLogWriter(self.task_log_root, task_id)
        logger.record(
            "run_started",
            "任务开始运行",
            source=str(source),
            work_root=settings.work_root,
            output_root=settings.output_root,
            max_files=settings.max_files,
            max_total_gb=settings.max_total_gb,
            max_archives=settings.max_archives,
            max_concurrency=settings.max_concurrency,
            task_password=password,
            password_pool=list(password_pool),
        )

        def report_progress(message: str) -> None:
            logger.record("progress", message)
            self._ui_events.put(("progress", task_id, message))

        try:
            result = run_extraction_job(
                source=source,
                work_root=settings.work_root,
                output_root=settings.output_root,
                password=password,
                password_candidates=password_pool,
                winrar=WinRARAdapter(settings.winrar_path),
                progress=report_progress,
                limits=ExtractionLimits(
                    max_files=settings.max_files,
                    max_total_bytes=settings.max_total_gb * 1024**3,
                    max_archives=settings.max_archives,
                ),
            )
        except Exception as error:
            logger.record(
                "unexpected_error",
                "任务发生未预期错误",
                level="ERROR",
                exception_type=type(error).__name__,
                traceback=traceback.format_exc(),
            )
            result = ExtractionJobResult(JobStatus.FAILED, "任务发生未预期错误，已停止。")
        logger.record(
            "run_finished",
            result.message,
            level="INFO" if result.status is JobStatus.COMPLETED else "ERROR",
            status=result.status.value,
            method=result.method.value if result.method is not None else None,
            output_directory=str(result.output_directory) if result.output_directory else None,
            work_directory=str(result.work_directory) if result.work_directory else None,
            file_count=result.file_count,
            total_bytes=result.total_bytes,
            archive_count=result.archive_count,
            volume_group_count=result.volume_group_count,
        )
        self._ui_events.put(("finished", task_id, result))

    def _drain_ui_events(self) -> None:
        self._ui_poll_id = None
        while True:
            try:
                event_type, task_id, payload = self._ui_events.get_nowait()
            except Empty:
                break
            if event_type == "progress":
                self._on_task_progress(task_id, str(payload))
            elif event_type == "finished" and isinstance(payload, ExtractionJobResult):
                self._on_task_finished(task_id, payload)
            elif event_type == "drop" and isinstance(payload, list):
                self._add_paths(payload)
        if not self._closing:
            self._ui_poll_id = self.root.after(50, self._drain_ui_events)

    def _on_task_progress(self, task_id: str, message: str) -> None:
        self.tasks.update_progress(task_id, message)
        task = self.tasks.get(task_id)
        if task is not None:
            self._render_task(task)

    def _on_task_finished(self, task_id: str, result: ExtractionJobResult) -> None:
        claimed = self.scheduler.complete(task_id, result)
        task = self.tasks.get(task_id)
        if task is not None:
            self._render_task(task)
            if task.state is TaskState.PASSWORD_REQUIRED:
                self.status_var.set(f"{task.source.name}：任务密码和密码池均未匹配")
            else:
                self.status_var.set(f"{task.source.name}：{task.progress}")
        self._save_history()
        self._update_summary()
        self._launch_claimed(claimed)
        if not self.scheduler.active_ids and not self.scheduler.enabled:
            self.status_var.set("本次队列处理完成")

    def _edit_selected_password(self) -> None:
        tasks = self._selected_tasks()
        if len(tasks) != 1:
            messagebox.showwarning("选择一个任务", "请只选择一个需要编辑密码的任务。", parent=self.root)
            return
        self._begin_password_edit(tasks[0].id)

    def _edit_context_password(self) -> None:
        if self._context_task_id is not None:
            self._begin_password_edit(self._context_task_id)

    def _on_table_double_click(self, event: tk.Event) -> str | None:
        row_id = self.table.identify_row(event.y)
        column = self.table.identify_column(event.x)
        if row_id and column == "#4":
            self.table.selection_set(row_id)
            self.table.focus(row_id)
            self._begin_password_edit(row_id)
            return "break"
        return None

    def _begin_password_edit(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        if task is None:
            return
        if task.state is TaskState.RUNNING:
            messagebox.showwarning("无法修改", "运行中的任务不能修改密码。", parent=self.root)
            return
        self._cancel_password_editor()
        self.table.see(task_id)
        self.root.update_idletasks()
        bounds = self.table.bbox(task_id, "password")
        if not bounds:
            return
        x, y, width, height = bounds
        editor = ttk.Entry(self.table)
        editor.insert(0, task.password)
        editor.place(x=x, y=y, width=width, height=height)
        editor.select_range(0, "end")
        editor.focus_set()
        editor.bind("<Return>", self._commit_password_from_event)
        editor.bind("<Escape>", self._cancel_password_from_event)
        editor.bind("<FocusOut>", self._commit_password_from_event)
        self._password_editor = editor
        self._password_editor_task_id = task_id

    def _commit_password_from_event(self, _event: tk.Event) -> str:
        self._commit_password_editor()
        return "break"

    def _cancel_password_from_event(self, _event: tk.Event) -> str:
        self._cancel_password_editor()
        return "break"

    def _commit_password_editor(self) -> None:
        editor = self._password_editor
        task_id = self._password_editor_task_id
        if editor is None or task_id is None:
            return
        password = editor.get()
        try:
            normalize_passwords([password])
        except ValueError as error:
            messagebox.showwarning("密码无效", str(error), parent=self.root)
            editor.focus_set()
            return
        self._password_editor = None
        self._password_editor_task_id = None
        editor.destroy()
        self._apply_task_password(task_id, password, False)

    def _cancel_password_editor(self) -> None:
        editor = self._password_editor
        self._password_editor = None
        self._password_editor_task_id = None
        if editor is not None:
            editor.destroy()

    def _apply_task_password(self, task_id: str, password: str, retry: bool) -> bool:
        task = self.tasks.get(task_id)
        if task is None:
            return False
        if not self.tasks.set_password(task.id, password):
            messagebox.showwarning("无法修改", "运行中的任务不能修改密码。", parent=self.root)
            return False
        self._render_task(task)
        self.status_var.set(
            f"{task.source.name}：已设置任务密码优先" if password else f"{task.source.name}：使用密码池"
        )
        if retry:
            if task.state is not TaskState.PENDING and not self.tasks.retry(task.id):
                messagebox.showwarning("无法重试", "运行中的任务不能重试。", parent=self.root)
                return False
            self._render_task(task)
            self._save_history()
            self._launch_claimed(self.scheduler.begin([task.id]))
        return True

    def _retry_selected(self) -> None:
        selected = self._selected_tasks()
        if not selected:
            return
        retried: list[QueueTask] = []
        for task in selected:
            if self.tasks.retry(task.id):
                retried.append(task)
                self._render_task(task)
        if not retried:
            self.status_var.set("所选任务均不能重试")
            return
        self._save_history()
        self._launch_claimed(self.scheduler.begin([task.id for task in retried]))

    def _remove_selected(self) -> None:
        selected = self._selected_tasks()
        if not selected:
            return
        removed = 0
        skipped = 0
        for task in selected:
            if self.tasks.remove(task.id):
                self.table.delete(task.id)
                removed += 1
            else:
                skipped += 1
        self._save_history()
        self._update_summary()
        self.status_var.set(f"已移除 {removed} 个任务，跳过 {skipped} 个运行中任务")

    def _clear_finished(self) -> None:
        removed = self.tasks.clear_finished()
        self._render_all_tasks()
        self._save_history()
        self._update_summary()
        self.status_var.set(f"已清除 {removed} 条结束记录")

    def _open_selected_result(self) -> None:
        tasks = self._selected_tasks()
        task = tasks[0] if len(tasks) == 1 else None
        if task is None or task.output_directory is None or not task.output_directory.is_dir():
            messagebox.showwarning("没有结果", "所选任务没有可打开的结果目录。", parent=self.root)
            return
        try:
            os.startfile(task.output_directory)  # type: ignore[attr-defined]
        except OSError as error:
            messagebox.showerror("无法打开目录", str(error), parent=self.root)

    def _open_context_result(self) -> None:
        if self._context_task_id is None:
            return
        self.table.selection_set(self._context_task_id)
        self._open_selected_result()

    def _open_selected_log(self) -> None:
        tasks = self._selected_tasks()
        task = tasks[0] if len(tasks) == 1 else None
        if task is None:
            messagebox.showwarning("选择一个任务", "请只选择一个需要查看日志的任务。", parent=self.root)
            return
        log_path = self.task_log_root / task.id / "task.log"
        if not log_path.is_file():
            messagebox.showwarning("没有日志", "该任务尚未运行，因此还没有任务日志。", parent=self.root)
            return
        try:
            os.startfile(log_path)  # type: ignore[attr-defined]
        except OSError as error:
            messagebox.showerror("无法打开日志", str(error), parent=self.root)

    def _open_global_log(self) -> None:
        log_path = self.task_log_root / "all-tasks.log"
        if not log_path.is_file():
            messagebox.showwarning("没有日志", "当前还没有运行过任务。", parent=self.root)
            return
        try:
            os.startfile(log_path)  # type: ignore[attr-defined]
        except OSError as error:
            messagebox.showerror("无法打开日志", str(error), parent=self.root)

    def _open_context_log(self) -> None:
        if self._context_task_id is None:
            return
        self.table.selection_set(self._context_task_id)
        self._open_selected_log()

    def _select_all_tasks(self) -> None:
        self.table.selection_set(self.table.get_children())

    def _open_settings(self) -> None:
        SettingsDialog(self.root, self.settings, self._save_settings)

    def _open_password_pool(self) -> None:
        PasswordPoolDialog(self.root, self.password_pool, self._save_password_pool)

    def _open_about(self) -> None:
        AboutDialog(self.root)

    def _save_password_pool(self, passwords: list[str]) -> bool:
        try:
            self.password_vault_store.save(passwords)
        except PasswordVaultError as error:
            messagebox.showerror("密码池保存失败", str(error), parent=self.root)
            return False
        self.password_pool = list(passwords)
        self._render_all_tasks()
        self.status_var.set(f"密码池已明文保存，共 {len(passwords)} 个密码")
        return True

    def _save_settings(self, settings: AppSettings) -> None:
        try:
            self.settings_store.save(settings)
        except OSError as error:
            messagebox.showerror("保存失败", f"无法保存设置：{error}", parent=self.root)
            return
        self.settings = settings
        self.scheduler.set_max_concurrency(settings.max_concurrency)
        if self.scheduler.enabled:
            self._launch_claimed(self.scheduler.claim_available())
        self.status_var.set("设置已保存；新启动的任务将使用新设置")

    def _selected_task(self) -> QueueTask | None:
        selected = self.table.selection()
        return self.tasks.get(selected[0]) if selected else None

    def _selected_tasks(self) -> list[QueueTask]:
        selected: list[QueueTask] = []
        for task_id in self.table.selection():
            task = self.tasks.get(task_id)
            if task is not None:
                selected.append(task)
        return selected

    def _render_all_tasks(self) -> None:
        for item in self.table.get_children():
            self.table.delete(item)
        for task in self.tasks.tasks:
            self._render_task(task)

    def _render_task(self, task: QueueTask) -> None:
        values = (
            str(task.source),
            _state_text(task.state),
            task.progress,
            _password_state_text(task, len(self.password_pool)),
            str(task.output_directory) if task.output_directory else "",
            _short_time(task.created_at),
        )
        if self.table.exists(task.id):
            self.table.item(task.id, values=values, tags=(task.state.value,))
        else:
            self.table.insert("", "end", iid=task.id, values=values, tags=(task.state.value,))

    def _save_history(self) -> None:
        try:
            self.history_store.save(self.tasks.tasks)
        except OSError as error:
            self.status_var.set(f"历史记录保存失败：{error}")

    def _update_summary(self) -> None:
        total = len(self.tasks.tasks)
        running = sum(task.state is TaskState.RUNNING for task in self.tasks.tasks)
        pending = sum(task.state is TaskState.PENDING for task in self.tasks.tasks)
        completed = sum(task.state is TaskState.COMPLETED for task in self.tasks.tasks)
        self.queue_summary_var.set(
            f"总计 {total}  |  运行 {running}  |  等待 {pending}  |  完成 {completed}"
        )

    def _on_close(self) -> None:
        if self._closing:
            return
        if self.scheduler.active_ids:
            messagebox.showwarning(
                "任务正在运行",
                "当前版本不能安全取消正在运行的 WinRAR 任务，请等待任务结束后关闭。",
                parent=self.root,
            )
            return
        self._closing = True
        if self._ui_poll_id is not None:
            with suppress(tk.TclError):
                self.root.after_cancel(self._ui_poll_id)
            self._ui_poll_id = None
        if self._file_drop is not None:
            self._file_drop.close()
        self._cancel_password_editor()
        self._save_history()
        self.root.destroy()


def _state_text(state: TaskState) -> str:
    return {
        TaskState.PENDING: "等待中",
        TaskState.RUNNING: "运行中",
        TaskState.COMPLETED: "已完成",
        TaskState.PASSWORD_REQUIRED: "需要密码",
        TaskState.NOT_ARCHIVE: "未识别",
        TaskState.BLOCKED: "已阻止",
        TaskState.FAILED: "失败",
        TaskState.INTERRUPTED: "已中断",
        TaskState.RESULT_MISSING: "结果不可用",
    }[state]


def _password_state_text(task: QueueTask, pool_count: int) -> str:
    if task.state is TaskState.PASSWORD_REQUIRED:
        return "均未匹配"
    if task.password:
        return task.password
    if pool_count:
        return f"密码池 {pool_count} 个"
    return "无密码"


def _short_time(value: str) -> str:
    return value[:16].replace("T", " ")


def run_application() -> None:
    configure_windows_app_identity()
    root = tk.Tk()
    apply_window_icon(root)
    MainWindow(root)
    root.mainloop()
