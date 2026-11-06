"""Project-level safety checks that protect against known review regressions."""

from __future__ import annotations

import unittest
from pathlib import Path

from scripts.validate import validate


class StaticValidationTests(unittest.TestCase):
    def test_domres_source_has_no_known_static_review_errors(self) -> None:
        errors, _warnings = validate()
        self.assertEqual(errors, [])

    def test_retired_indication_binary_sensor_is_not_forwarded_and_is_cleaned_up(self) -> None:
        component = Path(__file__).resolve().parents[1] / "custom_components/domres"
        setup_source = (component / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("PLATFORMS: list[Platform] = [Platform.SENSOR]", setup_source)
        self.assertIn("async_entries_for_config_entry", setup_source)
        self.assertIn("registry.async_remove", setup_source)
        self.assertFalse((component / "binary_sensor.py").exists())


if __name__ == "__main__":
    unittest.main()
