"""单根文件起步的递归安全解压任务流程。"""

from __future__ import annotations

import json
import shutil
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from apate_restore import RestoreStatus, looks_like_apate_archive, restore_apate_copy
from archive_alias import ArchiveAliasError, copy_archive_alias, detect_archive_suffix
from embedded_zip import EmbeddedZipError, copy_embedded_zip, find_embedded_zip
from split_volume_joiner import (
    VolumeJoinError,
    join_inferred_first_volume,
    join_numbered_extension_volumes,
)
from volume_resolver import (
    NUMBERED_EXTENSION_PATTERN,
    ordered_raw_volume_members,
    resolve_root_volume,
    resolve_volume_groups,
)
from password_vault import MAX_SAVED_PASSWORDS
from winrar_adapter import WinRARAdapter, WinRARResult, WinRARStatus


ProgressCallback = Callable[[str], None]


class JobStatus(StrEnum):
    COMPLETED = "completed"
    PASSWORD_REQUIRED = "password_required"
    NOT_ARCHIVE = "not_archive"
    BLOCKED = "blocked"
    FAILED = "failed"


class ExtractionMethod(StrEnum):
    DIRECT = "direct"
    EXTENSION_ALIAS = "extension_alias"
    EMBEDDED_ZIP = "embedded_zip"
    APATE = "apate"
    MIXED = "mixed"


@dataclass(frozen=True)
class ExtractionLimits:
    max_files: int = 0
    max_total_bytes: int = 0
    max_archives: int = 0


@dataclass(frozen=True)
class ExtractionJobResult:
    status: JobStatus
    message: str
    task_id: str | None = None
    method: ExtractionMethod | None = None
    output_directory: Path | None = None
    work_directory: Path | None = None
    file_count: int = 0
    total_bytes: int = 0
    archive_count: int = 0
    volume_group_count: int = 0


class _ProbeStatus(StrEnum):
    ARCHIVE = "archive"
    PASSWORD_REQUIRED = "password_required"
    NOT_ARCHIVE = "not_archive"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class _ProbeResult:
    status: _ProbeStatus
    message: str
    archive_path: Path | None = None
    method: ExtractionMethod | None = None
    password: str | None = None


@dataclass(frozen=True)
class _ArchiveTask:
    archive_path: Path
    logical_parent: Path
    method: ExtractionMethod
    password: str


def run_extraction_job(
    *,
    source: str | Path,
    work_root: str | Path,
    output_root: str | Path,
    password: str,
    password_candidates: Sequence[str] | None = None,
    winrar: WinRARAdapter,
    progress: ProgressCallback | None = None,
    limits: ExtractionLimits | None = None,
) -> ExtractionJobResult:
    result = _run_extraction_job_impl(
        source=source,
        work_root=work_root,
        output_root=output_root,
        password=password,
        password_candidates=password_candidates,
        winrar=winrar,
        progress=progress,
        limits=limits,
    )
    return _finalize_work_directory(result)


def _run_extraction_job_impl(
    *,
    source: str | Path,
    work_root: str | Path,
    output_root: str | Path,
    password: str,
    password_candidates: Sequence[str] | None = None,
    winrar: WinRARAdapter,
    progress: ProgressCallback | None = None,
    limits: ExtractionLimits | None = None,
) -> ExtractionJobResult:
    """从一个根文件开始，递归解压所有能被确认的内层压缩包。"""

    active_limits = limits or ExtractionLimits()
    active_passwords = _ordered_passwords(password, password_candidates)
    source_path = Path(source).expanduser()
    try:
        source_path = source_path.resolve(strict=True)
    except FileNotFoundError:
        return ExtractionJobResult(JobStatus.BLOCKED, "输入文件不存在。")
    if not source_path.is_file():
        return ExtractionJobResult(JobStatus.BLOCKED, "输入路径不是普通文件。")

    root_volume = resolve_root_volume(source_path)
    source_path = root_volume.start

    work_path = Path(work_root).expanduser().resolve(strict=False)
    output_path = Path(output_root).expanduser().resolve(strict=False)
    location_error = _validate_locations(source_path, work_path, output_path)
    if location_error is not None:
        return ExtractionJobResult(JobStatus.BLOCKED, location_error)

    task_id = _make_task_id(source_path)
    job_directory = work_path / "jobs" / task_id
    staging_directory = job_directory / "final-staging"
    try:
        staging_directory.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        return ExtractionJobResult(JobStatus.FAILED, f"无法创建工作目录：{error}")

    try:
        root_archive_path = _prepare_volume_source(
            root_volume.start,
            root_volume.members,
            job_directory / "joined-root",
            active_limits.max_total_bytes,
            progress,
        )
    except VolumeJoinError as error:
        return ExtractionJobResult(
            JobStatus.BLOCKED,
            str(error),
            task_id=task_id,
            work_directory=job_directory,
        )

    _notify(progress, "正在检查根文件...")
    root_probe = _probe_archive(
        root_archive_path,
        job_directory / "probes" / "root",
        active_passwords,
        winrar,
        progress,
    )
    early_result = _probe_failure_result(root_probe, task_id, job_directory, root=True)
    if early_result is not None:
        return early_result

    assert root_probe.archive_path is not None
    assert root_probe.method is not None
    assert root_probe.password is not None
    queue: deque[_ArchiveTask] = deque(
        [_ArchiveTask(root_probe.archive_path, Path(), root_probe.method, root_probe.password)]
    )
    used_apate = root_probe.method is ExtractionMethod.APATE
    used_extension_alias = root_probe.method is ExtractionMethod.EXTENSION_ALIAS
    used_embedded_zip = root_probe.method is ExtractionMethod.EMBEDDED_ZIP
    extracted_file_count = 0
    extracted_total_bytes = 0
    archive_count = 0
    password_attempt_count = 0
    volume_group_count = 1 if len(root_volume.members) > 1 else 0
    probe_count = 0

    while queue:
        task = queue.popleft()
        archive_count += 1
        if active_limits.max_archives > 0 and archive_count > active_limits.max_archives:
            return _limit_result(
                "压缩包数量超过任务限制。",
                task_id,
                job_directory,
                archive_count - 1,
                extracted_file_count,
                extracted_total_bytes,
            )

        _notify(progress, f"正在解压第 {archive_count} 个压缩包...")
        layer_directory = job_directory / "layers" / f"{archive_count:04d}"
        extraction, attempts_used = _extract_archive_with_passwords(
            task.archive_path,
            layer_directory,
            job_directory / "extraction-attempts" / f"{archive_count:04d}",
            active_passwords,
            winrar,
            progress,
        )
        password_attempt_count += attempts_used
        if extraction.status is WinRARStatus.PASSWORD_REQUIRED:
            return _password_result(
                task_id,
                job_directory,
                archive_count - 1,
                message=extraction.message,
            )
        if extraction.status is not WinRARStatus.EXTRACTED:
            status = (
                JobStatus.BLOCKED
                if extraction.status is WinRARStatus.BLOCKED
                else JobStatus.FAILED
            )
            return ExtractionJobResult(
                status=status,
                message=extraction.message,
                task_id=task_id,
                method=root_probe.method,
                work_directory=job_directory,
                archive_count=archive_count - 1,
            )

        try:
            layer_files = sorted(path for path in layer_directory.rglob("*") if path.is_file())
            layer_bytes = sum(path.stat().st_size for path in layer_files)
        except OSError as error:
            return ExtractionJobResult(
                status=JobStatus.FAILED,
                message=f"无法检查第 {archive_count} 层输出：{error}",
                task_id=task_id,
                work_directory=job_directory,
                archive_count=archive_count,
            )

        extracted_file_count += len(layer_files)
        extracted_total_bytes += layer_bytes
        if active_limits.max_files > 0 and extracted_file_count > active_limits.max_files:
            return _limit_result(
                "解压产生的文件数量超过任务限制。",
                task_id,
                job_directory,
                archive_count,
                extracted_file_count,
                extracted_total_bytes,
            )
        if (
            active_limits.max_total_bytes > 0
            and extracted_total_bytes > active_limits.max_total_bytes
        ):
            return _limit_result(
                "解压产生的累计容量超过任务限制。",
                task_id,
                job_directory,
                archive_count,
                extracted_file_count,
                extracted_total_bytes,
            )

        volume_mapping = resolve_volume_groups(layer_files)
        volume_group_count += len(set(volume_mapping.values()))
        for layer_file in layer_files:
            if layer_file in volume_mapping and volume_mapping[layer_file] != layer_file:
                continue
            relative_path = layer_file.relative_to(layer_directory)
            logical_path = task.logical_parent / relative_path
            probe_count += 1
            _notify(progress, f"正在检查内层文件 {probe_count}...")
            probe_directory = job_directory / "probes" / f"{probe_count:06d}"
            group_members = tuple(
                member
                for member, group_start in volume_mapping.items()
                if group_start == layer_file
            )
            try:
                probe_source = _prepare_volume_source(
                    layer_file,
                    group_members,
                    probe_directory / "joined-volume",
                    active_limits.max_total_bytes,
                    progress,
                )
            except VolumeJoinError as error:
                return ExtractionJobResult(
                    status=JobStatus.BLOCKED,
                    message=str(error),
                    task_id=task_id,
                    method=root_probe.method,
                    work_directory=job_directory,
                    archive_count=archive_count,
                )
            probe = _probe_archive(
                probe_source,
                probe_directory,
                active_passwords,
                winrar,
                progress,
            )
            failure = _probe_failure_result(probe, task_id, job_directory, root=False)
            if failure is not None:
                return failure

            if probe.status is _ProbeStatus.ARCHIVE:
                assert probe.archive_path is not None
                assert probe.method is not None
                assert probe.password is not None
                used_apate = used_apate or probe.method is ExtractionMethod.APATE
                used_extension_alias = (
                    used_extension_alias or probe.method is ExtractionMethod.EXTENSION_ALIAS
                )
                used_embedded_zip = (
                    used_embedded_zip or probe.method is ExtractionMethod.EMBEDDED_ZIP
                )
                queue.append(
                    _ArchiveTask(
                        probe.archive_path,
                        _nested_output_parent(logical_path),
                        probe.method,
                        probe.password,
                    )
                )
                continue

            try:
                _copy_final_file(layer_file, staging_directory / logical_path)
            except OSError as error:
                return ExtractionJobResult(
                    status=JobStatus.BLOCKED,
                    message=f"最终文件发生路径冲突或无法写入：{error}",
                    task_id=task_id,
                    work_directory=job_directory,
                    archive_count=archive_count,
                )

    _notify(progress, "正在把最终文件移入输出目录...")
    try:
        output_path.mkdir(parents=True, exist_ok=True)
        final_directory = output_path / task_id
        if final_directory.exists():
            raise FileExistsError(f"结果目录已经存在：{final_directory}")
        shutil.move(str(staging_directory), str(final_directory))
        final_file_count, final_total_bytes = _measure_tree(final_directory)
    except OSError as error:
        return ExtractionJobResult(
            status=JobStatus.FAILED,
            message=f"解压成功，但移动到输出目录失败：{error}",
            task_id=task_id,
            work_directory=job_directory,
            archive_count=archive_count,
        )

    if (
        root_probe.method is ExtractionMethod.APATE
        and not used_embedded_zip
        and not used_extension_alias
    ):
        final_method = ExtractionMethod.APATE
    elif (
        root_probe.method is ExtractionMethod.EMBEDDED_ZIP
        and not used_apate
        and not used_extension_alias
    ):
        final_method = ExtractionMethod.EMBEDDED_ZIP
    elif (
        root_probe.method is ExtractionMethod.EXTENSION_ALIAS
        and not used_apate
        and not used_embedded_zip
    ):
        final_method = ExtractionMethod.EXTENSION_ALIAS
    elif used_apate or used_embedded_zip or used_extension_alias:
        final_method = ExtractionMethod.MIXED
    else:
        final_method = ExtractionMethod.DIRECT

    completion_message = "全部可识别层已解压，源文件未被修改。"
    if used_embedded_zip:
        completion_message += " 已处理文件外壳中的嵌入式 ZIP。"
    if used_extension_alias:
        completion_message += " 已自动使用正确扩展名的工作副本。"
    if password_attempt_count > 1:
        completion_message += f" 实际尝试了 {password_attempt_count} 次密码候选。"
    return ExtractionJobResult(
        status=JobStatus.COMPLETED,
        message=completion_message,
        task_id=task_id,
        method=final_method,
        output_directory=final_directory,
        work_directory=job_directory,
        file_count=final_file_count,
        total_bytes=final_total_bytes,
        archive_count=archive_count,
        volume_group_count=volume_group_count,
    )


def _extract_archive_with_passwords(
    archive: Path,
    destination: Path,
    attempt_root: Path,
    passwords: Sequence[str],
    winrar: WinRARAdapter,
    progress: ProgressCallback | None,
) -> tuple[WinRARResult, int]:
    """实际解压每个候选；成功码但无输出时继续下一个密码。"""

    empty_success = False
    total = len(passwords)
    for index, candidate in enumerate(passwords, start=1):
        password_text = candidate if candidate else "（无密码）"
        _notify(progress, f"正在尝试密码候选 {index}/{total}：{password_text}")
        attempt_directory = attempt_root / f"{index:02d}"
        extraction = winrar.extract_archive(archive, attempt_directory, candidate)
        if extraction.status is WinRARStatus.PASSWORD_REQUIRED:
            shutil.rmtree(attempt_directory, ignore_errors=True)
            continue
        if extraction.status is not WinRARStatus.EXTRACTED:
            return extraction, index
        try:
            has_output = any(attempt_directory.iterdir())
        except OSError as error:
            return WinRARResult(
                WinRARStatus.FAILED,
                f"无法检查 WinRAR 解压输出：{error}",
                extraction.exit_code,
            ), index
        if not has_output:
            empty_success = True
            shutil.rmtree(attempt_directory, ignore_errors=True)
            continue
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                return WinRARResult(
                    WinRARStatus.BLOCKED,
                    "解压层目标目录已存在，任务已停止以避免覆盖。",
                ), index
            shutil.move(str(attempt_directory), str(destination))
            shutil.rmtree(attempt_root, ignore_errors=True)
        except OSError as error:
            return WinRARResult(
                WinRARStatus.BLOCKED,
                f"无法整理密码候选的解压结果：{error}",
                extraction.exit_code,
            ), index
        _notify(progress, f"密码候选 {index}/{total} 已产生有效文件：{password_text}")
        return replace(extraction, output_directory=destination), index

    message = "所有密码候选均未匹配。"
    if empty_success:
        message = "WinRAR 对候选密码返回成功，但均没有产生任何文件；已继续尝试全部候选。"
    return WinRARResult(WinRARStatus.PASSWORD_REQUIRED, message), total


def _finalize_work_directory(result: ExtractionJobResult) -> ExtractionJobResult:
    """清除可重新生成的大文件；失败时只保留不含密码的小型说明。"""

    work_directory = result.work_directory
    if work_directory is None or not work_directory.exists():
        return result
    try:
        shutil.rmtree(work_directory)
        if result.status is JobStatus.COMPLETED:
            return replace(result, work_directory=None)
        work_directory.mkdir(parents=True, exist_ok=False)
        report = {
            "version": 1,
            "task_id": result.task_id,
            "status": result.status.value,
            "message": result.message,
            "archive_count": result.archive_count,
        }
        (work_directory / "failure.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result
    except OSError as error:
        return replace(
            result,
            message=f"{result.message} 临时工作目录清理失败：{error}",
        )


def _probe_archive(
    source: Path,
    probe_directory: Path,
    passwords: Sequence[str],
    winrar: WinRARAdapter,
    progress: ProgressCallback | None = None,
) -> _ProbeResult:
    check, matched_password = _test_archive_with_passwords(
        source,
        passwords,
        winrar,
        progress,
    )
    if check.status is WinRARStatus.VALID:
        _notify(progress, "当前文件已被 WinRAR 直接识别为压缩包。")
        return _ProbeResult(
            _ProbeStatus.ARCHIVE,
            check.message,
            source,
            ExtractionMethod.DIRECT,
            matched_password,
        )
    if check.status is WinRARStatus.PASSWORD_REQUIRED:
        return _ProbeResult(_ProbeStatus.PASSWORD_REQUIRED, check.message)
    if check.status is WinRARStatus.BLOCKED:
        return _ProbeResult(_ProbeStatus.BLOCKED, check.message)
    if check.status is WinRARStatus.FAILED:
        return _ProbeResult(_ProbeStatus.FAILED, check.message)
    _notify(progress, "WinRAR 未直接识别当前文件，继续检查其他兼容路线。")

    try:
        detected_suffix = detect_archive_suffix(source)
        if detected_suffix is not None and source.suffix.casefold() != detected_suffix:
            _notify(
                progress,
                f"文件内容是 {detected_suffix[1:].upper()}，正在生成正确后缀的工作副本...",
            )
            alias_path = copy_archive_alias(
                source,
                probe_directory / "extension-alias",
                detected_suffix,
            )
            alias_check, matched_password = _test_archive_with_passwords(
                alias_path,
                passwords,
                winrar,
                progress,
            )
            if alias_check.status is WinRARStatus.VALID:
                _notify(progress, "正确后缀工作副本已通过 WinRAR 检查。")
                return _ProbeResult(
                    _ProbeStatus.ARCHIVE,
                    "已通过正确扩展名的工作副本识别压缩包。",
                    alias_path,
                    ExtractionMethod.EXTENSION_ALIAS,
                    matched_password,
                )
            if alias_check.status is WinRARStatus.PASSWORD_REQUIRED:
                return _ProbeResult(_ProbeStatus.PASSWORD_REQUIRED, alias_check.message)
            if alias_check.status is WinRARStatus.BLOCKED:
                return _ProbeResult(_ProbeStatus.BLOCKED, alias_check.message)
            if alias_check.status is WinRARStatus.FAILED:
                return _ProbeResult(_ProbeStatus.FAILED, alias_check.message)
            _notify(progress, "正确后缀工作副本仍未通过检查，继续检查文件外壳。")
    except ArchiveAliasError as error:
        return _ProbeResult(_ProbeStatus.FAILED, str(error))

    try:
        embedded_info = find_embedded_zip(source)
        if embedded_info is not None:
            _notify(
                progress,
                f"发现嵌入式 ZIP，正在生成 {embedded_info.archive_size / 1024**2:.2f} MB 工作副本...",
            )
            embedded_path = copy_embedded_zip(
                source,
                probe_directory / "embedded.zip",
                embedded_info,
            )
            embedded_check, matched_password = _test_archive_with_passwords(
                embedded_path,
                passwords,
                winrar,
                progress,
            )
            if embedded_check.status is WinRARStatus.VALID:
                _notify(progress, "嵌入式 ZIP 工作副本已通过 WinRAR 检查。")
                return _ProbeResult(
                    _ProbeStatus.ARCHIVE,
                    "已从文件外壳中识别出嵌入式 ZIP。",
                    embedded_path,
                    ExtractionMethod.EMBEDDED_ZIP,
                    matched_password,
                )
            if embedded_check.status is WinRARStatus.PASSWORD_REQUIRED:
                return _ProbeResult(_ProbeStatus.PASSWORD_REQUIRED, embedded_check.message)
            if embedded_check.status is WinRARStatus.BLOCKED:
                return _ProbeResult(_ProbeStatus.BLOCKED, embedded_check.message)
            if embedded_check.status is WinRARStatus.FAILED:
                return _ProbeResult(_ProbeStatus.FAILED, embedded_check.message)
            _notify(progress, "嵌入式 ZIP 工作副本未通过检查，继续检查 Apate 特征。")
    except EmbeddedZipError as error:
        return _ProbeResult(_ProbeStatus.FAILED, str(error))

    if not looks_like_apate_archive(source):
        _notify(progress, "未发现可验证的 Apate 归档特征。")
        return _ProbeResult(
            _ProbeStatus.NOT_ARCHIVE,
            "未识别为直接压缩包、错误后缀压缩包、嵌入式 ZIP 或 Apate 伪装包。",
        )

    restored_path = probe_directory / "restored.bin"
    _notify(progress, "发现可能的 Apate 结构，正在生成独立还原副本...")
    restore = restore_apate_copy(source, restored_path)
    if restore.status is RestoreStatus.FAILED:
        return _ProbeResult(_ProbeStatus.FAILED, restore.message)
    if restore.status is RestoreStatus.BLOCKED:
        return _ProbeResult(_ProbeStatus.BLOCKED, restore.message)
    if restore.status is not RestoreStatus.RESTORED:
        return _ProbeResult(_ProbeStatus.NOT_ARCHIVE, restore.message)

    restored_check, matched_password = _test_archive_with_passwords(
        restored_path,
        passwords,
        winrar,
        progress,
    )
    if restored_check.status is WinRARStatus.VALID:
        _notify(progress, "Apate 还原副本已通过 WinRAR 检查。")
        return _ProbeResult(
            _ProbeStatus.ARCHIVE,
            "Apate 还原后已识别压缩包。",
            restored_path,
            ExtractionMethod.APATE,
            matched_password,
        )
    if restored_check.status is WinRARStatus.PASSWORD_REQUIRED:
        return _ProbeResult(_ProbeStatus.PASSWORD_REQUIRED, restored_check.message)
    if restored_check.status is WinRARStatus.BLOCKED:
        return _ProbeResult(_ProbeStatus.BLOCKED, restored_check.message)
    if restored_check.status is WinRARStatus.FAILED:
        return _ProbeResult(_ProbeStatus.FAILED, restored_check.message)
    return _ProbeResult(_ProbeStatus.NOT_ARCHIVE, restored_check.message)


def _prepare_volume_source(
    start: Path,
    members: tuple[Path, ...],
    destination_directory: Path,
    max_total_bytes: int,
    progress: ProgressCallback | None,
) -> Path:
    if len(members) < 2:
        return start
    ordered = ordered_raw_volume_members(start, members)
    if ordered is None:
        return start
    _notify(progress, f"正在自动归组并拼接 {len(ordered)} 个连续分卷...")
    if NUMBERED_EXTENSION_PATTERN.match(start.name) is not None:
        return join_numbered_extension_volumes(
            ordered,
            destination_directory,
            max_total_bytes=max_total_bytes,
        )
    return join_inferred_first_volume(
        start,
        ordered,
        destination_directory,
        max_total_bytes=max_total_bytes,
    )


def _ordered_passwords(primary: str, candidates: Sequence[str] | None) -> tuple[str, ...]:
    """任务密码优先，其次密码池，最后尝试无密码；保持顺序并去重。"""

    ordered: list[str] = []
    seen: set[str] = set()
    values: list[str] = []
    if primary:
        values.append(primary)
    if candidates:
        values.extend(candidates[:MAX_SAVED_PASSWORDS])
    values.append("")
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)


def _test_archive_with_passwords(
    source: Path,
    passwords: Sequence[str],
    winrar: WinRARAdapter,
    progress: ProgressCallback | None = None,
) -> tuple[WinRARResult, str | None]:
    """只在 WinRAR 明确报告密码错误时继续尝试下一项。"""

    last_password_error: WinRARResult | None = None
    total = len(passwords)
    for index, candidate in enumerate(passwords, start=1):
        password_text = candidate if candidate else "（无密码）"
        _notify(progress, f"正在测试归档密码候选 {index}/{total}：{password_text}")
        check = winrar.test_archive(source, candidate)
        if check.status is WinRARStatus.PASSWORD_REQUIRED:
            last_password_error = check
            continue
        matched = candidate if check.status is WinRARStatus.VALID else None
        return check, matched
    assert last_password_error is not None
    return last_password_error, None


def _probe_failure_result(
    probe: _ProbeResult,
    task_id: str,
    work_directory: Path,
    *,
    root: bool,
) -> ExtractionJobResult | None:
    if probe.status is _ProbeStatus.PASSWORD_REQUIRED:
        return _password_result(task_id, work_directory, 0)
    if probe.status is _ProbeStatus.BLOCKED:
        return ExtractionJobResult(
            JobStatus.BLOCKED,
            probe.message,
            task_id=task_id,
            work_directory=work_directory,
        )
    if probe.status is _ProbeStatus.FAILED:
        return ExtractionJobResult(
            JobStatus.FAILED,
            probe.message,
            task_id=task_id,
            work_directory=work_directory,
        )
    if root and probe.status is _ProbeStatus.NOT_ARCHIVE:
        return ExtractionJobResult(
            JobStatus.NOT_ARCHIVE,
            "直接格式、正确后缀副本、嵌入式 ZIP 和 Apate 兼容检查都未识别出可解压内容。",
            task_id=task_id,
            work_directory=work_directory,
        )
    return None


def _validate_locations(source: Path, work_root: Path, output_root: Path) -> str | None:
    source_directory = source.parent.resolve(strict=False)
    if _is_inside(work_root, source_directory) or _is_inside(output_root, source_directory):
        return "工作目录和输出目录不能放在输入文件所在目录内。"
    if _is_inside(work_root, output_root) or _is_inside(output_root, work_root):
        return "工作目录和输出目录必须彼此独立。"
    return None


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        return path == parent or path.is_relative_to(parent)
    except (OSError, ValueError):
        return False


def _make_task_id(source: Path) -> str:
    safe_stem = "".join(character if character.isalnum() else "-" for character in source.stem)
    safe_stem = safe_stem.strip("-")[:32] or "task"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{safe_stem}-{uuid4().hex[:6]}"


def _nested_output_parent(logical_archive_path: Path) -> Path:
    return logical_archive_path.parent / f"{logical_archive_path.name}.解压内容"


def _copy_final_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"目标已存在：{destination}")
    shutil.copy2(source, destination)


def _password_result(
    task_id: str,
    work_directory: Path,
    archive_count: int,
    *,
    message: str | None = None,
) -> ExtractionJobResult:
    return ExtractionJobResult(
        status=JobStatus.PASSWORD_REQUIRED,
        message=message or "某一层需要密码，或当前任务密码不正确。请重新输入后再运行。",
        task_id=task_id,
        work_directory=work_directory,
        archive_count=archive_count,
    )


def _limit_result(
    message: str,
    task_id: str,
    work_directory: Path,
    archive_count: int,
    file_count: int,
    total_bytes: int,
) -> ExtractionJobResult:
    return ExtractionJobResult(
        status=JobStatus.BLOCKED,
        message=message,
        task_id=task_id,
        work_directory=work_directory,
        file_count=file_count,
        total_bytes=total_bytes,
        archive_count=archive_count,
    )


def _measure_tree(root: Path) -> tuple[int, int]:
    file_count = 0
    total_bytes = 0
    for path in root.rglob("*"):
        if path.is_file():
            file_count += 1
            total_bytes += path.stat().st_size
    return file_count, total_bytes


def _notify(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)
