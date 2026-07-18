"""按文件名关系识别常见分卷组并选择首卷。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


PART_RAR_PATTERN = re.compile(r"^(?P<base>.+)\.part(?P<number>\d+)\.rar$", re.IGNORECASE)
OLD_RAR_PATTERN = re.compile(r"^(?P<base>.+)\.r(?P<number>\d+)$", re.IGNORECASE)
SPLIT_ZIP_PATTERN = re.compile(r"^(?P<base>.+)\.z(?P<number>\d+)$", re.IGNORECASE)
NUMBERED_EXTENSION_PATTERN = re.compile(
    r"^(?P<base>.+)\.(?P<number>\d{3})$",
    re.IGNORECASE,
)
CUSTOM_PATTERN = re.compile(
    r"^(?P<base>.*?)(?P<number>\d+)(?P<suffix>\.?zip|\.?rar|\.?7z)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class VolumeSelection:
    start: Path
    members: tuple[Path, ...]


def expand_root_inputs(selected: list[str | Path] | tuple[str | Path, ...]) -> list[Path]:
    """把文件或文件夹展开为根任务；文件夹只查看第一层并合并分卷。"""

    roots: list[Path] = []
    seen: set[Path] = set()
    for value in selected:
        path = Path(value)
        if path.is_file():
            candidates = [resolve_root_volume(path).start]
        elif path.is_dir():
            try:
                files = sorted(
                    (child for child in path.iterdir() if child.is_file()),
                    key=lambda child: child.name.casefold(),
                )
            except OSError:
                continue
            mapping = resolve_volume_groups(files)
            candidates = [mapping.get(child, child) for child in files]
        else:
            continue

        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            roots.append(candidate)
    return roots


def resolve_root_volume(selected: str | Path) -> VolumeSelection:
    """若用户选中了分卷成员，返回同组首卷；识别失败时保持原文件。"""

    selected_path = Path(selected)
    try:
        siblings = [path for path in selected_path.parent.iterdir() if path.is_file()]
    except OSError:
        return VolumeSelection(selected_path, (selected_path,))

    mapping = resolve_volume_groups(siblings)
    start = mapping.get(selected_path, selected_path)
    if start == selected_path and selected_path not in mapping:
        return VolumeSelection(selected_path, (selected_path,))
    grouped_members = tuple(path for path, group_start in mapping.items() if group_start == start)
    members = ordered_raw_volume_members(start, grouped_members) or tuple(
        sorted(grouped_members, key=lambda path: path.name.casefold())
    )
    return VolumeSelection(start, members)


def resolve_volume_groups(files: list[Path]) -> dict[Path, Path]:
    """返回“分卷成员 -> 首卷”的映射；非分卷文件不出现在结果中。"""

    paths = list(dict.fromkeys(Path(path) for path in files))
    mapping: dict[Path, Path] = {}

    part_groups: dict[tuple[Path, str], list[tuple[int, Path]]] = {}
    for path in paths:
        match = PART_RAR_PATTERN.match(path.name)
        if match:
            key = (path.parent, match.group("base").casefold())
            part_groups.setdefault(key, []).append((int(match.group("number")), path))
    for members in part_groups.values():
        if len(members) >= 2:
            start = min(members, key=lambda item: item[0])[1]
            for _, path in members:
                mapping[path] = start

    path_by_name = {(path.parent, path.name.casefold()): path for path in paths}
    _map_main_file_groups(paths, mapping, path_by_name, OLD_RAR_PATTERN, ".rar")
    _map_main_file_groups(paths, mapping, path_by_name, SPLIT_ZIP_PATTERN, ".zip")

    numbered_groups: dict[tuple[Path, str, int], list[tuple[int, Path]]] = {}
    for path in paths:
        if path in mapping:
            continue
        match = NUMBERED_EXTENSION_PATTERN.match(path.name)
        if match:
            key = (
                path.parent,
                match.group("base").casefold(),
                len(match.group("number")),
            )
            numbered_groups.setdefault(key, []).append((int(match.group("number")), path))
    for (parent, base, _), members in numbered_groups.items():
        ordered = sorted(members, key=lambda item: item[0])
        inferred_first = _find_inferred_first(paths, mapping, parent, base, ordered)
        if inferred_first is not None:
            mapping[inferred_first] = inferred_first
            for _, path in ordered:
                mapping[path] = inferred_first
        elif len(ordered) >= 2:
            start = ordered[0][1]
            for _, path in ordered:
                mapping[path] = start

    custom_groups: dict[tuple[Path, str, str], list[tuple[int, Path]]] = {}
    for path in paths:
        if path in mapping:
            continue
        match = CUSTOM_PATTERN.match(path.name)
        if match:
            key = (
                path.parent,
                match.group("base").casefold(),
                match.group("suffix").casefold(),
            )
            custom_groups.setdefault(key, []).append((int(match.group("number")), path))
    for members in custom_groups.values():
        if len(members) >= 2:
            start = min(members, key=lambda item: item[0])[1]
            for _, path in members:
                mapping[path] = start

    return mapping


def ordered_raw_volume_members(
    start: Path,
    members: list[Path] | tuple[Path, ...],
) -> tuple[Path, ...] | None:
    """返回应按原始字节拼接的顺序；标准 RAR/ZIP 分卷返回 ``None``。"""

    unique = tuple(dict.fromkeys(Path(member) for member in members))
    if start not in unique or len(unique) < 2:
        return None

    parsed: list[tuple[int, Path, str, int]] = []
    for member in unique:
        match = NUMBERED_EXTENSION_PATTERN.match(member.name)
        if match is None:
            parsed = []
            break
        parsed.append(
            (
                int(match.group("number")),
                member,
                match.group("base").casefold(),
                len(match.group("number")),
            )
        )
    if parsed:
        bases = {base for _, _, base, _ in parsed}
        widths = {width for _, _, _, width in parsed}
        if len(bases) == 1 and len(widths) == 1:
            return tuple(path for _, path, _, _ in sorted(parsed, key=lambda item: item[0]))

    companions: list[tuple[int, Path]] = []
    expected_base = start.stem.casefold()
    expected_width: int | None = None
    for member in unique:
        if member == start:
            continue
        match = NUMBERED_EXTENSION_PATTERN.match(member.name)
        if match is None or match.group("base").casefold() != expected_base:
            return None
        width = len(match.group("number"))
        if expected_width is None:
            expected_width = width
        elif width != expected_width:
            return None
        companions.append((int(match.group("number")), member))
    companions.sort(key=lambda item: item[0])
    if not companions or companions[0][0] != 2:
        return None
    return (start, *(path for _, path in companions))


def _find_inferred_first(
    paths: list[Path],
    mapping: dict[Path, Path],
    parent: Path,
    base: str,
    numbered_members: list[tuple[int, Path]],
) -> Path | None:
    if not numbered_members or numbered_members[0][0] != 2:
        return None
    candidates = [
        path
        for path in paths
        if path.parent == parent
        and path not in mapping
        and NUMBERED_EXTENSION_PATTERN.match(path.name) is None
        and path.stem.casefold() == base
    ]
    return candidates[0] if len(candidates) == 1 else None


def _map_main_file_groups(
    paths: list[Path],
    mapping: dict[Path, Path],
    path_by_name: dict[tuple[Path, str], Path],
    pattern: re.Pattern[str],
    main_suffix: str,
) -> None:
    grouped: dict[tuple[Path, str], list[Path]] = {}
    for path in paths:
        if path in mapping:
            continue
        match = pattern.match(path.name)
        if match:
            key = (path.parent, match.group("base").casefold())
            grouped.setdefault(key, []).append(path)

    for (parent, base), members in grouped.items():
        main = path_by_name.get((parent, f"{base}{main_suffix}".casefold()))
        if main is None:
            continue
        mapping[main] = main
        for member in members:
            mapping[member] = main
