from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.services.subject_catalog import SubjectCatalog


class SubjectCatalogTest(unittest.TestCase):
    def test_unknown_runs_are_collected_in_other_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = SubjectCatalog(directory)
            catalog.ensure_runs({
                "auto-20261027-210716-aaaaaa": "2026-10-27",
                "auto-20261027-210725-bbbbbb": "2026-10-27",
            })

            first = catalog.assignment_for_run("auto-20261027-210716-aaaaaa")
            second = catalog.assignment_for_run("auto-20261027-210725-bbbbbb")

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertEqual(first.subject_id, second.subject_id)
            self.assertEqual("其他", first.display_name)
            self.assertEqual("其他", second.display_name)

    def test_explicit_same_name_groups_runs_and_rename_preserves_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = SubjectCatalog(root)
            first = catalog.assign_run(
                "auto-20261024-145545-aaaaaa",
                "2026-10-24",
                "受试者1号",
            )
            second = catalog.assign_run(
                "auto-20261029-190545-bbbbbb",
                "2026-10-29",
                "受试者1号",
            )
            catalog.rename_subject(first.subject_id, "张三")
            reloaded = SubjectCatalog(root)

            first_after = reloaded.assignment_for_run("auto-20261024-145545-aaaaaa")
            second_after = reloaded.assignment_for_run("auto-20261029-190545-bbbbbb")
            payload = json.loads(reloaded.path.read_text(encoding="utf-8"))

            self.assertEqual(first.subject_id, second.subject_id)
            self.assertEqual(first.subject_id, first_after.subject_id)
            self.assertEqual(first.subject_id, second_after.subject_id)
            self.assertEqual("张三", first_after.display_name)
            self.assertEqual(1, payload["schema_version"])

    def test_duplicate_name_is_rejected_during_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = SubjectCatalog(directory)
            first = catalog.assign_run("run-a", "2026-10-24", "甲")
            catalog.assign_run("run-b", "2026-10-25", "乙")

            with self.assertRaisesRegex(ValueError, "已属于另一位"):
                catalog.rename_subject(first.subject_id, "乙")


if __name__ == "__main__":
    unittest.main()
