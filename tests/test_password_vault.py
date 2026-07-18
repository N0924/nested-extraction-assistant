"""密码池解析和本机明文持久化测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from password_vault import (
    PasswordVaultStore,
    parse_password_text,
)


class PasswordTextTests(unittest.TestCase):
    def test_newlines_and_pipe_create_an_ordered_deduplicated_pool(self) -> None:
        parsed = parse_password_text(" default-password\nsecond|third\ndefault-password ")

        self.assertEqual(parsed, ["default-password", "second", "third"])

    def test_rejects_more_than_fifty_passwords(self) -> None:
        text = "\n".join(f"password-{number}" for number in range(51))

        with self.assertRaises(ValueError):
            parse_password_text(text)


class PasswordVaultStoreTests(unittest.TestCase):
    def test_round_trip_writes_plaintext_for_user_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "password-vault.json"
            store = PasswordVaultStore(path)

            store.save(["default-secret", "second-secret"])

            serialized = path.read_text(encoding="utf-8")
            self.assertIn("default-secret", serialized)
            self.assertIn("second-secret", serialized)
            self.assertIn('"storage": "local-plaintext"', serialized)
            self.assertEqual(store.load(), ["default-secret", "second-secret"])

    def test_empty_pool_removes_the_plaintext_pool_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "password-vault.json"
            store = PasswordVaultStore(path)
            store.save(["temporary"])

            store.save([])

            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
