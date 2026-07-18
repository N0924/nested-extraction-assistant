"""Windows 原生文件拖放窗口桥接测试。"""

from __future__ import annotations

import ctypes
import os
import time
import tkinter as tk
import unittest
from ctypes import wintypes
from tkinter import ttk

from windows_file_drop import WM_DROPFILES, WindowsFileDrop


class _DropFilesHeader(ctypes.Structure):
    _fields_ = (
        ("pFiles", wintypes.DWORD),
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
        ("fNC", wintypes.BOOL),
        ("fWide", wintypes.BOOL),
    )


@unittest.skipUnless(os.name == "nt", "仅在 Windows 上验证原生拖放")
class WindowsFileDropTests(unittest.TestCase):
    def test_installs_and_restores_the_widget_window_procedure(self) -> None:
        root = tk.Tk()
        root.withdraw()
        table = ttk.Treeview(root)
        table.pack()
        root.update_idletasks()

        bridge = WindowsFileDrop(table, lambda _paths: None)
        bridge.close()
        bridge.close()

        root.destroy()

    def test_receives_multiple_unicode_paths_from_drop_message(self) -> None:
        root = tk.Tk()
        root.withdraw()
        table = ttk.Treeview(root)
        table.pack()
        root.update_idletasks()
        received: list[list[str]] = []
        bridge = WindowsFileDrop(table, received.append)
        expected = [r"C:\Folder With Space\one.zip", r"D:\中文目录\two.rar"]

        drop_handle = _allocate_drop_handle(expected)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostMessageW.argtypes = (
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        user32.PostMessageW.restype = wintypes.BOOL
        posted = user32.PostMessageW(bridge.hwnd, WM_DROPFILES, drop_handle, 0)
        self.assertTrue(posted)

        deadline = time.monotonic() + 2
        while not received and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)

        self.assertEqual(received, [expected])
        bridge.close()
        root.destroy()


def _allocate_drop_handle(paths: list[str]) -> int:
    header = _DropFilesHeader(
        pFiles=ctypes.sizeof(_DropFilesHeader),
        x=0,
        y=0,
        fNC=False,
        fWide=True,
    )
    names = ("\x00".join(paths) + "\x00\x00").encode("utf-16-le")
    payload = ctypes.string_at(ctypes.byref(header), ctypes.sizeof(header)) + names

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalUnlock.restype = wintypes.BOOL

    handle = kernel32.GlobalAlloc(0x0042, len(payload))
    if not handle:
        raise OSError(ctypes.get_last_error(), "无法分配拖放测试内存。")
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        raise OSError(ctypes.get_last_error(), "无法锁定拖放测试内存。")
    ctypes.memmove(pointer, payload, len(payload))
    kernel32.GlobalUnlock(handle)
    return int(handle)


if __name__ == "__main__":
    unittest.main()
