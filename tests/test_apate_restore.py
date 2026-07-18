"""Apate 兼容还原的无私密测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from apate_restore import RestoreStatus, looks_like_apate_archive, restore_apate_copy


def _make_apate_like_file(original: bytes, mask: bytes) -> bytes:
    """按 Apate 已公开的数据布局生成测试文件，不使用真实私人样本。"""

    disguised = bytearray(original)
    disguised[: len(mask)] = mask
    disguised.extend(original[: len(mask)][::-1])
    disguised.extend(len(mask).to_bytes(4, byteorder="little"))
    return bytes(disguised)


class RestoreApateCopyTests(unittest.TestCase):
    def test_recognizes_an_apate_disguised_archive_before_writing_a_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "masked.txt"
            source.write_bytes(_make_apate_like_file(b"PK\x03\x04archive payload", b"mask"))

            self.assertTrue(looks_like_apate_archive(source))

    def test_does_not_treat_an_ordinary_file_as_an_apate_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ordinary.txt"
            source.write_bytes(b"ordinary file content")

            self.assertFalse(looks_like_apate_archive(source))

    def test_restores_a_separate_copy_and_preserves_source(self) -> None:
        original = b"PK\x03\x04test archive payload with enough content"
        mask = b"not-a-zip-header"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "masked.mp4"
            destination = root / "restored.bin"
            disguised = _make_apate_like_file(original, mask)
            source.write_bytes(disguised)

            result = restore_apate_copy(source, destination)

            self.assertEqual(result.status, RestoreStatus.RESTORED)
            self.assertEqual(destination.read_bytes(), original)
            self.assertEqual(source.read_bytes(), disguised)

    def test_rejects_an_implausible_tail_without_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "normal.txt"
            destination = root / "output.bin"
            source.write_bytes(b"ordinary file" + (9_999_999).to_bytes(4, byteorder="little"))

            result = restore_apate_copy(source, destination)

            self.assertEqual(result.status, RestoreStatus.NOT_PLAUSIBLE)
            self.assertFalse(destination.exists())

    def test_restores_when_mask_is_longer_than_the_original_file(self) -> None:
        """Apate 的自定义掩码可能大于原始文件，需要依靠最终截断保留正确内容。"""

        original = b"small original file"
        mask = b"x" * 128

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "masked.mp4"
            destination = root / "restored.bin"
            source.write_bytes(_make_apate_like_file(original, mask))

            result = restore_apate_copy(source, destination)

            self.assertEqual(result.status, RestoreStatus.RESTORED)
            self.assertEqual(destination.read_bytes(), original)

    def test_never_overwrites_an_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "masked.mp4"
            destination = root / "already-here.bin"
            source.write_bytes(_make_apate_like_file(b"original content", b"mask"))
            destination.write_bytes(b"keep this file")

            result = restore_apate_copy(source, destination)

            self.assertEqual(result.status, RestoreStatus.BLOCKED)
            self.assertEqual(destination.read_bytes(), b"keep this file")


if __name__ == "__main__":
    unittest.main()
