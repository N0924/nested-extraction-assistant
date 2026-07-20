"""在打包程序导入 Tkinter 前确认其运行库路径。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def prepare_packaged_tk() -> None:
    """通过相对路径加载 PyInstaller 放在 ``_internal`` 中的运行库。"""
    if not getattr(sys, "frozen", False):
        return

    bundle_directory = Path(sys._MEIPASS)
    tcl_directory = bundle_directory / "_tcl_data"
    tk_directory = bundle_directory / "_tk_data"

    if not tcl_directory.is_dir() or not tk_directory.is_dir():
        return

    application_directory = bundle_directory.parent
    os.chdir(application_directory)
    os.environ["TCL_LIBRARY"] = tcl_directory.relative_to(
        application_directory
    ).as_posix()
    os.environ["TK_LIBRARY"] = tk_directory.relative_to(
        application_directory
    ).as_posix()
