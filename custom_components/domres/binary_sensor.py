"""Read-only DomRes binary sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import DomResCoordinator


class DomResIndicationAvailabilitySensor(
    CoordinatorEntity[DomResCoordinator], BinarySensorEntity
):
    """Whether the service currently allows meter indications."""

    _attr_has_entity_name = True
    _attr_name = "Можно передать показания"
    _attr_icon = "mdi:meter-electric-outline"

    def __init__(
        self,
        coordinator: DomResCoordinator,
        entry: ConfigEntry,
        snapshot: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._account_id = snapshot["account_id"]
        self._attr_unique_id = f"{entry.entry_id}_{self._account_id}_indication_available"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{self._account_id}")},
            name=f"{NAME} — {snapshot['name']}",
            manufacturer=NAME,
            configuration_url="https://domres.ru",
        )

    @property
    def _snapshot(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if not data:
            return None
        for snapshot in data.get("accounts", []):
            if snapshot.get("account_id") == self._account_id:
                return snapshot
        return None

    @property
    def is_on(self) -> bool | None:
        snapshot = self._snapshot
        availability = snapshot.get("availability") if snapshot else None
        if not isinstance(availability, dict):
            return None
        value = availability.get("indicationAvailability")
        return value if isinstance(value, bool) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snapshot = self._snapshot
        availability = snapshot.get("availability") if snapshot else None
        if not isinstance(availability, dict):
            return {}
        return {
            "days": availability.get("days"),
            "title": availability.get("title"),
            "meter_types": availability.get("meterTypes"),
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create indication availability entities."""
    coordinator: DomResCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        DomResIndicationAvailabilitySensor(coordinator, entry, snapshot)
        for snapshot in coordinator.data.get("accounts", [])
    )
