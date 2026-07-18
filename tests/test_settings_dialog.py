"""设置窗口的不限额输入规则。"""

from __future__ import annotations

import unittest

from ui.settings_dialog import _optional_positive_int


class OptionalLimitTests(unittest.TestCase):
    def test_unlimited_checkbox_stores_zero(self) -> None:
        self.assertEqual(_optional_positive_int("", True, "最大文件数"), 0)

    def test_custom_limit_requires_a_positive_integer(self) -> None:
        self.assertEqual(_optional_positive_int("25", False, "最大文件数"), 25)
        with self.assertRaises(ValueError):
            _optional_positive_int("0", False, "最大文件数")


if __name__ == "__main__":
    unittest.main()
