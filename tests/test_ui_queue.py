"""多任务表格通过真实 WinRAR 运行不同密码任务。"""

from __future__ import annotations

import subprocess
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from task_queue import AppSettings, TaskState
from ui.main_window import MainWindow
from winrar_adapter import find_winrar


@unittest.skipUnless(find_winrar() is not None, "本机未安装 WinRAR")
class QueueUiIntegrationTests(unittest.TestCase):
    def test_two_encrypted_tasks_use_their_own_passwords(self) -> None:
        rar_executable = find_winrar().with_name("Rar.exe")
        if not rar_executable.is_file():
            self.skipTest("本机未安装 Rar.exe")

        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            source_dir = root_path / "source"
            source_dir.mkdir()
            archives: list[Path] = []
            for name, password in (("first", "alpha-password"), ("second", "beta-password")):
                payload = source_dir / f"{name}.txt"
                payload.write_text(f"{name} content", encoding="utf-8")
                archive = source_dir / f"{name}.rar"
                created = subprocess.run(
                    [
                        str(rar_executable),
                        "a",
                        "-inul",
                        f"-hp{password}",
                        str(archive),
                        payload.name,
                    ],
                    cwd=source_dir,
                    check=False,
                    shell=False,
                )
                self.assertEqual(created.returncode, 0)
                archives.append(archive)

            state_dir = root_path / "state"
            tk_root = tk.Tk()
            tk_root.withdraw()
            with patch("ui.main_window.default_state_directory", return_value=state_dir):
                window = MainWindow(tk_root)
            window.settings = AppSettings(
                work_root=str(root_path / "work"),
                output_root=str(root_path / "output"),
                winrar_path=str(find_winrar()),
                max_files=100,
                max_total_gb=1,
                max_archives=10,
                max_concurrency=2,
            )
            window.scheduler.set_max_concurrency(2)
            window._add_paths([str(path) for path in archives])
            added = window.tasks.tasks
            window.tasks.set_password(added[0].id, "alpha-password")
            window.tasks.set_password(added[1].id, "beta-password")

            window._start_all()
            deadline = time.monotonic() + 15
            while (
                window.scheduler.active_ids or window.tasks.pending()
            ) and time.monotonic() < deadline:
                tk_root.update()
                time.sleep(0.02)
            tk_root.update()

            self.assertEqual(
                [task.state for task in added],
                [TaskState.COMPLETED, TaskState.COMPLETED],
            )
            self.assertTrue(all(task.output_directory is not None for task in added))
            self.assertEqual(added[0].password, "")
            self.assertEqual(added[1].password, "")
            for task, password in zip(added, ("alpha-password", "beta-password"), strict=True):
                log_path = root_path / "logs" / task.id / "task.log"
                self.assertTrue(log_path.is_file())
                self.assertIn(password, log_path.read_text(encoding="utf-8"))
            global_log = root_path / "logs" / "all-tasks.log"
            self.assertIn("alpha-password", global_log.read_text(encoding="utf-8"))
            self.assertIn("beta-password", global_log.read_text(encoding="utf-8"))
            window._on_close()


if __name__ == "__main__":
    unittest.main()
