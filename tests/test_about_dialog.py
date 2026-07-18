"""关于窗口必须包含项目身份并能打开真实项目地址。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from ui.about_dialog import (
    APP_NAME,
    APP_VERSION,
    PROJECT_URL,
    open_project_page,
)


class AboutContentTests(unittest.TestCase):
    def test_contains_project_identity(self) -> None:
        self.assertEqual(APP_NAME, "嵌套解压助手")
        self.assertTrue(APP_VERSION)
        self.assertEqual(
            PROJECT_URL,
            "https://github.com/N0924/nested-extraction-assistant",
        )

    @patch("ui.about_dialog.webbrowser.open_new_tab")
    def test_project_page_uses_default_browser(self, open_new_tab) -> None:
        open_project_page()

        open_new_tab.assert_called_once_with(PROJECT_URL)


if __name__ == "__main__":
    unittest.main()
