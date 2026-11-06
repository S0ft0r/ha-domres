"""Unit tests for read-only response parsing (without requiring Home Assistant)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components/domres"
PACKAGE_NAME = "_domres_test_package"
_test_package = types.ModuleType(PACKAGE_NAME)
_test_package.__path__ = [str(COMPONENT)]
sys.modules.setdefault(PACKAGE_NAME, _test_package)
_spec = importlib.util.spec_from_file_location(
    f"{PACKAGE_NAME}.data", COMPONENT / "data.py"
)
assert _spec is not None and _spec.loader is not None
_data = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _data
_spec.loader.exec_module(_data)

login_type = _data.login_type
normalize_meters = _data.normalize_meters
numeric_value = _data.numeric_value
integer_days = _data.integer_days
russian_day_unit = _data.russian_day_unit


class LoginTypeTests(unittest.TestCase):
    def test_detects_email_automatically(self) -> None:
        self.assertEqual(login_type("owner@example.org"), "EMAIL")

    def test_detects_phone_automatically(self) -> None:
        self.assertEqual(login_type("+7 999 123-45-67"), "PHONE")


class NumericValueTests(unittest.TestCase):
    def test_parses_wrapped_zero_and_decimal_comma(self) -> None:
        self.assertEqual(numeric_value({"value": 0}), 0.0)
        self.assertEqual(numeric_value("1 234,56"), 1234.56)

    def test_rejects_boolean_and_non_numeric_text(self) -> None:
        self.assertIsNone(numeric_value(True))
        self.assertIsNone(numeric_value("нет данных"))


class DayValueTests(unittest.TestCase):
    def test_parses_only_whole_days_as_integer(self) -> None:
        self.assertEqual(integer_days("19"), 19)
        self.assertEqual(integer_days("19.0"), 19)
        self.assertIsNone(integer_days("19.5"))
        self.assertIsNone(integer_days("нет данных"))

    def test_russian_day_unit_uses_correct_plural_form(self) -> None:
        cases = {
            1: "день",
            2: "дня",
            4: "дня",
            5: "дней",
            11: "дней",
            14: "дней",
            21: "день",
            22: "дня",
            25: "дней",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(russian_day_unit(value), expected)


class MeterNormalizationTests(unittest.TestCase):
    def test_normalizes_known_pskov_style_meter_shape(self) -> None:
        result = normalize_meters(
            [
                {
                    "id": {"provider": "provider-1", "registration": "reg-42"},
                    "serial": "SN-7",
                    "subservice": {"name": "Электроэнергия"},
                    "indications": [
                        {
                            "scaleId": 3,
                            "scaleName": "День",
                            "previousReading": {"value": "125.4"},
                            "unit": "kWh",
                            "previousReadingDate": "2026-10-21",
                        }
                    ],
                }
            ]
        )
        self.assertEqual(len(result), 1)
        meter = result[0]
        self.assertEqual(meter["registration"], "reg-42")
        self.assertEqual(meter["serial"], "SN-7")
        self.assertEqual(meter["service"], "Электроэнергия")
        self.assertEqual(meter["scales"][0]["previous_reading"], 125.4)
        self.assertEqual(meter["scales"][0]["unit"], "kWh")

    def test_missing_meter_payload_is_empty(self) -> None:
        self.assertEqual(normalize_meters(None), [])


if __name__ == "__main__":
    unittest.main()
