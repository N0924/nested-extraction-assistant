"""原始字节切分卷只能在工作目录中按编号拼接。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from split_volume_joiner import (
    VolumeJoinError,
    join_inferred_first_volume,
    join_numbered_extension_volumes,
)


class SplitVolumeJoinerTests(unittest.TestCase):
    def test_joins_members_in_numeric_order_without_modifying_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            parts = [source / name for name in ("movie.7z.003", "movie.7z.001", "movie.7z.002")]
            payloads = {
                "movie.7z.001": b"first",
                "movie.7z.002": b"second",
                "movie.7z.003": b"third",
            }
            for part in parts:
                part.write_bytes(payloads[part.name])
            originals = {part: part.read_bytes() for part in parts}

            joined = join_numbered_extension_volumes(
                parts,
                root / "work",
                max_total_bytes=100,
            )

            self.assertEqual(joined.name, "movie.7z")
            self.assertEqual(joined.read_bytes(), b"firstsecondthird")
            self.assertEqual({part: part.read_bytes() for part in parts}, originals)

    def test_missing_middle_volume_is_blocked_before_writing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "movie.7z.001"
            third = root / "movie.7z.003"
            first.write_bytes(b"first")
            third.write_bytes(b"third")

            with self.assertRaises(VolumeJoinError):
                join_numbered_extension_volumes(
                    [first, third],
                    root / "work",
                    max_total_bytes=100,
                )

            self.assertFalse((root / "work" / "movie.7z").exists())

    def test_size_limit_is_checked_before_writing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parts = [root / "movie.zip.001", root / "movie.zip.002"]
            for part in parts:
                part.write_bytes(b"12345")

            with self.assertRaises(VolumeJoinError):
                join_numbered_extension_volumes(
                    parts,
                    root / "work",
                    max_total_bytes=9,
                )

            self.assertFalse((root / "work" / "movie.zip").exists())

    def test_zero_size_limit_allows_the_join(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parts = [root / "movie.zip.001", root / "movie.zip.002"]
            parts[0].write_bytes(b"first")
            parts[1].write_bytes(b"second")

            joined = join_numbered_extension_volumes(
                parts,
                root / "work",
                max_total_bytes=0,
            )

            self.assertEqual(joined.read_bytes(), b"firstsecond")

    def test_joins_disguised_first_file_before_002_companions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            first = source / "bundle.mp4"
            second = source / "bundle.002"
            third = source / "bundle.003"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            third.write_bytes(b"third")
            originals = tuple(path.read_bytes() for path in (first, second, third))

            joined = join_inferred_first_volume(
                first,
                (third, first, second),
                root / "work",
                max_total_bytes=100,
            )

            self.assertEqual(joined.read_bytes(), b"firstsecondthird")
            self.assertEqual(
                tuple(path.read_bytes() for path in (first, second, third)),
                originals,
            )

    def test_inferred_group_rejects_a_missing_002_volume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "bundle.mp4"
            third = root / "bundle.003"
            first.write_bytes(b"first")
            third.write_bytes(b"third")

            with self.assertRaises(VolumeJoinError):
                join_inferred_first_volume(
                    first,
                    (first, third),
                    root / "work",
                    max_total_bytes=100,
                )


if __name__ == "__main__":
    unittest.main()
