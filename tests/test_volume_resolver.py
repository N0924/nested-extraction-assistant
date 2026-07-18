"""分卷首卷选择规则测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from volume_resolver import expand_root_inputs, resolve_root_volume, resolve_volume_groups


class VolumeResolverTests(unittest.TestCase):
    def test_custom_numbered_group_uses_the_smallest_number(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = [root / name for name in ("0zip", "1zip", "2zip")]
            for path in files:
                path.write_bytes(b"part")

            selection = resolve_root_volume(files[2])

            self.assertEqual(selection.start, files[0])
            self.assertEqual(set(selection.members), set(files))

    def test_part_rar_group_uses_the_smallest_part(self) -> None:
        root = Path("C:/synthetic")
        files = [root / name for name in ("movie.part3.rar", "movie.part1.rar", "movie.part2.rar")]

        mapping = resolve_volume_groups(files)

        self.assertTrue(all(mapping[path] == root / "movie.part1.rar" for path in files))

    def test_standard_split_zip_uses_the_zip_file_not_z01(self) -> None:
        root = Path("C:/synthetic")
        files = [root / name for name in ("data.z01", "data.z02", "data.zip")]

        mapping = resolve_volume_groups(files)

        self.assertTrue(all(mapping[path] == root / "data.zip" for path in files))

    def test_numbered_7z_group_uses_the_001_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = [root / name for name in ("452.7z.001", "452.7z.002", "452.7z.003")]
            for path in files:
                path.write_bytes(b"part")

            selection = resolve_root_volume(files[2])

            self.assertEqual(selection.start, files[0])
            self.assertEqual(set(selection.members), set(files))

    def test_arbitrary_extension_numbered_group_uses_the_001_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = [root / name for name in ("bundle.mp4.001", "bundle.mp4.002", "bundle.mp4.003")]
            for path in files:
                path.write_bytes(b"part")

            selection = resolve_root_volume(files[2])

            self.assertEqual(selection.start, files[0])
            self.assertEqual(selection.members, tuple(files))

    def test_disguised_first_file_is_grouped_with_002_companions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "bundle.mp4"
            second = root / "bundle.002"
            third = root / "bundle.003"
            for path in (first, second, third):
                path.write_bytes(b"part")

            selection = resolve_root_volume(third)

            self.assertEqual(selection.start, first)
            self.assertEqual(selection.members, (first, second, third))

    def test_folder_adds_inferred_disguised_volume_group_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "bundle.txt"
            second = root / "bundle.002"
            third = root / "bundle.003"
            for path in (first, second, third):
                path.write_bytes(b"part")

            self.assertEqual(expand_root_inputs([root]), [first])

    def test_ambiguous_first_files_are_not_guessed(self) -> None:
        root = Path("C:/synthetic")
        movie = root / "bundle.mp4"
        text = root / "bundle.txt"
        second = root / "bundle.002"

        mapping = resolve_volume_groups([movie, text, second])

        self.assertNotIn(movie, mapping)
        self.assertNotIn(text, mapping)
        self.assertNotIn(second, mapping)

    def test_four_digit_year_suffixes_are_not_treated_as_volumes(self) -> None:
        root = Path("C:/synthetic")
        reports = [root / "report.2024", root / "report.2025"]

        self.assertEqual(resolve_volume_groups(reports), {})

    def test_folder_input_adds_each_top_level_archive_group_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            volumes = [root / name for name in ("452.7z.001", "452.7z.002", "452.7z.003")]
            ordinary = root / "disguised.mp4"
            nested = root / "nested"
            nested.mkdir()
            nested_archive = nested / "ignored.zip"
            for path in [*volumes, ordinary, nested_archive]:
                path.write_bytes(b"content")

            expanded = expand_root_inputs([root])

            self.assertEqual(expanded, [volumes[0], ordinary])

    def test_explicit_numbered_volume_is_normalized_to_the_first_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "sample.7z.001"
            second = root / "sample.7z.002"
            first.write_bytes(b"first")
            second.write_bytes(b"second")

            expanded = expand_root_inputs([second])

            self.assertEqual(expanded, [first])

    def test_unrelated_single_numbered_archive_is_not_treated_as_a_volume(self) -> None:
        path = Path("C:/synthetic/video1.zip")

        self.assertEqual(resolve_volume_groups([path]), {})


if __name__ == "__main__":
    unittest.main()
