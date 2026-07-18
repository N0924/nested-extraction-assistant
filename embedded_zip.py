"""识别并剥离带有前置外壳和尾随数据的嵌入式 ZIP。"""

from __future__ import annotations

import os
import struct
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path


BUFFER_SIZE = 1024 * 1024
EOCD_SIGNATURE = b"PK\x05\x06"
CENTRAL_SIGNATURE = b"PK\x01\x02"
LOCAL_SIGNATURE = b"PK\x03\x04"
EOCD_SIZE = 22
CENTRAL_HEADER_SIZE = 46
DEFAULT_SCAN_SIZE = 8 * 1024 * 1024


class EmbeddedZipError(RuntimeError):
    """嵌入式 ZIP 无法安全读取或复制。"""


@dataclass(frozen=True)
class EmbeddedZipInfo:
    """ZIP 在外壳文件中的边界，不包含前置外壳和 ZIP 后的尾随数据。"""

    source_size: int
    prefix_size: int
    archive_end: int
    entry_count: int

    @property
    def archive_size(self) -> int:
        return self.archive_end - self.prefix_size


def find_embedded_zip(
    source: str | Path,
    *,
    scan_size: int = DEFAULT_SCAN_SIZE,
) -> EmbeddedZipInfo | None:
    """从 ZIP 目录结构反推出前置外壳长度；仅有零散 ``PK`` 字节不算匹配。"""

    source_path = Path(source).expanduser()
    try:
        resolved_source = source_path.resolve(strict=True)
        if not resolved_source.is_file():
            return None
        source_size = resolved_source.stat().st_size
        if source_size < EOCD_SIZE:
            return None

        tail_start = max(0, source_size - max(scan_size, EOCD_SIZE))
        with resolved_source.open("rb") as stream:
            stream.seek(tail_start)
            tail = stream.read()

            search_to = len(tail)
            while True:
                relative_eocd = tail.rfind(EOCD_SIGNATURE, 0, search_to)
                if relative_eocd < 0:
                    return None
                absolute_eocd = tail_start + relative_eocd
                info = _validate_eocd(stream, source_size, absolute_eocd, tail[relative_eocd:])
                if info is not None:
                    return info
                search_to = relative_eocd
    except OSError as error:
        raise EmbeddedZipError(f"无法检查嵌入式 ZIP：{error}") from error


def copy_embedded_zip(
    source: str | Path,
    destination: str | Path,
    info: EmbeddedZipInfo,
) -> Path:
    """只读源文件，把已验证的 ZIP 字节区间流式写入独立副本。"""

    source_path = Path(source).expanduser().resolve(strict=True)
    destination_path = Path(destination).expanduser()
    if not source_path.is_file():
        raise EmbeddedZipError("输入路径不是普通文件。")
    if source_path.stat().st_size != info.source_size:
        raise EmbeddedZipError("源文件大小已经改变，请重新检测后再处理。")
    if info.prefix_size <= 0 or info.archive_end > info.source_size or info.archive_size <= 0:
        raise EmbeddedZipError("嵌入式 ZIP 边界无效，未生成副本。")
    if destination_path.exists():
        raise EmbeddedZipError("嵌入式 ZIP 副本目标已经存在。")

    try:
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with source_path.open("rb") as source_stream, destination_path.open("xb") as output_stream:
            source_stream.seek(info.prefix_size, os.SEEK_SET)
            remaining = info.archive_size
            while remaining:
                chunk = source_stream.read(min(BUFFER_SIZE, remaining))
                if not chunk:
                    raise OSError("读取嵌入式 ZIP 时源文件提前结束。")
                output_stream.write(chunk)
                remaining -= len(chunk)
        if source_path.stat().st_size != info.source_size:
            raise OSError("复制期间源文件大小发生变化。")
    except OSError as error:
        with suppress(OSError):
            destination_path.unlink(missing_ok=True)
        raise EmbeddedZipError(f"无法生成嵌入式 ZIP 工作副本：{error}") from error

    return destination_path


def _validate_eocd(
    stream: object,
    source_size: int,
    absolute_eocd: int,
    tail_from_eocd: bytes,
) -> EmbeddedZipInfo | None:
    if len(tail_from_eocd) < EOCD_SIZE:
        return None
    try:
        (
            signature,
            disk_number,
            central_disk,
            entries_on_disk,
            entry_count,
            central_size,
            central_offset,
            comment_length,
        ) = struct.unpack("<4s4H2LH", tail_from_eocd[:EOCD_SIZE])
    except struct.error:
        return None

    archive_end = absolute_eocd + EOCD_SIZE + comment_length
    if (
        signature != EOCD_SIGNATURE
        or disk_number != 0
        or central_disk != 0
        or entries_on_disk != entry_count
        or entry_count in (0, 0xFFFF)
        or archive_end > source_size
    ):
        return None

    central_absolute = absolute_eocd - central_size
    prefix_size = central_absolute - central_offset
    if prefix_size <= 0 or central_absolute <= prefix_size:
        return None

    position = central_absolute
    central_end = central_absolute + central_size
    for _ in range(entry_count):
        if position + CENTRAL_HEADER_SIZE > central_end:
            return None
        stream.seek(position, os.SEEK_SET)
        header = stream.read(CENTRAL_HEADER_SIZE)
        if len(header) != CENTRAL_HEADER_SIZE or not header.startswith(CENTRAL_SIGNATURE):
            return None
        name_length, extra_length, file_comment_length = struct.unpack_from("<HHH", header, 28)
        disk_start = struct.unpack_from("<H", header, 34)[0]
        local_offset = struct.unpack_from("<L", header, 42)[0]
        if disk_start != 0:
            return None
        local_absolute = prefix_size + local_offset
        if local_absolute < prefix_size or local_absolute + 4 > central_absolute:
            return None
        stream.seek(local_absolute, os.SEEK_SET)
        if stream.read(4) != LOCAL_SIGNATURE:
            return None
        position += CENTRAL_HEADER_SIZE + name_length + extra_length + file_comment_length

    if position != central_end:
        return None
    return EmbeddedZipInfo(source_size, prefix_size, archive_end, entry_count)
