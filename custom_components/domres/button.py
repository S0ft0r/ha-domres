"""Preparation buttons for meter readings; network submission is not wired yet."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DomResCoordinator
from .data import previous_reading_for_scale, reading_not_below_previous
from .entity import account_device_info, meter_scale_key, reading_input_key


class DomResSubmitReadingButton(CoordinatorEntity[DomResCoordinator], ButtonEntity):
    """Visible send-button scaffold that explicitly refuses to transmit data."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:send"

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
        self._meter_id = str(meter.get("meter_id"))
        self._scale_id = str(scale.get("scale_id"))
        self._initial_previous_reading = previous_reading_for_scale(
            [snapshot],
            self._account_id,
            self._meter_id,
            self._scale_id,
        )
        self._attr_unique_id = (
            f"{entry.entry_id}_{self._account_id}_reading_submit_"
            f"{meter_scale_key(meter, scale, index)}"
        )
        self._attr_name = f"Отправить — {meter['name']} — {scale['name']}"
        self._attr_device_info = account_device_info(entry, snapshot)

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

    async def async_press(self) -> None:
        """Validate monotonicity, then fail closed until a write contract is verified."""
        if self._input_key not in self.coordinator.reading_values:
            raise HomeAssistantError("Сначала введите показание в числовое поле.")
        value = self.coordinator.reading_values[self._input_key]
        if value < 0 or not reading_not_below_previous(value, self.previous_reading):
            raise HomeAssistantError(
                "Новое показание не может быть меньше предыдущего значения счётчика."
            )
        raise HomeAssistantError(
            "Показание не отправлено: передача пока отключена до проверки API-запроса."
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one guarded send-button stub per meter scale."""
    coordinator: DomResCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = []
    for snapshot in coordinator.data.get("accounts", []):
        for meter in snapshot.get("meters", []):
            for index, scale in enumerate(meter.get("scales", []), start=1):
                entities.append(
                    DomResSubmitReadingButton(
                        coordinator,
                        entry,
                        snapshot,
                        meter,
                        scale,
                        index,
                    )
                )
    async_add_entities(entities)
