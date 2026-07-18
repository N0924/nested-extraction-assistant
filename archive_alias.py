"""为文件头明确的压缩包生成正确扩展名的只读工作副本。"""

from __future__ import annotations

import shutil
from contextlib import suppress
from pathlib import Path


COPY_BUFFER_BYTES = 8 * 1024 * 1024
HEADER_SIZE = 8

ARCHIVE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"PK\x03\x04", ".zip"),
    (b"PK\x05\x06", ".zip"),
    (b"PK\x07\x08", ".zip"),
    (b"Rar!\x1a\x07\x00", ".rar"),
    (b"Rar!\x1a\x07\x01\x00", ".rar"),
    (b"7z\xbc\xaf'\x1c", ".7z"),
)


class ArchiveAliasError(RuntimeError):
    """正确后缀工作副本无法被安全创建。"""


def detect_archive_suffix(source: str | Path) -> str | None:
    """只根据文件开头的正式签名字节识别 ZIP、RAR 或 7Z。"""

    source_path = Path(source).expanduser()
    try:
        resolved_source = source_path.resolve(strict=True)
        if not resolved_source.is_file() or resolved_source.is_symlink():
            return None
        with resolved_source.open("rb") as stream:
            header = stream.read(HEADER_SIZE)
    except OSError as error:
        raise ArchiveAliasError(f"无法读取文件头：{error}") from error

    for signature, suffix in ARCHIVE_SIGNATURES:
        if header.startswith(signature):
            return suffix
    return None


def copy_archive_alias(
    source: str | Path,
    destination_directory: str | Path,
    suffix: str,
) -> Path:
    """流式复制到正确后缀的工作文件；不改名、不写入源文件。"""

    normalized_suffix = suffix.casefold()
    if normalized_suffix not in {".zip", ".rar", ".7z"}:
        raise ArchiveAliasError("工作副本扩展名不受支持。")

    source_path = Path(source).expanduser().resolve(strict=True)
    if not source_path.is_file() or source_path.is_symlink():
        raise ArchiveAliasError("输入路径不是安全的普通文件。")

    destination_root = Path(destination_directory).expanduser()
    destination = destination_root / f"archive{normalized_suffix}"
    partial = destination.with_name(f"{destination.name}.copying")
    try:
        source_size = source_path.stat().st_size
        if source_size <= 0:
            raise OSError("输入文件大小为零。")
        destination_root.mkdir(parents=True, exist_ok=False)
        if shutil.disk_usage(destination_root).free < source_size:
            raise OSError("工作磁盘剩余空间不足。")
        with source_path.open("rb") as source_stream, partial.open("xb") as output_stream:
            shutil.copyfileobj(source_stream, output_stream, length=COPY_BUFFER_BYTES)
        if source_path.stat().st_size != source_size:
            raise OSError("复制期间源文件大小发生变化。")
        if partial.stat().st_size != source_size:
            raise OSError("工作副本大小与源文件不一致。")
        partial.replace(destination)
    except OSError as error:
        with suppress(OSError):
            partial.unlink(missing_ok=True)
        raise ArchiveAliasError(f"无法生成正确后缀的工作副本：{error}") from error
    return destination
