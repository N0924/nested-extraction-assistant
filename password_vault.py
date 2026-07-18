"""按用户选择，以可核对的明文 JSON 保存常用密码池。"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path


MAX_SAVED_PASSWORDS = 50
MAX_PASSWORD_LENGTH = 127


class PasswordVaultError(RuntimeError):
    """密码池无法读取、校验或保存。"""


def parse_password_text(text: str) -> list[str]:
    """按换行或竖线拆分批量输入，第一项作为默认密码。"""

    return normalize_passwords([part.strip() for part in re.split(r"[|\r\n]+", text)])


def normalize_passwords(values: Sequence[str] | object) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise ValueError("密码池数据必须是密码列表。")

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError("密码池中只能包含文字密码。")
        if not value:
            continue
        if "\x00" in value:
            raise ValueError("密码不能包含空字符。")
        if len(value) > MAX_PASSWORD_LENGTH:
            raise ValueError(f"单个密码不能超过 {MAX_PASSWORD_LENGTH} 个字符。")
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)

    if len(normalized) > MAX_SAVED_PASSWORDS:
        raise ValueError(f"密码池最多保存 {MAX_SAVED_PASSWORDS} 个密码。")
    return normalized


class PasswordVaultStore:
    """保留旧类名，实际使用本机明文 JSON 文件。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> list[str]:
        if not self.path.exists():
            return []
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(envelope, dict) or envelope.get("version") != 3:
                raise ValueError("旧版密码池需要重新保存。")
            if envelope.get("storage") != "local-plaintext":
                raise ValueError("密码池保存方式不受支持。")
            return normalize_passwords(envelope.get("passwords"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise PasswordVaultError("密码池文件无法读取或已经损坏，请重新保存。") from error

    def save(self, passwords: Sequence[str]) -> None:
        normalized = normalize_passwords(passwords)
        if not normalized:
            try:
                self.path.unlink(missing_ok=True)
            except OSError as error:
                raise PasswordVaultError("无法清空密码池文件。") from error
            return

        envelope = {
            "version": 3,
            "storage": "local-plaintext",
            "passwords": normalized,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
            temporary.write_text(
                json.dumps(envelope, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError as error:
            raise PasswordVaultError("无法保存密码池文件。") from error
