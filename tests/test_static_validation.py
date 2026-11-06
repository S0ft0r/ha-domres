"""Project-level safety checks that protect against known review regressions."""

from __future__ import annotations

import unittest

from scripts.validate import validate


class StaticValidationTests(unittest.TestCase):
    def test_domres_source_has_no_known_static_review_errors(self) -> None:
        errors, _warnings = validate()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
