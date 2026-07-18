"""任务日志必须同时提供全局汇总和单任务明细。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from task_logging import TaskLogWriter


class TaskLogWriterTests(unittest.TestCase):
    def test_writes_text_and_jsonl_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            writer = TaskLogWriter(Path(directory), "task-123")

            self.assertTrue(
                writer.record(
                    "run_started",
                    "任务开始",
                    source="D:/sample.zip",
                    max_files=0,
                )
            )

            self.assertIn("run_started: 任务开始", writer.text_path.read_text(encoding="utf-8"))
            event = json.loads(writer.events_path.read_text(encoding="utf-8"))
            self.assertEqual(event["event"], "run_started")
            self.assertEqual(event["details"]["max_files"], 0)
            self.assertIn("[task-123]", writer.global_text_path.read_text(encoding="utf-8"))
            global_event = json.loads(writer.global_events_path.read_text(encoding="utf-8"))
            self.assertEqual(global_event["task_id"], "task-123")

    def test_password_fields_are_written_in_plaintext_by_user_choice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            writer = TaskLogWriter(Path(directory), "task-456")

            writer.record(
                "progress",
                "正在尝试候选",
                password="secret-value",
                password_pool="also-secret",
                candidate_index=2,
            )

            combined = writer.text_path.read_text(encoding="utf-8") + writer.events_path.read_text(
                encoding="utf-8"
            )
            self.assertIn("secret-value", combined)
            self.assertIn("also-secret", combined)
            self.assertIn("candidate_index", combined)
            self.assertIn("secret-value", writer.global_text_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
