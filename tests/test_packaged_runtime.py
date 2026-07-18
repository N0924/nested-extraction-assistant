"""验证打包程序启动时不会继承不兼容的 Conda Tcl/Tk 路径。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

from packaged_runtime import prepare_packaged_tk


class PackagedRuntimeTests(unittest.TestCase):
    def test_frozen_app_removes_tcl_environment_overrides(self) -> None:
        environment = {
            "TCL_LIBRARY": "temporary-tcl-path",
            "TK_LIBRARY": "temporary-tk-path",
        }
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.dict(os.environ, environment, clear=True),
        ):
            prepare_packaged_tk()

            self.assertNotIn("TCL_LIBRARY", os.environ)
            self.assertNotIn("TK_LIBRARY", os.environ)

    def test_source_run_keeps_existing_environment(self) -> None:
        environment = {
            "TCL_LIBRARY": "source-tcl-path",
            "TK_LIBRARY": "source-tk-path",
        }
        with patch.object(sys, "frozen", False, create=True), patch.dict(
            os.environ, environment, clear=True
        ):
            prepare_packaged_tk()

            self.assertEqual(os.environ["TCL_LIBRARY"], "source-tcl-path")
            self.assertEqual(os.environ["TK_LIBRARY"], "source-tk-path")


if __name__ == "__main__":
    unittest.main()
