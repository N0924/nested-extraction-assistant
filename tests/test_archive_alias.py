"""错误扩展名压缩包工作副本的安全测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archive_alias import copy_archive_alias, detect_archive_suffix


class ArchiveAliasTests(unittest.TestCase):
    def test_detects_supported_archive_signatures(self) -> None:
        samples = (
            (b"PK\x03\x04payload", ".zip"),
            (b"Rar!\x1a\x07\x00payload", ".rar"),
            (b"Rar!\x1a\x07\x01\x00payload", ".rar"),
            (b"7z\xbc\xaf'\x1cpayload", ".7z"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, (payload, expected) in enumerate(samples):
                source = root / f"sample-{index}.txt"
                source.write_bytes(payload)
                self.assertEqual(detect_archive_suffix(source), expected)

    def test_does_not_treat_an_ordinary_mp4_as_an_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "movie.mp4"
            source.write_bytes(b"\x00\x00\x00\x18ftypmp42ordinary video")

            self.assertIsNone(detect_archive_suffix(source))

    def test_copies_to_work_directory_without_modifying_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "archive.txt"
            payload = b"PK\x03\x04payload"
            source.write_bytes(payload)

            destination = copy_archive_alias(source, root / "work", ".zip")

            self.assertEqual(destination.name, "archive.zip")
            self.assertEqual(destination.read_bytes(), payload)
            self.assertEqual(source.name, "archive.txt")
            self.assertEqual(source.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
