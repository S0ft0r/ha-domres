"""Shared Home Assistant entity identifiers and device metadata."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, NAME


def account_device_info(entry: ConfigEntry, snapshot: dict[str, Any]) -> DeviceInfo:
    """Return a device description shared by all entities for one account."""
    account_id = snapshot["account_id"]
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{account_id}")},
        name=f"{NAME} — {snapshot['name']}",
        manufacturer=NAME,
        configuration_url="https://domres.ru",
    )


def meter_scale_key(meter: dict[str, Any], scale: dict[str, Any], index: int) -> str:
    """Build the stable per-scale suffix used by the reading entities."""
    safe_meter = meter.get("registration") or meter.get("serial") or meter.get("meter_id") or "meter"
    safe_scale = scale.get("scale_id") or index
    return f"{safe_meter}_{safe_scale}"


def reading_input_key(
    account_id: str,
    meter: dict[str, Any],
    scale: dict[str, Any],
    index: int,
) -> str:
    """Return the coordinator-local key shared by one input and its button."""
    return f"{account_id}_{meter_scale_key(meter, scale, index)}"
