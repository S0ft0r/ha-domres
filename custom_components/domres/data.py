"""Parsing helpers for DomRes responses."""

from __future__ import annotations

import math
from typing import Any


def login_type(login: str) -> str:
    """Return the API login kind, automatically detecting email vs phone."""
    return "EMAIL" if "@" in login else "PHONE"


def first_present(*values: Any) -> Any:
    """Return the first non-None value, preserving meaningful zero/false values."""
    for value in values:
        if value is not None:
            return value
    return None


def numeric_value(value: Any) -> float | None:
    """Read a numeric API field, including common {value: ...} wrappers."""
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(" ", "").replace(",", ".")
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def reading_not_below_previous(value: Any, previous: Any) -> bool:
    """Return whether a proposed reading is numeric and not below the last value."""
    proposed = numeric_value(value)
    if proposed is None or not math.isfinite(proposed):
        return False
    previous_value = numeric_value(previous)
    return previous_value is None or (
        math.isfinite(previous_value) and proposed >= previous_value
    )


def integer_days(value: Any) -> int | None:
    """Parse the API's whole-day count without exposing a float sensor state."""
    number = numeric_value(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def russian_day_unit(days: int) -> str:
    """Return the grammatically correct Russian form for a day count."""
    last_two = abs(days) % 100
    if 11 <= last_two <= 14:
        return "дней"
    last = abs(days) % 10
    if last == 1:
        return "день"
    if 2 <= last <= 4:
        return "дня"
    return "дней"


def _list_from_payload(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("meters", "data", "items", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def normalize_meters(payload: Any) -> list[dict[str, Any]]:
    """Normalize known meter/scale fields while retaining raw fields for attributes."""
    normalized: list[dict[str, Any]] = []
    for meter_index, item in enumerate(_list_from_payload(payload)):
        if not isinstance(item, dict):
            continue
        identity = item.get("id") if isinstance(item.get("id"), dict) else {}
        raw_id = item.get("id") if not isinstance(item.get("id"), dict) else None
        registration = first_present(
            item.get("registration"),
            identity.get("registration"),
            item.get("meterRegistration"),
        )
        serial = first_present(item.get("serial"), item.get("number"), item.get("meterNumber"))
        meter_id = first_present(
            item.get("meterId"),
            item.get("meter_id"),
            identity.get("meterId"),
            identity.get("id"),
            raw_id,
            registration,
            serial,
            f"meter-{meter_index + 1}",
        )
        subservice = item.get("subservice") if isinstance(item.get("subservice"), dict) else {}
        meter_name = first_present(
            item.get("name"),
            item.get("title"),
            subservice.get("name"),
            serial,
            f"Счётчик {meter_index + 1}",
        )
        raw_scales = first_present(
            item.get("indications"),
            item.get("scales"),
            item.get("meterIndications"),
            [],
        )
        if isinstance(raw_scales, dict):
            raw_scales = [raw_scales]
        scales: list[dict[str, Any]] = []
        if isinstance(raw_scales, list):
            for scale_index, scale in enumerate(raw_scales):
                if not isinstance(scale, dict):
                    continue
                scale_id = first_present(
                    scale.get("scaleId"),
                    scale.get("meterScaleId"),
                    scale.get("id"),
                    f"scale-{scale_index + 1}",
                )
                scale_name = first_present(
                    scale.get("scaleName"),
                    scale.get("name"),
                    scale.get("title"),
                    f"Тариф {scale_index + 1}",
                )
                reading_raw = first_present(
                    scale.get("previousReading"),
                    scale.get("lastReading"),
                    scale.get("lastValue"),
                    scale.get("value"),
                )
                scales.append(
                    {
                        "scale_id": str(scale_id),
                        "name": str(scale_name),
                        "previous_reading": numeric_value(reading_raw),
                        "unit": first_present(
                            scale.get("unit"),
                            scale.get("unitName"),
                            item.get("unit"),
                        ),
                        "date": first_present(
                            scale.get("previousReadingDate"),
                            scale.get("lastReadingDate"),
                            scale.get("date"),
                        ),
                        "raw": scale,
                    }
                )
        normalized.append(
            {
                "meter_id": str(meter_id),
                "registration": str(registration) if registration is not None else None,
                "serial": str(serial) if serial is not None else None,
                "name": str(meter_name),
                "service": first_present(subservice.get("name"), item.get("service")),
                "scales": scales,
                "raw": item,
            }
        )
    return normalized


def previous_reading_for_scale(
    snapshots: list[dict[str, Any]],
    account_id: str,
    meter_id: str,
    scale_id: str,
) -> float | None:
    """Find the latest known reading for one account, meter, and scale."""
    for snapshot in snapshots:
        if str(snapshot.get("account_id")) != account_id:
            continue
        for meter in snapshot.get("meters", []):
            if str(meter.get("meter_id")) != meter_id:
                continue
            for scale in meter.get("scales", []):
                if str(scale.get("scale_id")) == scale_id:
                    return numeric_value(scale.get("previous_reading"))
    return None


def account_name(account: dict[str, Any]) -> str:
    """Choose a non-secret, human-readable account label."""
    for key in ("name", "title", "address", "shortAddress", "location"):
        value = account.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for nested_key in ("shortLocation", "location", "address", "name", "title"):
                nested = value.get(nested_key)
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
    number = account.get("number") or account.get("accountNumber")
    if number:
        return f"Лицевой счёт {number}"
    account_id = account.get("id") or account.get("accountId") or ""
    return f"Лицевой счёт {account_id}".strip()
