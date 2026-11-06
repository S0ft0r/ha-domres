"""Tests for versioning and delta archive behavior."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.build_release import BuildError, build_delta, build_component_zip, verify_current_state


class ReleaseBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "custom_components/domres").mkdir(parents=True)
        (self.root / "scripts").mkdir()
        (self.root / "tests").mkdir()
        (self.root / ".github/workflows").mkdir(parents=True)
        (self.root / "custom_components/domres/manifest.json").write_text(
            json.dumps({"domain": "domres", "version": "0.1.0"}, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.root / "custom_components/domres/__init__.py").write_text("# initial\n", encoding="utf-8")
        (self.root / "scripts/validate.py").write_text("# validator\n", encoding="utf-8")
        (self.root / "tests/test_smoke.py").write_text("# tests\n", encoding="utf-8")
        (self.root / ".github/workflows/validate.yml").write_text("name: validate\n", encoding="utf-8")
        (self.root / "README.md").write_text("Public documentation\n", encoding="utf-8")
        (self.root / "internal").mkdir()
        (self.root / "internal/decision.md").write_text("not for release\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_first_archive_includes_public_project_files_only(self) -> None:
        archive_path, changed, deleted = build_delta(self.root)
        self.assertEqual(deleted, [])
        self.assertIn("custom_components/domres/__init__.py", changed)
        self.assertIn("scripts/validate.py", changed)
        self.assertIn("tests/test_smoke.py", changed)
        self.assertIn(".github/workflows/validate.yml", changed)
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
        self.assertIn("README.md", names)
        self.assertNotIn("internal/decision.md", names)
        verify_current_state(self.root)

    def test_ci_bootstrap_can_create_first_archive_at_current_patch_version(self) -> None:
        manifest_path = self.root / "custom_components/domres/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = "0.1.4"
        manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

        with self.assertRaises(BuildError):
            build_delta(self.root)
        archive_path, changed, _ = build_delta(
            self.root,
            bootstrap_current_version=True,
        )

        self.assertEqual(archive_path.name, "domres_0.1.4_changed_files.zip")
        self.assertIn("custom_components/domres/__init__.py", changed)
        verify_current_state(self.root)

    def test_next_build_bumps_patch_and_keeps_previous_archive(self) -> None:
        first_archive, _, _ = build_delta(self.root)
        (self.root / "custom_components/domres/api.py").write_text("# changed\n", encoding="utf-8")
        second_archive, changed, _ = build_delta(self.root)
        manifest = json.loads(
            (self.root / "custom_components/domres/manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["version"], "0.1.1")
        self.assertEqual(first_archive.name, "domres_0.1.0_changed_files.zip")
        self.assertTrue(first_archive.is_file())
        self.assertEqual(second_archive.name, "domres_0.1.1_changed_files.zip")
        self.assertIn("custom_components/domres/api.py", changed)
        self.assertIn("custom_components/domres/manifest.json", changed)
        verify_current_state(self.root)

    def test_component_zip_has_domres_root(self) -> None:
        target = build_component_zip(self.root, "component.zip")
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
        self.assertIn("domres/manifest.json", names)
        self.assertIn("domres/__init__.py", names)

    def test_deleted_file_is_recorded_and_old_archive_is_retained(self) -> None:
        first_archive, _, _ = build_delta(self.root)
        (self.root / "scripts/validate.py").unlink()
        second_archive, _, deleted = build_delta(self.root)
        self.assertEqual(deleted, ["scripts/validate.py"])
        self.assertTrue(first_archive.is_file())
        with zipfile.ZipFile(second_archive) as archive:
            manifest = archive.read("DELETED_FILES.txt").decode("utf-8")
        self.assertEqual(manifest.strip(), "scripts/validate.py")

    def test_no_change_does_not_create_duplicate_build(self) -> None:
        build_delta(self.root)
        with self.assertRaises(BuildError):
            build_delta(self.root)


if __name__ == "__main__":
    unittest.main()
