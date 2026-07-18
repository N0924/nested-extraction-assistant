"""带 MP4/TXT 等前置外壳的嵌入式 ZIP 检测测试。"""

from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from embedded_zip import copy_embedded_zip, find_embedded_zip


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("payload.txt", "embedded payload")
    return buffer.getvalue()


class EmbeddedZipTests(unittest.TestCase):
    def test_finds_zip_inside_multiple_outer_file_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = b"\x00\x00\x00\x1cftypisom" + b"video-mask" * 20
            archive = _zip_bytes()
            trailer = b"\x00\x00\x00\x08free"
            for extension in ("mp4", "txt", "pdf", "jpg"):
                with self.subTest(extension=extension):
                    source = root / f"disguised.{extension}"
                    source.write_bytes(prefix + archive + trailer)
                    original = source.read_bytes()

                    info = find_embedded_zip(source)

                    self.assertIsNotNone(info)
                    assert info is not None
                    self.assertEqual(info.prefix_size, len(prefix))
                    self.assertEqual(info.archive_size, len(archive))
                    destination = copy_embedded_zip(
                        source,
                        root / "work" / f"embedded-{extension}.zip",
                        info,
                    )
                    self.assertEqual(destination.read_bytes(), archive)
                    self.assertTrue(zipfile.is_zipfile(destination))
                    self.assertEqual(source.read_bytes(), original)

    def test_rejects_normal_mp4_with_incidental_pk_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "normal.mp4"
            source.write_bytes(
                b"\x00\x00\x00\x1cftypisom"
                + b"PK\x03\x04random"
                + b"\x00\x00\x00\x08free"
            )

            self.assertIsNone(find_embedded_zip(source))


if __name__ == "__main__":
    unittest.main()
