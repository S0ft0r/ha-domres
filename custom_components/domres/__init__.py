"""Home Assistant integration for DomRes."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DomResApi
from .const import (
    CONF_CUSTOMER,
    CONF_DEADLINE_DAYS,
    DEFAULT_DEADLINE_DAYS,
    DOMAIN,
)
from .coordinator import DomResCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.NUMBER, Platform.BUTTON]


def _remove_retired_binary_sensors(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the old indication-availability entity from previous releases."""
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.domain == Platform.BINARY_SENSOR:
            registry.async_remove(entity.entity_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DomRes from a config entry."""
    api = DomResApi(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_CUSTOMER) or None,
    )
    coordinator = DomResCoordinator(
        hass,
        api,
        entry.entry_id,
        int(entry.options.get(CONF_DEADLINE_DAYS, DEFAULT_DEADLINE_DAYS)),
    )
    await coordinator.async_load_event_state()
    # Let DataUpdateCoordinator translate UpdateFailed into ConfigEntryNotReady.
    # Do not catch Exception here: programming errors must remain visible in logs.
    await coordinator.async_config_entry_first_refresh()

    _remove_retired_binary_sensors(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the DomRes config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok
