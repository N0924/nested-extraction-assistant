"""多任务队列、设置和历史存储测试。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from extraction_job import ExtractionJobResult, JobStatus
from task_queue import (
    AppSettings,
    HistoryStore,
    QueueScheduler,
    SettingsStore,
    TaskCollection,
    TaskState,
    default_state_directory,
)


class StateDirectoryTests(unittest.TestCase):
    def test_packaged_app_uses_local_app_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.dict(os.environ, {"LOCALAPPDATA": directory}),
            ):
                state_directory = default_state_directory()

        self.assertEqual(
            state_directory,
            Path(directory) / "NestedExtractionAssistant" / "state",
        )


class TaskCollectionTests(unittest.TestCase):
    def test_each_task_keeps_an_independent_password(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.zip"
            second = root / "second.rar"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            tasks = TaskCollection()

            added = tasks.add_files([first, second])
            tasks.set_password(added[0].id, "first-password")
            tasks.set_password(added[1].id, "second-password")

            self.assertEqual(tasks.get(added[0].id).password, "first-password")
            self.assertEqual(tasks.get(added[1].id).password, "second-password")

    def test_does_not_add_the_same_pending_file_twice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "same.zip"
            source.write_bytes(b"data")
            tasks = TaskCollection()

            first = tasks.add_files([source])
            second = tasks.add_files([source])

            self.assertEqual(len(first), 1)
            self.assertEqual(second, [])

    def test_running_task_cannot_be_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "running.zip"
            source.write_bytes(b"data")
            tasks = TaskCollection()
            task = tasks.add_files([source])[0]
            tasks.mark_running(task.id)

            removed = tasks.remove(task.id)

            self.assertFalse(removed)
            self.assertIsNotNone(tasks.get(task.id))

    def test_job_result_updates_the_matching_task_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.zip"
            second = root / "second.zip"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            tasks = TaskCollection()
            added = tasks.add_files([first, second])
            output = root / "result"
            output.mkdir()

            tasks.apply_result(
                added[0].id,
                ExtractionJobResult(
                    JobStatus.COMPLETED,
                    "完成",
                    output_directory=output,
                ),
            )

            self.assertEqual(tasks.get(added[0].id).state, TaskState.COMPLETED)
            self.assertEqual(tasks.get(added[1].id).state, TaskState.PENDING)


class HistoryStoreTests(unittest.TestCase):
    def test_history_never_writes_task_passwords(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "private.zip"
            source.write_bytes(b"data")
            tasks = TaskCollection()
            task = tasks.add_files([source])[0]
            tasks.set_password(task.id, "must-not-be-saved")
            store_path = root / "history.json"

            HistoryStore(store_path).save(tasks.tasks)

            text = store_path.read_text(encoding="utf-8")
            self.assertNotIn("must-not-be-saved", text)
            self.assertNotIn('"password"', text)

    def test_running_history_is_loaded_as_interrupted_without_password(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "running.zip"
            source.write_bytes(b"data")
            tasks = TaskCollection()
            task = tasks.add_files([source])[0]
            tasks.set_password(task.id, "temporary")
            tasks.mark_running(task.id)
            store = HistoryStore(root / "history.json")
            store.save(tasks.tasks)

            restored = store.load()

            self.assertEqual(restored[0].state, TaskState.INTERRUPTED)
            self.assertEqual(restored[0].password, "")

    def test_completed_history_without_output_is_marked_result_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "archive.zip"
            source.write_bytes(b"data")
            tasks = TaskCollection()
            task = tasks.add_files([source])[0]
            task.state = TaskState.COMPLETED
            task.output_directory = root / "missing-output"
            store = HistoryStore(root / "history.json")
            store.save(tasks.tasks)

            restored = store.load()

            self.assertEqual(restored[0].state, TaskState.RESULT_MISSING)
            self.assertIn("结果目录", restored[0].progress)


class SettingsStoreTests(unittest.TestCase):
    def test_default_limits_are_unlimited_and_output_name_is_current(self) -> None:
        settings = AppSettings.defaults()

        self.assertEqual(
            (settings.max_files, settings.max_total_gb, settings.max_archives),
            (0, 0, 0),
        )
        self.assertEqual(settings.max_concurrency, 1)
        self.assertEqual(Path(settings.output_root).name, "输出")

    def test_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            settings = AppSettings(
                work_root="C:/work",
                output_root="D:/output",
                winrar_path="C:/WinRAR/WinRAR.exe",
                max_files=200,
                max_total_gb=50,
                max_archives=20,
                max_concurrency=2,
            )
            store = SettingsStore(path)

            store.save(settings)
            restored = store.load(AppSettings.defaults())

            self.assertEqual(restored, settings)

    def test_invalid_settings_file_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("not-json", encoding="utf-8")
            defaults = AppSettings.defaults()

            restored = SettingsStore(path).load(defaults)

            self.assertEqual(restored, defaults)

    def test_non_object_settings_file_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("[]", encoding="utf-8")
            defaults = AppSettings.defaults()

            restored = SettingsStore(path).load(defaults)

            self.assertEqual(restored, defaults)

    def test_legacy_default_limits_migrate_to_unlimited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "work_root": "C:/work",
                        "output_root": "D:/custom-output",
                        "winrar_path": "C:/WinRAR/WinRAR.exe",
                        "max_files": 10_000,
                        "max_total_gb": 100,
                        "max_archives": 1_000,
                        "max_concurrency": 1,
                    }
                ),
                encoding="utf-8",
            )

            restored = SettingsStore(path).load(AppSettings.defaults())

            self.assertEqual(
                (restored.max_files, restored.max_total_gb, restored.max_archives),
                (0, 0, 0),
            )
            self.assertEqual(restored.output_root, "D:/custom-output")


class QueueSchedulerTests(unittest.TestCase):
    def test_single_concurrency_starts_the_next_task_after_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = [root / "one.zip", root / "two.zip"]
            for path in files:
                path.write_bytes(b"data")
            tasks = TaskCollection()
            added = tasks.add_files(files)
            scheduler = QueueScheduler(tasks, max_concurrency=1)

            first_batch = scheduler.begin()
            second_batch = scheduler.complete(
                added[0].id,
                ExtractionJobResult(JobStatus.FAILED, "failed"),
            )

            self.assertEqual([task.id for task in first_batch], [added[0].id])
            self.assertEqual([task.id for task in second_batch], [added[1].id])

    def test_double_concurrency_claims_two_different_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = [root / f"{index}.zip" for index in range(3)]
            for path in files:
                path.write_bytes(b"data")
            tasks = TaskCollection()
            added = tasks.add_files(files)
            scheduler = QueueScheduler(tasks, max_concurrency=2)

            first_batch = scheduler.begin()

            self.assertEqual([task.id for task in first_batch], [added[0].id, added[1].id])
            self.assertEqual(len(scheduler.active_ids), 2)

    def test_five_concurrent_tasks_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = [root / f"{index}.zip" for index in range(6)]
            for path in files:
                path.write_bytes(b"data")
            tasks = TaskCollection()
            added = tasks.add_files(files)
            scheduler = QueueScheduler(tasks, max_concurrency=5)

            claimed = scheduler.begin()

            self.assertEqual([task.id for task in claimed], [task.id for task in added[:5]])
            self.assertEqual(len(scheduler.active_ids), 5)

    def test_selected_start_does_not_claim_unselected_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = [root / f"{index}.zip" for index in range(3)]
            for path in files:
                path.write_bytes(b"data")
            tasks = TaskCollection()
            added = tasks.add_files(files)
            scheduler = QueueScheduler(tasks, max_concurrency=1)

            first_batch = scheduler.begin([added[1].id, added[2].id])
            second_batch = scheduler.complete(
                added[1].id,
                ExtractionJobResult(JobStatus.FAILED, "failed"),
            )
            final_batch = scheduler.complete(
                added[2].id,
                ExtractionJobResult(JobStatus.FAILED, "failed"),
            )

            self.assertEqual([task.id for task in first_batch], [added[1].id])
            self.assertEqual([task.id for task in second_batch], [added[2].id])
            self.assertEqual(final_batch, [])
            self.assertEqual(added[0].state, TaskState.PENDING)
            self.assertFalse(scheduler.enabled)

    def test_starting_all_after_selected_mode_claims_remaining_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = [root / f"{index}.zip" for index in range(2)]
            for path in files:
                path.write_bytes(b"data")
            tasks = TaskCollection()
            added = tasks.add_files(files)
            scheduler = QueueScheduler(tasks, max_concurrency=1)

            selected = scheduler.begin([added[1].id])
            scheduler.complete(
                added[1].id,
                ExtractionJobResult(JobStatus.FAILED, "failed"),
            )
            remaining = scheduler.begin()

            self.assertEqual([task.id for task in selected], [added[1].id])
            self.assertEqual([task.id for task in remaining], [added[0].id])


if __name__ == "__main__":
    unittest.main()
