"""Read-only DomRes sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import DomResCoordinator
from .data import numeric_value


def _balance_value(snapshot: dict[str, Any], key: str) -> float | None:
    summary = snapshot.get("balance")
    if not isinstance(summary, dict):
        return None
    return numeric_value(summary.get(key))


def _days_value(snapshot: dict[str, Any]) -> float | None:
    availability = snapshot.get("availability")
    if not isinstance(availability, dict):
        return None
    return numeric_value(availability.get("days"))


def _reading_units(raw_unit: Any) -> tuple[str | None, SensorDeviceClass | None, SensorStateClass | None]:
    """Map only units with known HA semantics; leave unfamiliar units unclassified."""
    if not isinstance(raw_unit, str):
        return None, None, None
    unit = raw_unit.strip()
    normalized = unit.casefold().replace(" ", "")
    if normalized in {"kwh", "квт⋅ч", "квт*ч", "квт·ч"}:
        return UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING
    if normalized in {"m3", "м3", "м³", "м^3"}:
        return UnitOfVolume.CUBIC_METERS, SensorDeviceClass.WATER, SensorStateClass.TOTAL_INCREASING
    return unit or None, None, None


def _device_info(entry: ConfigEntry, snapshot: dict[str, Any]) -> DeviceInfo:
    account_id = snapshot["account_id"]
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{account_id}")},
        name=f"{NAME} — {snapshot['name']}",
        manufacturer=NAME,
        configuration_url="https://domres.ru",
    )


class _DomResSensor(CoordinatorEntity[DomResCoordinator], SensorEntity):
    """Base entity for account sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DomResCoordinator,
        entry: ConfigEntry,
        snapshot: dict[str, Any],
        key: str,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._account_id = snapshot["account_id"]
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{self._account_id}_{key}"
        self._attr_name = name
        self._attr_device_info = _device_info(entry, snapshot)

    @property
    def _snapshot(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if not data:
            return None
        for snapshot in data.get("accounts", []):
            if snapshot.get("account_id") == self._account_id:
                return snapshot
        return None


class DomResDebtSensor(_DomResSensor):
    """Account balance field shown by the service as debt/balance."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "RUB"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:cash-minus"

    def __init__(self, coordinator: DomResCoordinator, entry: ConfigEntry, snapshot: dict[str, Any]) -> None:
        super().__init__(coordinator, entry, snapshot, "debt", "Задолженность")

    @property
    def native_value(self) -> float | None:
        snapshot = self._snapshot
        return _balance_value(snapshot, "balance") if snapshot else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snapshot = self._snapshot
        if not snapshot:
            return {}
        return {"fine_rub": _balance_value(snapshot, "fine"), "account_id": self._account_id}


class DomResFineSensor(_DomResSensor):
    """Account fine/penalty field."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "RUB"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:cash-alert"

    def __init__(self, coordinator: DomResCoordinator, entry: ConfigEntry, snapshot: dict[str, Any]) -> None:
        super().__init__(coordinator, entry, snapshot, "fine", "Пени")

    @property
    def native_value(self) -> float | None:
        snapshot = self._snapshot
        return _balance_value(snapshot, "fine") if snapshot else None


class DomResDaysSensor(_DomResSensor):
    """Number of days remaining in the current indication period."""

    _attr_native_unit_of_measurement = "d"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: DomResCoordinator, entry: ConfigEntry, snapshot: dict[str, Any]) -> None:
        super().__init__(coordinator, entry, snapshot, "indication_days", "Дней до окончания периода")

    @property
    def native_value(self) -> float | None:
        snapshot = self._snapshot
        return _days_value(snapshot) if snapshot else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snapshot = self._snapshot
        availability = snapshot.get("availability") if snapshot else None
        if not isinstance(availability, dict):
            return {}
        return {
            "title": availability.get("title"),
            "indication_available": availability.get("indicationAvailability"),
            "meter_types": availability.get("meterTypes"),
        }


class DomResLastReadingSensor(_DomResSensor):
    """Most recent known reading for one meter scale."""

    def __init__(
        self,
        coordinator: DomResCoordinator,
        entry: ConfigEntry,
        snapshot: dict[str, Any],
        meter: dict[str, Any],
        scale: dict[str, Any],
        index: int,
    ) -> None:
        safe_meter = meter.get("registration") or meter.get("serial") or meter.get("meter_id") or "meter"
        safe_scale = scale.get("scale_id") or index
        key = f"reading_{safe_meter}_{safe_scale}"
        name = f"Последнее показание — {meter['name']} — {scale['name']}"
        super().__init__(coordinator, entry, snapshot, key, name)
        unit, device_class, state_class = _reading_units(scale.get("unit"))
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        if device_class == SensorDeviceClass.ENERGY:
            self._attr_icon = "mdi:flash"
        elif device_class == SensorDeviceClass.WATER:
            self._attr_icon = "mdi:water"
        else:
            self._attr_icon = "mdi:counter"
        self._meter_id = meter.get("meter_id")
        self._meter_serial = meter.get("serial")
        self._meter_registration = meter.get("registration")
        self._scale_id = scale.get("scale_id")
        self._scale_name = scale.get("name")
        self._reading_date = scale.get("date")
        self._raw_unit = scale.get("unit")

    @property
    def native_value(self) -> float | None:
        snapshot = self._snapshot
        if not snapshot:
            return None
        for meter in snapshot.get("meters", []):
            for scale in meter.get("scales", []):
                if (
                    meter.get("meter_id") == self._meter_id
                    and scale.get("scale_id") == self._scale_id
                ):
                    return scale.get("previous_reading")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "meter_serial": self._meter_serial,
            "meter_registration": self._meter_registration,
            "scale_id": self._scale_id,
            "scale_name": self._scale_name,
            "reading_date": self._reading_date,
            "api_unit": self._raw_unit,
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create account and meter sensors."""
    coordinator: DomResCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    for snapshot in coordinator.data.get("accounts", []):
        entities.extend(
            [
                DomResDebtSensor(coordinator, entry, snapshot),
                DomResFineSensor(coordinator, entry, snapshot),
                DomResDaysSensor(coordinator, entry, snapshot),
            ]
        )
        for meter in snapshot.get("meters", []):
            for index, scale in enumerate(meter.get("scales", []), start=1):
                if scale.get("previous_reading") is None:
                    continue
                entities.append(
                    DomResLastReadingSensor(
                        coordinator, entry, snapshot, meter, scale, index
                    )
                )
    async_add_entities(entities)
