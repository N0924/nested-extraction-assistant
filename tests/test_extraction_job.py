"""单根文件解压任务的无私密测试。"""

from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from extraction_job import ExtractionLimits, ExtractionMethod, JobStatus, run_extraction_job
from winrar_adapter import WinRARAdapter, WinRARResult, WinRARStatus, find_winrar


def _make_apate_like_file(original: bytes, mask: bytes) -> bytes:
    disguised = bytearray(original)
    disguised[: len(mask)] = mask
    disguised.extend(original[: len(mask)][::-1])
    disguised.extend(len(mask).to_bytes(4, byteorder="little"))
    return bytes(disguised)


class FakeWinRARAdapter:
    def __init__(self) -> None:
        self.tested_paths: list[Path] = []
        self.received_passwords: list[str] = []

    def test_archive(self, archive: str | Path, password: str) -> WinRARResult:
        archive_path = Path(archive)
        self.tested_paths.append(archive_path)
        self.received_passwords.append(password)
        if archive_path.read_bytes().startswith(b"PK\x03\x04"):
            return WinRARResult(WinRARStatus.VALID, "可读取", 0)
        return WinRARResult(WinRARStatus.INVALID_ARCHIVE, "不可读取", 1)

    def extract_archive(
        self,
        archive: str | Path,
        destination: str | Path,
        password: str,
    ) -> WinRARResult:
        destination_path = Path(destination)
        destination_path.mkdir(parents=True)
        (destination_path / "result.txt").write_text("finished", encoding="utf-8")
        return WinRARResult(
            WinRARStatus.EXTRACTED,
            "解压完成",
            0,
            output_directory=destination_path,
        )


class PasswordWinRARAdapter(FakeWinRARAdapter):
    def test_archive(self, archive: str | Path, password: str) -> WinRARResult:
        return WinRARResult(WinRARStatus.PASSWORD_REQUIRED, "需要密码", 11)


class CandidatePasswordAdapter(FakeWinRARAdapter):
    def __init__(self, accepted_password: str) -> None:
        super().__init__()
        self.accepted_password = accepted_password
        self.extraction_passwords: list[str] = []
        self.root_password_attempts: list[str] = []

    def test_archive(self, archive: str | Path, password: str) -> WinRARResult:
        self.received_passwords.append(password)
        if Path(archive).name == "result.txt":
            return WinRARResult(WinRARStatus.INVALID_ARCHIVE, "不是压缩包", 1)
        self.root_password_attempts.append(password)
        if password == self.accepted_password:
            return WinRARResult(WinRARStatus.VALID, "可读取", 0)
        return WinRARResult(WinRARStatus.PASSWORD_REQUIRED, "需要密码", 11)

    def extract_archive(
        self,
        archive: str | Path,
        destination: str | Path,
        password: str,
    ) -> WinRARResult:
        self.extraction_passwords.append(password)
        if password != self.accepted_password:
            return WinRARResult(WinRARStatus.PASSWORD_REQUIRED, "需要密码", 11)
        return super().extract_archive(archive, destination, password)


class EmptySuccessForWrongPasswordAdapter(FakeWinRARAdapter):
    """模拟 WinRAR 对错误密码返回成功码但不产生文件。"""

    def __init__(self) -> None:
        super().__init__()
        self.extraction_passwords: list[str] = []

    def test_archive(self, archive: str | Path, password: str) -> WinRARResult:
        self.received_passwords.append(password)
        if Path(archive).name == "result.txt":
            return WinRARResult(WinRARStatus.INVALID_ARCHIVE, "not an archive", 1)
        return WinRARResult(WinRARStatus.VALID, "test returned success", 0)

    def extract_archive(
        self,
        archive: str | Path,
        destination: str | Path,
        password: str,
    ) -> WinRARResult:
        self.extraction_passwords.append(password)
        target = Path(destination)
        target.mkdir(parents=True, exist_ok=True)
        if password == "second-password":
            (target / "result.txt").write_text("second candidate worked", encoding="utf-8")
        return WinRARResult(WinRARStatus.EXTRACTED, "extract returned success", 0, target)


class ExtensionSensitiveAdapter(FakeWinRARAdapter):
    """模拟只能在扩展名正确时识别文件的解压器。"""

    def test_archive(self, archive: str | Path, password: str) -> WinRARResult:
        archive_path = Path(archive)
        self.tested_paths.append(archive_path)
        self.received_passwords.append(password)
        if archive_path.suffix.casefold() == ".zip" and archive_path.read_bytes().startswith(
            b"PK\x03\x04"
        ):
            return WinRARResult(WinRARStatus.VALID, "正确后缀可读取", 0)
        return WinRARResult(WinRARStatus.INVALID_ARCHIVE, "当前文件名不可读取", 1)


class NestedWinRARAdapter(FakeWinRARAdapter):
    def extract_archive(
        self,
        archive: str | Path,
        destination: str | Path,
        password: str,
    ) -> WinRARResult:
        destination_path = Path(destination)
        destination_path.mkdir(parents=True)
        payload = Path(archive).read_bytes()
        if b"root archive" in payload:
            (destination_path / "inner.weird").write_bytes(b"PK\x03\x04inner archive")
        else:
            (destination_path / "result.txt").write_text("nested finished", encoding="utf-8")
        return WinRARResult(
            WinRARStatus.EXTRACTED,
            "解压完成",
            0,
            output_directory=destination_path,
        )


class PasswordOnInnerAdapter(NestedWinRARAdapter):
    def test_archive(self, archive: str | Path, password: str) -> WinRARResult:
        if b"inner archive" in Path(archive).read_bytes():
            return WinRARResult(WinRARStatus.PASSWORD_REQUIRED, "需要密码", 11)
        return super().test_archive(archive, password)


class MultipartWinRARAdapter(FakeWinRARAdapter):
    def extract_archive(
        self,
        archive: str | Path,
        destination: str | Path,
        password: str,
    ) -> WinRARResult:
        destination_path = Path(destination)
        destination_path.mkdir(parents=True)
        if Path(archive).name == "root.zip":
            (destination_path / "0zip").write_bytes(b"PK\x03\x04first volume")
            (destination_path / "1zip").write_bytes(b"companion volume")
            (destination_path / "2zip").write_bytes(b"companion volume")
        else:
            (destination_path / "result.txt").write_text("volume finished", encoding="utf-8")
        return WinRARResult(WinRARStatus.EXTRACTED, "解压完成", 0, destination_path)


class EmptyExtractionAdapter(FakeWinRARAdapter):
    def extract_archive(
        self,
        archive: str | Path,
        destination: str | Path,
        password: str,
    ) -> WinRARResult:
        destination_path = Path(destination)
        destination_path.mkdir(parents=True)
        return WinRARResult(WinRARStatus.EXTRACTED, "解压完成", 0, destination_path)


class ExtractionJobTests(unittest.TestCase):
    def test_retries_a_header_verified_archive_with_the_correct_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source" / "archive.txt"
            source.parent.mkdir()
            payload = b"PK\x03\x04plain archive bytes"
            source.write_bytes(payload)

            adapter = ExtensionSensitiveAdapter()
            result = run_extraction_job(
                source=source,
                work_root=root / "work",
                output_root=root / "output",
                password="",
                winrar=adapter,
            )

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(result.method, ExtractionMethod.EXTENSION_ALIAS)
            self.assertEqual(source.read_bytes(), payload)
            self.assertEqual(adapter.tested_paths[0], source)
            self.assertEqual(adapter.tested_paths[1].suffix, ".zip")

    def test_extracts_zip_embedded_between_mp4_prefix_and_trailer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("inside.txt", "hidden archive")
            source = source_dir / "disguised.mp4"
            source.write_bytes(
                b"\x00\x00\x00\x1cftypisom"
                + b"video-mask" * 20
                + zip_buffer.getvalue()
                + b"\x00\x00\x00\x08free"
            )
            original = source.read_bytes()

            result = run_extraction_job(
                source=source,
                work_root=root / "work",
                output_root=root / "output",
                password="",
                winrar=FakeWinRARAdapter(),
            )

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(result.method, ExtractionMethod.EMBEDDED_ZIP)
            self.assertEqual(source.read_bytes(), original)

    def test_joins_numbered_volumes_before_detecting_an_embedded_zip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("inside.txt", "hidden split archive")
            disguised = (
                b"\x00\x00\x00\x1cftypisom"
                + b"video-mask" * 20
                + zip_buffer.getvalue()
                + b"\x00\x00\x00\x08free"
            )
            split_at = len(disguised) // 3
            members = (
                source_dir / "bundle.zip.001",
                source_dir / "bundle.zip.002",
                source_dir / "bundle.zip.003",
            )
            members[0].write_bytes(disguised[:split_at])
            members[1].write_bytes(disguised[split_at : split_at * 2])
            members[2].write_bytes(disguised[split_at * 2 :])
            original_members = tuple(member.read_bytes() for member in members)

            result = run_extraction_job(
                source=members[1],
                work_root=root / "work",
                output_root=root / "output",
                password="",
                winrar=FakeWinRARAdapter(),
            )

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(result.method, ExtractionMethod.EMBEDDED_ZIP)
            self.assertEqual(
                tuple(member.read_bytes() for member in members),
                original_members,
            )

    def test_infers_a_disguised_first_volume_and_002_companions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("inside.txt", "automatic irregular volume grouping")
            disguised = (
                b"\x00\x00\x00\x1cftypisom"
                + b"video-mask" * 20
                + zip_buffer.getvalue()
                + b"\x00\x00\x00\x08free"
            )
            split_at = len(disguised) // 3
            members = (
                source_dir / "bundle.mp4",
                source_dir / "bundle.002",
                source_dir / "bundle.003",
            )
            members[0].write_bytes(disguised[:split_at])
            members[1].write_bytes(disguised[split_at : split_at * 2])
            members[2].write_bytes(disguised[split_at * 2 :])
            original_members = tuple(member.read_bytes() for member in members)

            result = run_extraction_job(
                source=members[2],
                work_root=root / "work",
                output_root=root / "output",
                password="",
                winrar=FakeWinRARAdapter(),
            )

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(result.method, ExtractionMethod.EMBEDDED_ZIP)
            self.assertEqual(
                tuple(member.read_bytes() for member in members),
                original_members,
            )

    def test_success_exit_with_empty_output_is_not_reported_as_completed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            source = source_dir / "archive.zip"
            source.write_bytes(b"PK\x03\x04synthetic archive")

            result = run_extraction_job(
                source=source,
                work_root=root / "work",
                output_root=root / "output",
                password="",
                winrar=EmptyExtractionAdapter(),
            )

            self.assertEqual(result.status, JobStatus.PASSWORD_REQUIRED)
            self.assertIn("没有产生任何文件", result.message)
            self.assertIsNone(result.output_directory)

    def test_password_pool_stops_after_the_first_successful_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            source = source_dir / "encrypted.rar"
            source.write_bytes(b"Rar! synthetic")
            adapter = CandidatePasswordAdapter("second-password")

            result = run_extraction_job(
                source=source,
                work_root=root / "work",
                output_root=root / "output",
                password="",
                password_candidates=["default-password", "second-password", "unused-password"],
                winrar=adapter,
            )

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(
                adapter.root_password_attempts,
                ["default-password", "second-password"],
            )
            self.assertEqual(adapter.extraction_passwords, ["default-password", "second-password"])

    def test_task_password_is_tried_before_the_saved_pool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            source = source_dir / "encrypted.rar"
            source.write_bytes(b"Rar! synthetic")
            adapter = CandidatePasswordAdapter("task-password")

            result = run_extraction_job(
                source=source,
                work_root=root / "work",
                output_root=root / "output",
                password="task-password",
                password_candidates=["default-password", "other-password"],
                winrar=adapter,
            )

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(adapter.root_password_attempts, ["task-password"])
            self.assertEqual(adapter.extraction_passwords, ["task-password"])

    def test_direct_extraction_preserves_source_and_creates_isolated_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            source = source_dir / "archive.zip"
            source.write_bytes(b"PK\x03\x04synthetic archive")
            original = source.read_bytes()
            adapter = FakeWinRARAdapter()

            result = run_extraction_job(
                source=source,
                work_root=root / "work",
                output_root=root / "output",
                password="task-password",
                winrar=adapter,
            )

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(result.method, ExtractionMethod.DIRECT)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual((result.output_directory / "result.txt").read_text(), "finished")
            self.assertTrue(adapter.received_passwords)
            self.assertTrue(
                all(password == "task-password" for password in adapter.received_passwords)
            )

    def test_empty_success_with_wrong_password_continues_to_second_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_directory = root / "source"
            source_directory.mkdir()
            source = source_directory / "archive.7z"
            source.write_bytes(b"7z\xbc\xaf'\x1csynthetic")
            adapter = EmptySuccessForWrongPasswordAdapter()
            progress_messages: list[str] = []

            result = run_extraction_job(
                source=source,
                work_root=root / "work",
                output_root=root / "output",
                password="",
                password_candidates=["first-password", "second-password"],
                winrar=adapter,
                progress=progress_messages.append,
            )

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(adapter.extraction_passwords, ["first-password", "second-password"])
            self.assertEqual(
                (result.output_directory / "result.txt").read_text(encoding="utf-8"),
                "second candidate worked",
            )
            self.assertTrue(any("2/3" in message for message in progress_messages))

    def test_uses_apate_restore_only_after_direct_test_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            source = source_dir / "masked.mp4"
            original_archive = b"PK\x03\x04synthetic archive payload"
            disguised = _make_apate_like_file(original_archive, b"video-mask")
            source.write_bytes(disguised)
            adapter = FakeWinRARAdapter()

            result = run_extraction_job(
                source=source,
                work_root=root / "work",
                output_root=root / "output",
                password="same-password",
                winrar=adapter,
            )

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(result.method, ExtractionMethod.APATE)
            self.assertEqual(source.read_bytes(), disguised)
            self.assertGreaterEqual(len(adapter.tested_paths), 2)
            self.assertNotEqual(adapter.tested_paths[1], source)

    def test_reports_password_problem_without_creating_final_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            source = source_dir / "encrypted.rar"
            source.write_bytes(b"Rar!\x1a\x07\x01\x00synthetic")

            result = run_extraction_job(
                source=source,
                work_root=root / "work",
                output_root=root / "output",
                password="wrong",
                winrar=PasswordWinRARAdapter(),
            )

            self.assertEqual(result.status, JobStatus.PASSWORD_REQUIRED)
            self.assertIsNone(result.output_directory)
            self.assertIsNotNone(result.work_directory)
            self.assertEqual(
                sorted(path.name for path in result.work_directory.iterdir()),
                ["failure.json"],
            )

    def test_blocks_work_or_output_inside_the_source_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            source = source_dir / "archive.zip"
            source.write_bytes(b"PK\x03\x04synthetic archive")

            result = run_extraction_job(
                source=source,
                work_root=source_dir / "work",
                output_root=root / "output",
                password="",
                winrar=FakeWinRARAdapter(),
            )

            self.assertEqual(result.status, JobStatus.BLOCKED)

    def test_recursively_extracts_an_inner_archive_with_the_same_password(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            source = source_dir / "root.zip"
            source.write_bytes(b"PK\x03\x04root archive")
            adapter = NestedWinRARAdapter()

            result = run_extraction_job(
                source=source,
                work_root=root / "work",
                output_root=root / "output",
                password="inherited-password",
                winrar=adapter,
            )

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(result.archive_count, 2)
            self.assertEqual(
                (
                    result.output_directory
                    / "inner.weird.解压内容"
                    / "result.txt"
                ).read_text(encoding="utf-8"),
                "nested finished",
            )
            self.assertTrue(
                all(value == "inherited-password" for value in adapter.received_passwords)
            )

    def test_stops_if_an_inner_archive_needs_a_different_password(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            source = source_dir / "root.zip"
            source.write_bytes(b"PK\x03\x04root archive")

            result = run_extraction_job(
                source=source,
                work_root=root / "work",
                output_root=root / "output",
                password="wrong-for-inner",
                winrar=PasswordOnInnerAdapter(),
            )

            self.assertEqual(result.status, JobStatus.PASSWORD_REQUIRED)
            self.assertIsNone(result.output_directory)

    def test_stops_when_the_user_file_limit_is_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            source = source_dir / "root.zip"
            source.write_bytes(b"PK\x03\x04root archive")

            result = run_extraction_job(
                source=source,
                work_root=root / "work",
                output_root=root / "output",
                password="",
                winrar=NestedWinRARAdapter(),
                limits=ExtractionLimits(max_files=1, max_total_bytes=1024, max_archives=10),
            )

            self.assertEqual(result.status, JobStatus.BLOCKED)
            self.assertIsNone(result.output_directory)

    def test_zero_resource_limits_mean_unlimited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source" / "root.zip"
            source.parent.mkdir()
            source.write_bytes(b"PK\x03\x04root archive")

            result = run_extraction_job(
                source=source,
                work_root=root / "work",
                output_root=root / "output",
                password="",
                winrar=NestedWinRARAdapter(),
                limits=ExtractionLimits(max_files=0, max_total_bytes=0, max_archives=0),
            )

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertGreater(result.archive_count, 1)

    def test_only_processes_the_smallest_custom_volume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            source = source_dir / "root.zip"
            source.write_bytes(b"PK\x03\x04root archive")
            adapter = MultipartWinRARAdapter()

            result = run_extraction_job(
                source=source,
                work_root=root / "work",
                output_root=root / "output",
                password="",
                winrar=adapter,
            )

            tested_names = [path.name for path in adapter.tested_paths]
            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(result.volume_group_count, 1)
            self.assertIn("0zip", tested_names)
            self.assertNotIn("1zip", tested_names)
            self.assertNotIn("2zip", tested_names)
            self.assertEqual(
                (result.output_directory / "0zip.解压内容" / "result.txt").read_text(),
                "volume finished",
            )


@unittest.skipUnless(find_winrar() is not None, "本机未安装 WinRAR")
class ExtractionJobIntegrationTests(unittest.TestCase):
    def test_real_winrar_extracts_a_zip_split_as_numbered_extension_parts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            original_archive = source_dir / "sample.zip"
            with zipfile.ZipFile(original_archive, "w") as archive:
                archive.writestr("result.txt", "joined volume result")
            archive_bytes = original_archive.read_bytes()
            split_at = len(archive_bytes) // 2
            first = source_dir / "sample.zip.001"
            second = source_dir / "sample.zip.002"
            first.write_bytes(archive_bytes[:split_at])
            second.write_bytes(archive_bytes[split_at:])
            original_archive.unlink()
            original_parts = (first.read_bytes(), second.read_bytes())

            result = run_extraction_job(
                source=second,
                work_root=root / "work",
                output_root=root / "output",
                password="",
                winrar=WinRARAdapter(find_winrar()),
            )

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(
                (result.output_directory / "result.txt").read_text(encoding="utf-8"),
                "joined volume result",
            )
            self.assertEqual((first.read_bytes(), second.read_bytes()), original_parts)
            self.assertIsNone(result.work_directory)

    def test_real_winrar_uses_the_first_matching_saved_password(self) -> None:
        rar_executable = find_winrar().with_name("Rar.exe")
        if not rar_executable.is_file():
            self.skipTest("本机未安装 Rar.exe")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            payload = source_dir / "payload.txt"
            payload.write_text("password pool result", encoding="utf-8")
            archive = source_dir / "encrypted.rar"
            created = subprocess.run(
                [
                    str(rar_executable),
                    "a",
                    "-inul",
                    "-hppool-success-password",
                    str(archive),
                    payload.name,
                ],
                cwd=source_dir,
                check=False,
                shell=False,
            )
            self.assertEqual(created.returncode, 0)

            result = run_extraction_job(
                source=archive,
                work_root=root / "work",
                output_root=root / "output",
                password="task-password-is-wrong",
                password_candidates=[
                    "default-password-is-wrong",
                    "pool-success-password",
                    "unused-password",
                ],
                winrar=WinRARAdapter(find_winrar()),
            )

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(
                (result.output_directory / payload.name).read_text(encoding="utf-8"),
                "password pool result",
            )

    def test_real_winrar_selects_the_first_encrypted_rar_volume(self) -> None:
        rar_executable = find_winrar().with_name("Rar.exe")
        if not rar_executable.is_file():
            self.skipTest("本机未安装 Rar.exe")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            payload = source_dir / "payload.bin"
            payload_bytes = bytes(range(256)) * 256
            payload.write_bytes(payload_bytes)
            archive = source_dir / "volumes.rar"
            created = subprocess.run(
                [
                    str(rar_executable),
                    "a",
                    "-inul",
                    "-m0",
                    "-v16k",
                    "-hpvolume-password",
                    str(archive),
                    payload.name,
                ],
                cwd=source_dir,
                check=False,
                shell=False,
            )
            self.assertEqual(created.returncode, 0)
            parts = sorted(source_dir.glob("volumes.part*.rar"))
            self.assertGreaterEqual(len(parts), 2)

            wrong_password_result = run_extraction_job(
                source=parts[-1],
                work_root=root / "wrong-work",
                output_root=root / "wrong-output",
                password="wrong-password",
                winrar=WinRARAdapter(find_winrar()),
            )
            self.assertEqual(wrong_password_result.status, JobStatus.PASSWORD_REQUIRED)
            self.assertIsNone(wrong_password_result.output_directory)

            result = run_extraction_job(
                source=parts[-1],
                work_root=root / "work",
                output_root=root / "output",
                password="volume-password",
                winrar=WinRARAdapter(find_winrar()),
            )

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(result.volume_group_count, 1)
            self.assertEqual(result.archive_count, 1)
            self.assertEqual((result.output_directory / "payload.bin").read_bytes(), payload_bytes)

    def test_real_winrar_recursively_extracts_zip_inside_zip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            source = source_dir / "outer.zip"

            inner_buffer = io.BytesIO()
            with zipfile.ZipFile(inner_buffer, "w") as inner_zip:
                inner_zip.writestr("deep/result.txt", "nested real result")
            with zipfile.ZipFile(source, "w") as outer_zip:
                outer_zip.writestr("outer.txt", "outer final file")
                outer_zip.writestr("inner.data", inner_buffer.getvalue())
            original = source.read_bytes()

            result = run_extraction_job(
                source=source,
                work_root=root / "work",
                output_root=root / "output",
                password="",
                winrar=WinRARAdapter(find_winrar()),
            )

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(result.archive_count, 2)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(
                (
                    result.output_directory
                    / "inner.data.解压内容"
                    / "deep"
                    / "result.txt"
                ).read_text(encoding="utf-8"),
                "nested real result",
            )
            self.assertEqual(
                (result.output_directory / "outer.txt").read_text(encoding="utf-8"),
                "outer final file",
            )

    def test_real_winrar_completes_the_full_direct_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            source = source_dir / "archive-named-video.mp4"
            with zipfile.ZipFile(source, "w") as zip_file:
                zip_file.writestr("folder/result.txt", "direct result")
            original = source.read_bytes()

            result = run_extraction_job(
                source=source,
                work_root=root / "work",
                output_root=root / "output",
                password="",
                winrar=WinRARAdapter(find_winrar()),
            )

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(result.method, ExtractionMethod.DIRECT)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(
                (result.output_directory / "folder" / "result.txt").read_text(encoding="utf-8"),
                "direct result",
            )

    def test_real_winrar_completes_the_full_apate_fallback_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            original_archive = root / "original.zip"
            with zipfile.ZipFile(original_archive, "w") as zip_file:
                zip_file.writestr("restored/result.txt", "apate result")
            original_bytes = original_archive.read_bytes()
            source = source_dir / "masked.txt"
            disguised = _make_apate_like_file(original_bytes, b"apate-test-mask")
            source.write_bytes(disguised)

            result = run_extraction_job(
                source=source,
                work_root=root / "work",
                output_root=root / "output",
                password="",
                winrar=WinRARAdapter(find_winrar()),
            )

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(result.method, ExtractionMethod.APATE)
            self.assertEqual(source.read_bytes(), disguised)
            self.assertEqual(
                (result.output_directory / "restored" / "result.txt").read_text(encoding="utf-8"),
                "apate result",
            )


if __name__ == "__main__":
    unittest.main()
