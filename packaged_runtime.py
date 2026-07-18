"""在打包程序导入 Tkinter 前修正运行环境。"""

from __future__ import annotations

import os
import sys


def prepare_packaged_tk() -> None:
    """移除 Conda 路径覆盖，让 Tcl/Tk 使用打包目录中的运行库。"""
    if not getattr(sys, "frozen", False):
        return
    os.environ.pop("TCL_LIBRARY", None)
    os.environ.pop("TK_LIBRARY", None)
