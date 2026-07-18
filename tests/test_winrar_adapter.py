"""WinRAR 后台调用适配层测试。"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from winrar_adapter import WinRARAdapter, WinRARStatus, find_winrar


class WinRARAdapterUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executable = Path(r"C:\Program Files\WinRAR\WinRAR.exe")

    @patch("winrar_adapter.subprocess.run")
    def test_maps_password_exit_code_without_exposing_password(self, run_mock: object) -> None:
        run_mock.return_value = subprocess.CompletedProcess([], 11)
        adapter = WinRARAdapter(self.executable)

        result = adapter.test_archive("sample.rar", "private-password")

        self.assertEqual(result.status, WinRARStatus.PASSWORD_REQUIRED)
        self.assertNotIn("private-password", result.message)
        command = run_mock.call_args.args[0]
        self.assertIn("-pprivate-password", command)
        self.assertFalse(run_mock.call_args.kwargs["shell"])

    @patch("winrar_adapter.subprocess.run")
    def test_uses_no_prompt_password_switch_when_password_is_empty(self, run_mock: object) -> None:
        run_mock.return_value = subprocess.CompletedProcess([], 0)
        adapter = WinRARAdapter(self.executable)

        result = adapter.test_archive("sample.zip", "")

        self.assertEqual(result.status, WinRARStatus.VALID)
        self.assertIn("-p-", run_mock.call_args.args[0])

    @patch("winrar_adapter.subprocess.run")
    def test_blocks_extracting_into_a_nonempty_directory(self, run_mock: object) -> None:
        adapter = WinRARAdapter(self.executable)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "output"
            destination.mkdir()
            (destination / "keep.txt").write_text("keep", encoding="utf-8")

            result = adapter.extract_archive(root / "sample.zip", destination, "")

        self.assertEqual(result.status, WinRARStatus.BLOCKED)
        run_mock.assert_not_called()

    @patch("winrar_adapter.subprocess.run")
    def test_blocks_extracting_when_the_destination_is_a_file(self, run_mock: object) -> None:
        adapter = WinRARAdapter(self.executable)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "occupied"
            destination.write_text("not a directory", encoding="utf-8")

            result = adapter.extract_archive("sample.zip", destination, "")

        self.assertEqual(result.status, WinRARStatus.BLOCKED)
        run_mock.assert_not_called()

    @patch("winrar_adapter.subprocess.run")
    def test_rejects_a_password_longer_than_winrar_supports(self, run_mock: object) -> None:
        adapter = WinRARAdapter(self.executable)

        result = adapter.test_archive("sample.rar", "x" * 128)

        self.assertEqual(result.status, WinRARStatus.BLOCKED)
        run_mock.assert_not_called()


class WinRARAdapterIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(find_winrar() is not None, "本机未安装 WinRAR")
    def test_extracts_a_zip_even_when_it_has_an_mp4_suffix(self) -> None:
        adapter = WinRARAdapter(find_winrar())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "disguised.mp4"
            destination = root / "output"
            with zipfile.ZipFile(archive, "w") as zip_file:
                zip_file.writestr("folder/result.txt", "synthetic content")

            check = adapter.test_archive(archive, "")
            extraction = adapter.extract_archive(archive, destination, "")

            self.assertEqual(check.status, WinRARStatus.VALID)
            self.assertEqual(extraction.status, WinRARStatus.EXTRACTED)
            self.assertEqual(
                (destination / "folder" / "result.txt").read_text(encoding="utf-8"),
                "synthetic content",
            )


if __name__ == "__main__":
    unittest.main()
