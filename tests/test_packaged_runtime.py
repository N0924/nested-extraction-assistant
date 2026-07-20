"""验证打包程序始终使用便携包自带的 Tcl/Tk 运行库。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from packaged_runtime import prepare_packaged_tk


class PackagedRuntimeTests(unittest.TestCase):
    def test_frozen_app_uses_bundled_tcl_and_tk_directories(self) -> None:
        with tempfile.TemporaryDirectory(prefix="嵌套解压助手-中文路径-") as temporary:
            application_directory = Path(temporary)
            bundle_directory = application_directory / "_internal"
            tcl_directory = bundle_directory / "_tcl_data"
            tk_directory = bundle_directory / "_tk_data"
            tcl_directory.mkdir(parents=True)
            tk_directory.mkdir()

            environment = {
                "TCL_LIBRARY": "stale-conda-tcl-path",
                "TK_LIBRARY": "stale-conda-tk-path",
            }
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "_MEIPASS", str(bundle_directory), create=True),
                patch.dict(os.environ, environment, clear=True),
                patch("packaged_runtime.os.chdir") as change_directory,
            ):
                prepare_packaged_tk()

                change_directory.assert_called_once_with(application_directory)
                self.assertEqual(os.environ["TCL_LIBRARY"], "_internal/_tcl_data")
                self.assertEqual(os.environ["TK_LIBRARY"], "_internal/_tk_data")

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
