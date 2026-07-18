"""密码池和行级任务密码在表格中的行为。"""

from __future__ import annotations

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from extraction_job import ExtractionJobResult, JobStatus
from password_vault import PasswordVaultStore
from task_queue import TaskState
from ui.main_window import MainWindow
from ui.password_dialogs import PasswordPoolDialog, _display_password


class PasswordUiTests(unittest.TestCase):
    def test_table_distinguishes_saved_pool_from_task_specific_password(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            state_path = root_path / "state"
            vault = PasswordVaultStore(state_path / "password-vault.json")
            vault.save(["default-password", "backup-password"])
            source = root_path / "source.rar"
            source.write_bytes(b"synthetic")

            tk_root = tk.Tk()
            tk_root.withdraw()
            with patch("ui.main_window.default_state_directory", return_value=state_path):
                window = MainWindow(tk_root)
            window._add_paths([str(source)])
            task = window.tasks.tasks[0]

            self.assertEqual(window.table.item(task.id, "values")[3], "密码池 2 个")
            self.assertTrue(window._apply_task_password(task.id, "task-password", False))
            self.assertEqual(window.table.item(task.id, "values")[3], "task-password")
            window._on_close()
            vault.save([])

    def test_password_cell_uses_an_in_place_plaintext_editor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            source = root_path / "source.rar"
            source.write_bytes(b"synthetic")
            tk_root = tk.Tk()
            with patch("ui.main_window.default_state_directory", return_value=root_path / "state"):
                window = MainWindow(tk_root)
            window._add_paths([str(source)])
            task = window.tasks.tasks[0]
            tk_root.update()

            window._begin_password_edit(task.id)

            self.assertIsNotNone(window._password_editor)
            self.assertEqual(window._password_editor.cget("show"), "")
            window._password_editor.insert(0, "row-password")
            window._commit_password_editor()
            self.assertEqual(task.password, "row-password")
            self.assertEqual(window.table.item(task.id, "values")[3], "row-password")
            window._on_close()

    def test_saved_passwords_are_displayed_as_plaintext(self) -> None:
        self.assertEqual(_display_password("12345"), "12345")

    def test_password_pool_initial_height_fits_all_controls(self) -> None:
        tk_root = tk.Tk()
        tk_root.geometry("1x1+0+0")
        dialog = PasswordPoolDialog(tk_root, ["default", "backup"], lambda _values: True)
        tk_root.update()

        self.assertGreaterEqual(dialog.window.winfo_height(), dialog.window.winfo_reqheight())

        dialog.window.destroy()
        tk_root.destroy()

    def test_table_supports_multiple_selected_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            sources = [root_path / "first.rar", root_path / "second.rar"]
            for source in sources:
                source.write_bytes(b"synthetic")
            tk_root = tk.Tk()
            tk_root.withdraw()
            with patch("ui.main_window.default_state_directory", return_value=root_path / "state"):
                window = MainWindow(tk_root)
            window._add_paths([str(source) for source in sources])
            task_ids = [task.id for task in window.tasks.tasks]

            window.table.selection_set(task_ids)

            self.assertEqual(str(window.table.cget("selectmode")), "extended")
            self.assertEqual([task.id for task in window._selected_tasks()], task_ids)
            window._on_close()

    def test_start_preparation_retries_a_password_required_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            source = root_path / "source.rar"
            source.write_bytes(b"synthetic")
            tk_root = tk.Tk()
            tk_root.withdraw()
            with patch("ui.main_window.default_state_directory", return_value=root_path / "state"):
                window = MainWindow(tk_root)
            window._add_paths([str(source)])
            task = window.tasks.tasks[0]
            window.tasks.apply_result(
                task.id,
                ExtractionJobResult(JobStatus.PASSWORD_REQUIRED, "需要密码"),
            )

            prepared = window._prepare_startable_task_ids([task])

            self.assertEqual(prepared, [task.id])
            self.assertEqual(task.state, TaskState.PENDING)
            window._on_close()


if __name__ == "__main__":
    unittest.main()
