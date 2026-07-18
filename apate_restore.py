"""以独立输出副本方式实现 Apate 的还原数据变换。

本模块不调用 apate.exe，也不修改输入文件。它只实现已知的文件结构变换；
一个输出副本是否真的是有效压缩包，仍应在下一阶段交由 WinRAR 实测确认。
"""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


BUFFER_SIZE = 1024 * 1024
ARCHIVE_SIGNATURES = (
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"Rar!\x1a\x07\x00",
    b"Rar!\x1a\x07\x01\x00",
    b"7z\xbc\xaf\x27\x1c",
)


class RestoreStatus(StrEnum):
    """还原尝试的结果分类，供界面和后续任务状态机使用。"""

    RESTORED = "restored"
    NOT_PLAUSIBLE = "not_plausible"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class RestoreResult:
    """一次还原尝试的完整结果，不包含文件内容或任何私人数据。"""

    status: RestoreStatus
    message: str
    source_size: int | None = None
    output_size: int | None = None
    mask_length: int | None = None


def looks_like_apate_archive(source: str | Path) -> bool:
    """只读取少量头尾字节，判断 Apate 还原后是否像受支持的压缩包。"""

    source_path = Path(source).expanduser()
    try:
        resolved_source = source_path.resolve(strict=True)
        if not resolved_source.is_file():
            return False
        source_size = resolved_source.stat().st_size
        if source_size <= 4:
            return False

        with resolved_source.open("rb") as source_stream:
            source_stream.seek(-4, os.SEEK_END)
            mask_length = int.from_bytes(source_stream.read(4), byteorder="little")
            output_size = source_size - mask_length - 4
            if mask_length == 0 or output_size <= 0:
                return False

            prefix_length = min(
                max(len(signature) for signature in ARCHIVE_SIGNATURES),
                output_size,
            )
            restored_length = min(mask_length, prefix_length)
            source_stream.seek(output_size + mask_length - restored_length)
            restored_prefix = source_stream.read(restored_length)[::-1]

            if restored_length < prefix_length:
                source_stream.seek(restored_length)
                restored_prefix += source_stream.read(prefix_length - restored_length)
    except (OSError, ValueError):
        return False

    return any(restored_prefix.startswith(signature) for signature in ARCHIVE_SIGNATURES)


def restore_apate_copy(source: str | Path, destination: str | Path) -> RestoreResult:
    """从 ``source`` 写出一个独立的 Apate 还原副本到 ``destination``。

    Apate 在文件结尾写入四字节小端序掩码长度，并在此前写入倒序的原始文件
    头部。还原时需将该段倒序字节写回文件开头，然后去除末尾的附加数据。

    这里直接从输入文件读取、向全新的输出文件写入，而不是先复制后原地修改：
    这样无论成功、失败还是程序中断，输入文件都只会被读取。输出路径要求此前
    不存在，避免覆盖用户已有文件。
    """

    source_path = Path(source).expanduser()
    destination_path = Path(destination).expanduser()

    try:
        resolved_source = source_path.resolve(strict=True)
    except FileNotFoundError:
        return RestoreResult(RestoreStatus.BLOCKED, "输入文件不存在。")

    if not resolved_source.is_file():
        return RestoreResult(RestoreStatus.BLOCKED, "输入路径不是普通文件。")

    resolved_destination = destination_path.resolve(strict=False)
    if resolved_source == resolved_destination:
        return RestoreResult(RestoreStatus.BLOCKED, "输出路径不能与输入文件相同。")

    if destination_path.exists():
        return RestoreResult(RestoreStatus.BLOCKED, "输出路径已存在；请选择新的文件名。")

    try:
        source_size = resolved_source.stat().st_size
    except OSError as error:
        return RestoreResult(RestoreStatus.FAILED, f"无法读取输入文件大小：{error}")

    if source_size <= 4:
        return RestoreResult(
            RestoreStatus.NOT_PLAUSIBLE,
            "文件不足以包含 Apate 结尾标记，未生成输出副本。",
            source_size=source_size,
        )

    try:
        with resolved_source.open("rb") as source_stream:
            # Apate 将掩码长度按无符号四字节小端序写在文件的最后四个字节。
            source_stream.seek(-4, os.SEEK_END)
            mask_length = int.from_bytes(source_stream.read(4), byteorder="little")
    except OSError as error:
        return RestoreResult(RestoreStatus.FAILED, f"无法读取 Apate 结尾标记：{error}")

    output_size = source_size - mask_length - 4
    if mask_length == 0 or output_size <= 0:
        return RestoreResult(
            RestoreStatus.NOT_PLAUSIBLE,
            "结尾标记不是可用的 Apate 掩码长度，未生成输出副本。",
            source_size=source_size,
            mask_length=mask_length,
        )

    try:
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        # 使用 x 模式独占创建，确保检查到“文件不存在”后也不会发生意外覆盖。
        with (
            resolved_source.open("rb") as source_stream,
            destination_path.open("xb+") as output_stream,
        ):
            _copy_prefix(source_stream, output_stream, output_size)
            _write_reversed_tail(source_stream, output_stream, output_size, mask_length)
            output_stream.truncate(output_size)
    except FileExistsError:
        return RestoreResult(RestoreStatus.BLOCKED, "输出路径已存在；请选择新的文件名。")
    except OSError as error:
        # 此文件只可能由本次调用创建；失败时清理不完整副本，绝不触及输入文件。
        with suppress(OSError):
            destination_path.unlink(missing_ok=True)
        return RestoreResult(
            RestoreStatus.FAILED,
            f"生成还原副本失败：{error}",
            source_size=source_size,
            output_size=output_size,
            mask_length=mask_length,
        )

    return RestoreResult(
        RestoreStatus.RESTORED,
        "已生成独立还原副本。请使用 WinRAR 手动验证该副本能否打开。",
        source_size=source_size,
        output_size=output_size,
        mask_length=mask_length,
    )


def _copy_prefix(source_stream: object, output_stream: object, output_size: int) -> None:
    """分块复制应保留的正文，避免把大文件一次装入内存。"""

    source_stream.seek(0)
    remaining = output_size
    while remaining:
        chunk = source_stream.read(min(BUFFER_SIZE, remaining))
        if not chunk:
            raise OSError("输入文件在读取过程中提前结束。")
        output_stream.write(chunk)
        remaining -= len(chunk)


def _write_reversed_tail(
    source_stream: object,
    output_stream: object,
    tail_start: int,
    tail_length: int,
) -> None:
    """把输入文件中倒序保存的原始头部，以反转后的顺序写回输出开头。

    不能直接把整个头部读进内存，因为自定义掩码文件可能很大。每次从尾部读取
    一块、翻转块内字节并顺序写入，就能得到完整反转结果且内存占用恒定。
    """

    output_stream.seek(0)
    remaining = tail_length
    read_position = tail_start + tail_length

    while remaining:
        chunk_size = min(BUFFER_SIZE, remaining)
        read_position -= chunk_size
        source_stream.seek(read_position)
        chunk = source_stream.read(chunk_size)
        if len(chunk) != chunk_size:
            raise OSError("Apate 头部数据不完整。")
        output_stream.write(chunk[::-1])
        remaining -= chunk_size
