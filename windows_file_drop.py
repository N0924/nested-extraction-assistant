"""使用 Windows Shell 消息为 Tk 控件提供原生文件拖放。"""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from ctypes import wintypes


WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4
DROP_FILE_COUNT = 0xFFFFFFFF
LONG_PTR = ctypes.c_ssize_t
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class WindowsFileDrop:
    """拦截指定 Tk 控件的 WM_DROPFILES，并回调完整 Unicode 路径列表。"""

    def __init__(self, widget: object, on_drop: Callable[[list[str]], None]) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows 原生拖放仅适用于 Windows。")

        self.widget = widget
        self.on_drop = on_drop
        self._closed = False
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        self._configure_functions()

        widget.update_idletasks()
        self.hwnd = wintypes.HWND(widget.winfo_id())
        self._new_wndproc = WNDPROC(self._window_procedure)

        ctypes.set_last_error(0)
        callback_address = ctypes.cast(self._new_wndproc, ctypes.c_void_p).value
        self._old_wndproc = self._set_window_long_ptr(
            self.hwnd,
            GWLP_WNDPROC,
            LONG_PTR(callback_address),
        )
        error = ctypes.get_last_error()
        if not self._old_wndproc and error:
            raise OSError(error, "无法启用 Windows 文件拖放。")

        self._shell32.DragAcceptFiles(self.hwnd, True)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._shell32.DragAcceptFiles(self.hwnd, False)
        if self._old_wndproc:
            self._set_window_long_ptr(
                self.hwnd,
                GWLP_WNDPROC,
                LONG_PTR(self._old_wndproc),
            )
            self._old_wndproc = 0

    def _configure_functions(self) -> None:
        self._set_window_long_ptr = (
            self._user32.SetWindowLongPtrW
            if ctypes.sizeof(ctypes.c_void_p) == 8
            else self._user32.SetWindowLongW
        )
        self._set_window_long_ptr.argtypes = (wintypes.HWND, ctypes.c_int, LONG_PTR)
        self._set_window_long_ptr.restype = LONG_PTR

        self._user32.CallWindowProcW.argtypes = (
            ctypes.c_void_p,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        self._user32.CallWindowProcW.restype = LRESULT

        self._shell32.DragAcceptFiles.argtypes = (wintypes.HWND, wintypes.BOOL)
        self._shell32.DragAcceptFiles.restype = None
        self._shell32.DragQueryFileW.argtypes = (
            wintypes.HANDLE,
            wintypes.UINT,
            wintypes.LPWSTR,
            wintypes.UINT,
        )
        self._shell32.DragQueryFileW.restype = wintypes.UINT
        self._shell32.DragFinish.argtypes = (wintypes.HANDLE,)
        self._shell32.DragFinish.restype = None

    def _window_procedure(
        self,
        hwnd: int,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        if message == WM_DROPFILES:
            try:
                paths = self._read_paths(wparam)
                # 不能在 Windows 窗口回调内部重入 Tk；这里只允许写入线程安全队列。
                self.on_drop(paths)
            finally:
                self._shell32.DragFinish(wintypes.HANDLE(wparam))
            return 0

        return self._user32.CallWindowProcW(
            ctypes.c_void_p(self._old_wndproc),
            hwnd,
            message,
            wparam,
            lparam,
        )

    def _read_paths(self, drop_handle: int) -> list[str]:
        handle = wintypes.HANDLE(drop_handle)
        count = self._shell32.DragQueryFileW(handle, DROP_FILE_COUNT, None, 0)
        paths: list[str] = []
        for index in range(count):
            length = self._shell32.DragQueryFileW(handle, index, None, 0)
            buffer = ctypes.create_unicode_buffer(length + 1)
            self._shell32.DragQueryFileW(handle, index, buffer, len(buffer))
            paths.append(buffer.value)
        return paths
