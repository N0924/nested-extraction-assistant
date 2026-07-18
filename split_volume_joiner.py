"""在独立工作目录中流式拼接 name.7z.001 一类原始字节切分卷。"""

from __future__ import annotations

import shutil
from contextlib import suppress
from pathlib import Path

from volume_resolver import NUMBERED_EXTENSION_PATTERN, ordered_raw_volume_members


COPY_BUFFER_BYTES = 8 * 1024 * 1024


class VolumeJoinError(RuntimeError):
    """分卷不完整、超过限制或无法安全写入工作目录。"""


def join_numbered_extension_volumes(
    members: list[Path] | tuple[Path, ...],
    destination_directory: str | Path,
    *,
    max_total_bytes: int,
) -> Path:
    """按末尾编号拼接分卷并返回工作副本；绝不修改源卷。"""

    parsed: list[tuple[int, Path, str]] = []
    for value in dict.fromkeys(Path(member) for member in members):
        match = NUMBERED_EXTENSION_PATTERN.match(value.name)
        if match is None:
            raise VolumeJoinError("分卷名称不是 name.7z.001 形式。")
        if not value.is_file() or value.is_symlink():
            raise VolumeJoinError("分卷成员不存在或不是安全的普通文件。")
        parsed.append((int(match.group("number")), value, match.group("base")))

    if len(parsed) < 2:
        raise VolumeJoinError("至少需要两个连续分卷才能执行拼接。")
    parents = {path.parent.resolve(strict=False) for _, path, _ in parsed}
    bases = {base.casefold() for _, _, base in parsed}
    if len(parents) != 1 or len(bases) != 1:
        raise VolumeJoinError("分卷必须位于同一目录并具有相同基础名称。")

    parsed.sort(key=lambda item: item[0])
    numbers = [number for number, _, _ in parsed]
    if len(numbers) != len(set(numbers)):
        raise VolumeJoinError("分卷编号存在重复。")
    if numbers[0] not in (0, 1):
        raise VolumeJoinError("缺少首卷；编号分卷必须从 000 或 001 开始。")
    if numbers != list(range(numbers[0], numbers[-1] + 1)):
        raise VolumeJoinError("分卷编号不连续，可能缺少中间分卷。")

    return _join_ordered_members(
        tuple(path for _, path, _ in parsed),
        destination_directory,
        destination_name=parsed[0][2],
        max_total_bytes=max_total_bytes,
    )


def join_inferred_first_volume(
    start: Path,
    members: list[Path] | tuple[Path, ...],
    destination_directory: str | Path,
    *,
    max_total_bytes: int,
) -> Path:
    """把 ``name.mp4 + name.002/.003`` 按首卷、后续编号顺序拼接。"""

    ordered = ordered_raw_volume_members(start, members)
    if ordered is None or ordered[0] != start:
        raise VolumeJoinError("无法确定不规则分卷的唯一拼接顺序。")
    numbers: list[int] = []
    for member in ordered[1:]:
        match = NUMBERED_EXTENSION_PATTERN.match(member.name)
        if match is None:
            raise VolumeJoinError("后续分卷缺少连续数字编号。")
        numbers.append(int(match.group("number")))
    if numbers != list(range(2, len(numbers) + 2)):
        raise VolumeJoinError("后续分卷必须从 002 开始连续编号。")
    return _join_ordered_members(
        ordered,
        destination_directory,
        destination_name=f"{start.stem}.joined",
        max_total_bytes=max_total_bytes,
    )


def _join_ordered_members(
    members: tuple[Path, ...],
    destination_directory: str | Path,
    *,
    destination_name: str,
    max_total_bytes: int,
) -> Path:
    parents = {path.parent.resolve(strict=False) for path in members}
    if len(parents) != 1:
        raise VolumeJoinError("分卷必须位于同一目录。")
    for path in members:
        if not path.is_file() or path.is_symlink():
            raise VolumeJoinError("分卷成员不存在或不是安全的普通文件。")

    try:
        total_bytes = sum(path.stat().st_size for path in members)
    except OSError as error:
        raise VolumeJoinError(f"无法读取分卷大小：{error}") from error
    if total_bytes <= 0:
        raise VolumeJoinError("分卷总大小为零。")
    if max_total_bytes > 0 and total_bytes > max_total_bytes:
        raise VolumeJoinError("分卷拼接大小超过任务累计容量限制。")

    destination_root = Path(destination_directory)
    try:
        destination_root.mkdir(parents=True, exist_ok=False)
        free_bytes = shutil.disk_usage(destination_root).free
    except OSError as error:
        raise VolumeJoinError(f"无法创建分卷工作目录：{error}") from error
    if free_bytes < total_bytes:
        raise VolumeJoinError("工作磁盘剩余空间不足，无法生成分卷拼接副本。")

    destination = destination_root / destination_name
    partial = destination.with_name(f"{destination.name}.joining")
    try:
        with partial.open("xb") as output_stream:
            for member in members:
                with member.open("rb") as input_stream:
                    shutil.copyfileobj(input_stream, output_stream, length=COPY_BUFFER_BYTES)
        if partial.stat().st_size != total_bytes:
            raise OSError("拼接副本大小与源分卷总大小不一致。")
        partial.replace(destination)
    except OSError as error:
        with suppress(OSError):
            partial.unlink(missing_ok=True)
        raise VolumeJoinError(f"无法拼接分卷工作副本：{error}") from error
    return destination
