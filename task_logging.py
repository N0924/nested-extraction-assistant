"""为每次队列任务写入可读日志和结构化事件日志。"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path


TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_GLOBAL_LOG_LOCK = threading.Lock()


class TaskLogWriter:
    """向同一任务目录追加 ``task.log`` 和 ``task-events.jsonl``。"""

    def __init__(self, log_root: str | Path, task_id: str) -> None:
        if TASK_ID_PATTERN.fullmatch(task_id) is None:
            raise ValueError("任务日志标识无效。")
        self.task_id = task_id
        self.directory = Path(log_root) / task_id
        self.text_path = self.directory / "task.log"
        self.events_path = self.directory / "task-events.jsonl"
        self.global_text_path = Path(log_root) / "all-tasks.log"
        self.global_events_path = Path(log_root) / "all-task-events.jsonl"
        self.last_error: str | None = None
        self._lock = threading.Lock()

    def record(
        self,
        event: str,
        message: str,
        *,
        level: str = "INFO",
        **details: object,
    ) -> bool:
        """追加一条事件；日志失败不会中断解压任务。"""

        timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        payload = {
            "timestamp": timestamp,
            "level": level,
            "task_id": self.task_id,
            "event": event,
            "message": message,
            "details": details,
        }
        detail_text = (
            f" | {json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)}"
            if details
            else ""
        )
        text_line = f"{timestamp} [{level}] {event}: {message}{detail_text}\n"
        global_text_line = (
            f"{timestamp} [{level}] [{self.task_id}] {event}: {message}{detail_text}\n"
        )
        json_line = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) + "\n"

        try:
            with self._lock:
                self.directory.mkdir(parents=True, exist_ok=True)
                with self.text_path.open("a", encoding="utf-8", newline="") as stream:
                    stream.write(text_line)
                with self.events_path.open("a", encoding="utf-8", newline="") as stream:
                    stream.write(json_line)
            with _GLOBAL_LOG_LOCK:
                with self.global_text_path.open("a", encoding="utf-8", newline="") as stream:
                    stream.write(global_text_line)
                with self.global_events_path.open("a", encoding="utf-8", newline="") as stream:
                    stream.write(json_line)
            self.last_error = None
            return True
        except (OSError, TypeError) as error:
            self.last_error = str(error)
            return False
