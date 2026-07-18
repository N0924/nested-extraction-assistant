"""多任务表格使用的数据模型、设置和无密码历史存储。"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from extraction_job import ExtractionJobResult, JobStatus
from winrar_adapter import find_winrar


MAX_HISTORY_ITEMS = 1_000
LEGACY_DEFAULT_LIMITS = (10_000, 100, 1_000)
PACKAGED_APP_DATA_DIRECTORY = "NestedExtractionAssistant"


class TaskState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PASSWORD_REQUIRED = "password_required"
    NOT_ARCHIVE = "not_archive"
    BLOCKED = "blocked"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    RESULT_MISSING = "result_missing"


@dataclass
class QueueTask:
    id: str
    source: Path
    state: TaskState = TaskState.PENDING
    progress: str = "等待开始"
    message: str = ""
    output_directory: Path | None = None
    created_at: str = field(default_factory=lambda: _now_text())
    started_at: str | None = None
    finished_at: str | None = None
    password: str = field(default="", repr=False)

    @classmethod
    def create(cls, source: str | Path) -> QueueTask:
        return cls(id=uuid4().hex, source=Path(source).resolve(strict=True))

    @property
    def password_state(self) -> str:
        return "已设置" if self.password else "未设置"

    def to_history(self) -> dict[str, object]:
        return {
            "id": self.id,
            "source": str(self.source),
            "state": self.state.value,
            "progress": self.progress,
            "message": self.message,
            "output_directory": str(self.output_directory) if self.output_directory else None,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_history(cls, data: dict[str, object]) -> QueueTask:
        state = TaskState(str(data.get("state", TaskState.INTERRUPTED.value)))
        if state in {TaskState.PENDING, TaskState.RUNNING}:
            state = TaskState.INTERRUPTED
        output_text = data.get("output_directory")
        output_path = Path(str(output_text)) if output_text else None
        progress = "上次运行已中断" if state is TaskState.INTERRUPTED else str(data.get("progress", ""))
        if state is TaskState.COMPLETED:
            try:
                output_available = (
                    output_path is not None
                    and output_path.is_dir()
                    and any(output_path.iterdir())
                )
            except OSError:
                output_available = False
            if not output_available:
                state = TaskState.RESULT_MISSING
                progress = "历史记录显示完成，但结果目录为空或不存在，请重试。"
        return cls(
            id=str(data["id"]),
            source=Path(str(data["source"])),
            state=state,
            progress=progress,
            message=str(data.get("message", "")),
            output_directory=output_path,
            created_at=str(data.get("created_at", _now_text())),
            started_at=str(data["started_at"]) if data.get("started_at") else None,
            finished_at=str(data["finished_at"]) if data.get("finished_at") else None,
            password="",
        )


class TaskCollection:
    def __init__(self, tasks: list[QueueTask] | None = None) -> None:
        self.tasks = list(tasks or [])

    def add_files(self, paths: list[str | Path] | tuple[str | Path, ...]) -> list[QueueTask]:
        active_sources = {
            _path_key(task.source)
            for task in self.tasks
            if task.state in {TaskState.PENDING, TaskState.RUNNING, TaskState.PASSWORD_REQUIRED}
        }
        added: list[QueueTask] = []
        for value in paths:
            try:
                source = Path(value).expanduser().resolve(strict=True)
            except (FileNotFoundError, OSError):
                continue
            if not source.is_file() or _path_key(source) in active_sources:
                continue
            task = QueueTask.create(source)
            self.tasks.append(task)
            added.append(task)
            active_sources.add(_path_key(source))
        return added

    def get(self, task_id: str) -> QueueTask | None:
        return next((task for task in self.tasks if task.id == task_id), None)

    def set_password(self, task_id: str, password: str) -> bool:
        task = self.get(task_id)
        if task is None or task.state is TaskState.RUNNING:
            return False
        task.password = password
        return True

    def mark_running(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task is None or task.state is not TaskState.PENDING:
            return False
        task.state = TaskState.RUNNING
        task.progress = "正在准备任务"
        task.message = ""
        task.started_at = _now_text()
        task.finished_at = None
        return True

    def update_progress(self, task_id: str, message: str) -> None:
        task = self.get(task_id)
        if task is not None and task.state is TaskState.RUNNING:
            task.progress = message

    def apply_result(self, task_id: str, result: ExtractionJobResult) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        task.state = _state_from_result(result.status)
        task.progress = result.message
        task.message = result.message
        task.output_directory = result.output_directory
        task.finished_at = _now_text()
        task.password = ""
        return True

    def retry(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task is None or task.state in {TaskState.PENDING, TaskState.RUNNING}:
            return False
        task.state = TaskState.PENDING
        task.progress = "等待重新开始"
        task.message = ""
        task.output_directory = None
        task.started_at = None
        task.finished_at = None
        return True

    def remove(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task is None or task.state is TaskState.RUNNING:
            return False
        self.tasks.remove(task)
        return True

    def clear_finished(self) -> int:
        retained = [
            task
            for task in self.tasks
            if task.state in {TaskState.PENDING, TaskState.RUNNING, TaskState.PASSWORD_REQUIRED}
        ]
        removed = len(self.tasks) - len(retained)
        self.tasks = retained
        return removed

    def pending(self) -> list[QueueTask]:
        return [task for task in self.tasks if task.state is TaskState.PENDING]


class QueueScheduler:
    """在主线程中领取待处理任务；实际工作线程由界面创建。"""

    def __init__(self, tasks: TaskCollection, *, max_concurrency: int = 1) -> None:
        self.tasks = tasks
        self.active_ids: set[str] = set()
        self.enabled = False
        self._run_all = False
        self._requested_ids: list[str] = []
        self.set_max_concurrency(max_concurrency)

    def set_max_concurrency(self, value: int) -> None:
        if not 1 <= value <= 5:
            raise ValueError("并发任务数必须在 1 到 5 之间。")
        self.max_concurrency = value

    def begin(self, task_ids: Sequence[str] | None = None) -> list[QueueTask]:
        self.enabled = True
        if task_ids is None:
            self._run_all = True
            self._requested_ids.clear()
        elif not self._run_all:
            for task_id in task_ids:
                if task_id not in self._requested_ids:
                    self._requested_ids.append(task_id)
        return self.claim_available()

    def claim_available(self) -> list[QueueTask]:
        return self._claim_available() if self.enabled else []

    def complete(self, task_id: str, result: ExtractionJobResult) -> list[QueueTask]:
        self.active_ids.discard(task_id)
        if task_id in self._requested_ids:
            self._requested_ids.remove(task_id)
        self.tasks.apply_result(task_id, result)
        claimed = self._claim_available() if self.enabled else []
        if not self.active_ids and not self._has_eligible_pending():
            self.enabled = False
            self._run_all = False
            self._requested_ids.clear()
        return claimed

    def _claim_available(self) -> list[QueueTask]:
        claimed: list[QueueTask] = []
        for task in self._eligible_pending():
            if len(self.active_ids) >= self.max_concurrency:
                break
            if self.tasks.mark_running(task.id):
                self.active_ids.add(task.id)
                claimed.append(task)
        return claimed

    def _eligible_pending(self) -> list[QueueTask]:
        if self._run_all:
            return self.tasks.pending()
        eligible: list[QueueTask] = []
        for task_id in self._requested_ids:
            task = self.tasks.get(task_id)
            if task is not None and task.state is TaskState.PENDING:
                eligible.append(task)
        return eligible

    def _has_eligible_pending(self) -> bool:
        return bool(self._eligible_pending())


@dataclass(frozen=True)
class AppSettings:
    work_root: str
    output_root: str
    winrar_path: str
    max_files: int = 0
    max_total_gb: int = 0
    max_archives: int = 0
    max_concurrency: int = 1

    @classmethod
    def defaults(cls) -> AppSettings:
        base = Path.home() / "Documents" / "嵌套解压助手"
        detected = find_winrar()
        return cls(
            work_root=str(base / "工作目录"),
            output_root=str(base / "输出"),
            winrar_path=str(detected) if detected else "",
        )

    def validate(self) -> None:
        required_paths = (self.work_root, self.output_root, self.winrar_path)
        if any(not path.strip() for path in required_paths):
            raise ValueError("工作目录、输出目录和 WinRAR 路径不能为空。")
        if min(self.max_files, self.max_total_gb, self.max_archives) < 0:
            raise ValueError("任务上限不能小于 0；0 表示不限制。")
        if not 1 <= self.max_concurrency <= 5:
            raise ValueError("并发任务数必须在 1 到 5 之间。")


class SettingsStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self, defaults: AppSettings) -> AppSettings:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("设置文件必须是 JSON 对象。")
            if (
                data.get("max_files"),
                data.get("max_total_gb"),
                data.get("max_archives"),
            ) == LEGACY_DEFAULT_LIMITS:
                data["max_files"] = 0
                data["max_total_gb"] = 0
                data["max_archives"] = 0
            settings = AppSettings(**data)
            settings.validate()
            return settings
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return defaults

    def save(self, settings: AppSettings) -> None:
        settings.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _write_json_atomically(self.path, asdict(settings))


class HistoryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> list[QueueTask]:
        try:
            raw_items = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(raw_items, list):
            return []

        tasks: list[QueueTask] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            try:
                tasks.append(QueueTask.from_history(item))
            except (KeyError, ValueError, TypeError):
                continue
        return tasks[-MAX_HISTORY_ITEMS:]

    def save(self, tasks: list[QueueTask]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [task.to_history() for task in tasks[-MAX_HISTORY_ITEMS:]]
        _write_json_atomically(self.path, payload)


def default_state_directory() -> Path:
    if getattr(sys, "frozen", False):
        local_app_data = os.environ.get("LOCALAPPDATA")
        base_directory = (
            Path(local_app_data)
            if local_app_data
            else Path.home() / "AppData" / "Local"
        )
        return base_directory / PACKAGED_APP_DATA_DIRECTORY / "state"
    return Path(__file__).resolve().parent / "runtime" / "state"


def _state_from_result(status: JobStatus) -> TaskState:
    return {
        JobStatus.COMPLETED: TaskState.COMPLETED,
        JobStatus.PASSWORD_REQUIRED: TaskState.PASSWORD_REQUIRED,
        JobStatus.NOT_ARCHIVE: TaskState.NOT_ARCHIVE,
        JobStatus.BLOCKED: TaskState.BLOCKED,
        JobStatus.FAILED: TaskState.FAILED,
    }[status]


def _path_key(path: Path) -> str:
    return str(path).casefold()


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_json_atomically(path: Path, payload: object) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
