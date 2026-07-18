"""以无窗口子进程方式调用本机 WinRAR。"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


DEFAULT_TIMEOUT_SECONDS = 24 * 60 * 60


class WinRARStatus(StrEnum):
    """WinRAR 测试或解压操作的统一结果。"""

    VALID = "valid"
    EXTRACTED = "extracted"
    PASSWORD_REQUIRED = "password_required"
    INVALID_ARCHIVE = "invalid_archive"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class WinRARResult:
    status: WinRARStatus
    message: str
    exit_code: int | None = None
    output_directory: Path | None = None


def find_winrar() -> Path | None:
    """从 WinRAR 的标准安装位置查找程序。"""

    candidates: list[Path] = []
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root:
            candidates.append(Path(root) / "WinRAR" / "WinRAR.exe")
    candidates.append(Path(r"C:\Program Files\WinRAR\WinRAR.exe"))

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


class WinRARAdapter:
    """只向 WinRAR 传递参数，不使用 shell，也不记录密码。"""

    def __init__(
        self,
        executable: str | Path | None,
        *,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.executable = Path(executable) if executable is not None else None
        self.timeout_seconds = timeout_seconds

    def test_archive(self, archive: str | Path, password: str) -> WinRARResult:
        unavailable = self._unavailable_result()
        if unavailable is not None:
            return unavailable
        password_error = _validate_password(password)
        if password_error is not None:
            return password_error

        result = self._run(
            "t",
            "-inul",
            "-y",
            _password_switch(password),
            str(Path(archive)),
        )
        if isinstance(result, WinRARResult):
            return result
        return _result_from_exit_code(result, operation="test")

    def extract_archive(
        self,
        archive: str | Path,
        destination: str | Path,
        password: str,
    ) -> WinRARResult:
        unavailable = self._unavailable_result()
        if unavailable is not None:
            return unavailable
        password_error = _validate_password(password)
        if password_error is not None:
            return password_error

        destination_path = Path(destination)
        try:
            if destination_path.exists():
                if not destination_path.is_dir() or destination_path.is_symlink():
                    return WinRARResult(WinRARStatus.BLOCKED, "解压目标位置不是安全的普通目录。")
                if any(destination_path.iterdir()):
                    return WinRARResult(
                        WinRARStatus.BLOCKED,
                        "解压目标目录不是空目录，已停止以避免覆盖文件。",
                    )
        except OSError as error:
            return WinRARResult(WinRARStatus.FAILED, f"无法检查解压目录：{error}")

        try:
            destination_path.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            return WinRARResult(WinRARStatus.FAILED, f"无法创建解压目录：{error}")

        result = self._run(
            "x",
            "-inul",
            "-y",
            "-o-",
            "-ol-",
            _password_switch(password),
            str(Path(archive)),
            f"-op{destination_path}",
        )
        if isinstance(result, WinRARResult):
            return result

        mapped = _result_from_exit_code(result, operation="extract")
        if mapped.status is not WinRARStatus.EXTRACTED:
            return mapped

        validation_error = _validate_extracted_tree(destination_path)
        if validation_error is not None:
            return WinRARResult(WinRARStatus.BLOCKED, validation_error, result, destination_path)

        return WinRARResult(
            WinRARStatus.EXTRACTED,
            "WinRAR 解压完成。",
            result,
            destination_path,
        )

    def _unavailable_result(self) -> WinRARResult | None:
        if self.executable is None or not self.executable.is_file():
            return WinRARResult(WinRARStatus.BLOCKED, "未找到可用的 WinRAR.exe。")
        return None

    def _run(self, *arguments: str) -> int | WinRARResult:
        command = [str(self.executable), *arguments]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                timeout=self.timeout_seconds,
                creationflags=creation_flags,
            )
        except subprocess.TimeoutExpired:
            return WinRARResult(WinRARStatus.FAILED, "WinRAR 运行超时，任务已停止。")
        except (OSError, ValueError) as error:
            return WinRARResult(WinRARStatus.FAILED, f"无法启动 WinRAR：{error}")
        return completed.returncode


def _password_switch(password: str) -> str:
    # -p- 禁止 WinRAR 在缺少密码时弹出自己的输入窗口。
    return f"-p{password}" if password else "-p-"


def _validate_password(password: str) -> WinRARResult | None:
    if "\x00" in password:
        return WinRARResult(WinRARStatus.BLOCKED, "密码不能包含空字符。")
    if len(password) > 127:
        return WinRARResult(WinRARStatus.BLOCKED, "WinRAR 密码不能超过 127 个字符。")
    return None


def _result_from_exit_code(exit_code: int, *, operation: str) -> WinRARResult:
    if exit_code == 0:
        if operation == "test":
            return WinRARResult(WinRARStatus.VALID, "WinRAR 已确认这是可读取的压缩包。", exit_code)
        return WinRARResult(WinRARStatus.EXTRACTED, "WinRAR 解压完成。", exit_code)
    if exit_code == 11:
        return WinRARResult(
            WinRARStatus.PASSWORD_REQUIRED,
            "压缩包需要密码，或当前密码不正确。",
            exit_code,
        )
    if operation == "test" and exit_code in {1, 2, 3, 10}:
        return WinRARResult(
            WinRARStatus.INVALID_ARCHIVE,
            "WinRAR 无法把当前文件作为完整压缩包读取。",
            exit_code,
        )
    return WinRARResult(
        WinRARStatus.FAILED,
        f"WinRAR 未完成操作，返回代码为 {exit_code}。",
        exit_code,
    )


def _validate_extracted_tree(destination: Path) -> str | None:
    """确认 WinRAR 的所有输出仍位于任务目录内，且没有保留链接。"""

    try:
        root = destination.resolve(strict=True)
        for path in destination.rglob("*"):
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                return "解压结果包含链接，已停止后续处理。"
            if not path.resolve(strict=False).is_relative_to(root):
                return "解压结果试图离开任务目录，已停止后续处理。"
    except OSError as error:
        return f"无法验证解压结果：{error}"
    return None
