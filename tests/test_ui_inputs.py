"""界面把文件夹和分卷转换为清晰的根任务。"""

from __future__ import annotations

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from ui.main_window import MainWindow


class QueueInputUiTests(unittest.TestCase):
    def test_folder_with_numbered_7z_volumes_creates_only_the_001_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            source = root_path / "source"
            source.mkdir()
            volumes = [source / f"452.7z.{number:03d}" for number in range(1, 4)]
            for volume in volumes:
                volume.write_bytes(b"part")

            tk_root = tk.Tk()
            tk_root.withdraw()
            with patch("ui.main_window.default_state_directory", return_value=root_path / "state"):
                window = MainWindow(tk_root)

            window._add_paths([str(source)])

            self.assertEqual([task.source for task in window.tasks.tasks], [volumes[0]])
            window._on_close()


if __name__ == "__main__":
    unittest.main()
