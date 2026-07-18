"""验证程序图标在源码和打包环境中均可使用。"""

from __future__ import annotations

import struct
import unittest

from ui.app_assets import APP_ICON_NAME, APP_LOGO_NAME, ASSET_DIRECTORY, resource_path


class AppAssetTests(unittest.TestCase):
    def test_resource_path_finds_source_assets(self) -> None:
        self.assertTrue(resource_path(ASSET_DIRECTORY, APP_ICON_NAME).is_file())
        self.assertTrue(resource_path(ASSET_DIRECTORY, APP_LOGO_NAME).is_file())

    def test_icon_contains_required_windows_sizes(self) -> None:
        data = resource_path(ASSET_DIRECTORY, APP_ICON_NAME).read_bytes()
        reserved, image_type, count = struct.unpack_from("<HHH", data, 0)
        self.assertEqual((reserved, image_type), (0, 1))

        sizes: set[tuple[int, int]] = set()
        for index in range(count):
            offset = 6 + index * 16
            width = data[offset] or 256
            height = data[offset + 1] or 256
            sizes.add((width, height))

        self.assertEqual(
            sizes,
            {
                (16, 16),
                (24, 24),
                (32, 32),
                (48, 48),
                (64, 64),
                (128, 128),
                (256, 256),
            },
        )

    def test_about_logo_is_128_pixel_png(self) -> None:
        data = resource_path(ASSET_DIRECTORY, APP_LOGO_NAME).read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", data[16:24]), (128, 128))


if __name__ == "__main__":
    unittest.main()
