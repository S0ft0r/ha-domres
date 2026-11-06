"""Local numeric fields for preparing meter readings (no API submission)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DomResCoordinator
from .data import previous_reading_for_scale, reading_not_below_previous
from .entity import account_device_info, meter_scale_key, reading_input_key


class DomResReadingNumber(CoordinatorEntity[DomResCoordinator], NumberEntity):
    """Editable local value for one meter scale; it does not submit to DomRes."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:counter"
    _attr_mode = NumberMode.BOX
    _attr_native_max_value = 1_000_000_000.0
    _attr_native_step = 0.001
    _attr_suggested_display_precision = 3

    def __init__(
        self,
        coordinator: DomResCoordinator,
        entry: ConfigEntry,
        snapshot: dict[str, Any],
        meter: dict[str, Any],
        scale: dict[str, Any],
        index: int,
    ) -> None:
        super().__init__(coordinator)
        self._account_id = str(snapshot["account_id"])
        self._input_key = reading_input_key(self._account_id, meter, scale, index)
        self._attr_unique_id = (
            f"{entry.entry_id}_{self._account_id}_reading_input_"
            f"{meter_scale_key(meter, scale, index)}"
        )
        self._attr_name = "Показание"
        self._attr_device_info = account_device_info(entry, snapshot)
        unit = scale.get("unit")
        self._attr_native_unit_of_measurement = unit.strip() if isinstance(unit, str) and unit.strip() else None
        self._meter_id = str(meter.get("meter_id"))
        self._scale_id = str(scale.get("scale_id"))
        self._initial_previous_reading = previous_reading_for_scale(
            [snapshot],
            self._account_id,
            self._meter_id,
            self._scale_id,
        )
        self._meter_serial = meter.get("serial")
        self._meter_registration = meter.get("registration")

    @property
    def previous_reading(self) -> float | None:
        """Return the latest reported value for this exact account/meter/scale."""
        data = self.coordinator.data or {}
        current = previous_reading_for_scale(
            data.get("accounts", []),
            self._account_id,
            self._meter_id,
            self._scale_id,
        )
        return current if current is not None else self._initial_previous_reading

    @property
    def native_min_value(self) -> float:
        """Prevent entering a value below the last reported reading."""
        previous = self.previous_reading
        return max(0.0, previous) if previous is not None else 0.0

    @property
    def native_max_value(self) -> float:
        """Keep the upper bound above the previous value for unusual meters."""
        return max(float(self._attr_native_max_value), self.native_min_value)

    @property
    def native_value(self) -> float:
        """Return the entered value, prefilling the field with the last reading."""
        return self.coordinator.reading_values.get(self._input_key, self.native_min_value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "meter_serial": self._meter_serial,
            "meter_registration": self._meter_registration,
            "scale_id": self._scale_id,
            "previous_reading": self.previous_reading,
            "value_set": self._input_key in self.coordinator.reading_values,
            "submission_enabled": False,
        }

    def _handle_coordinator_update(self) -> None:
        """Clamp a draft upward if the latest reported reading has increased."""
        current_value = self.coordinator.reading_values.get(self._input_key)
        if current_value is not None and not reading_not_below_previous(
            current_value,
            self.previous_reading,
        ):
            self.coordinator.reading_values[self._input_key] = self.native_min_value
        super()._handle_coordinator_update()

    async def async_set_native_value(self, value: float) -> None:
        """Store a draft only when it is at least the previous reported value."""
        numeric_value = float(value)
        if not reading_not_below_previous(numeric_value, self.previous_reading):
            raise ValueError("Новое показание не может быть меньше предыдущего")
        if not self.native_min_value <= numeric_value <= self.native_max_value:
            raise ValueError("Meter reading is outside the allowed numeric range")
        self.coordinator.reading_values[self._input_key] = numeric_value
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one local input number per meter scale."""
    coordinator: DomResCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = []
    for snapshot in coordinator.data.get("accounts", []):
        for meter in snapshot.get("meters", []):
            for index, scale in enumerate(meter.get("scales", []), start=1):
                entities.append(
                    DomResReadingNumber(
                        coordinator,
                        entry,
                        snapshot,
                        meter,
                        scale,
                        index,
                    )
                )
    async_add_entities(entities)
