"""Coordinator for read-only DomRes account data."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    DomResApi,
    DomResApiError,
    DomResAuthenticationError,
    DomResCannotConnect,
    DomResVerificationRequired,
)
from .const import DOMAIN, NAME, UPDATE_INTERVAL
from .data import account_name, normalize_meters
from .events import DomResEventTracker

_LOGGER = logging.getLogger(__name__)


class DomResCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch DomRes read-only account data on a low-frequency schedule."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: DomResApi,
        entry_id: str,
        deadline_days: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=NAME,
            update_interval=UPDATE_INTERVAL,
        )
        self.api = api
        self.entry_id = entry_id
        self._deadline_days = deadline_days
        self._event_store = Store(hass, 1, f"{DOMAIN}_{entry_id}_event_state")
        self.event_tracker = DomResEventTracker(deadline_days)
        self.reading_values: dict[str, float] = {}

    async def async_load_event_state(self) -> None:
        """Restore unpaid-event period markers before the first refresh."""
        try:
            stored_state = await self._event_store.async_load()
        except OSError as err:
            _LOGGER.warning("Could not restore DomRes event state: %s", err)
            stored_state = None
        self.event_tracker = DomResEventTracker(self._deadline_days, stored_state)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            accounts = await self.api.async_get_accounts()
            snapshots: list[dict[str, Any]] = []
            for account in accounts:
                try:
                    raw = await self.api.async_get_account_snapshot(account)
                except (
                    DomResAuthenticationError,
                    DomResVerificationRequired,
                    DomResCannotConnect,
                ):
                    raise
                except DomResApiError as err:
                    _LOGGER.debug("Skipping one DomRes account after API error: %s", err)
                    continue
                account_id = raw.get("account_id")
                if not account_id:
                    continue
                snapshots.append(
                    {
                        "account": account,
                        "account_id": str(account_id),
                        "name": account_name(account),
                        "balance": raw.get("balance"),
                        "availability": raw.get("availability"),
                        "indication_status": raw.get("indication_status"),
                        "meters": normalize_meters(raw.get("meters")),
                        "errors": raw.get("errors", {}),
                    }
                )
        except DomResApiError as err:
            raise UpdateFailed(f"Не удалось получить данные ДомРесурса: {err}") from err

        if accounts and not snapshots:
            raise UpdateFailed("Не удалось получить данные ни по одному лицевому счёту")
        previous_event_state = self.event_tracker.stored_state
        events = self.event_tracker.update(snapshots)
        current_event_state = self.event_tracker.stored_state
        if current_event_state != previous_event_state:
            try:
                await self._event_store.async_save(current_event_state)
            except OSError as err:
                _LOGGER.warning("Could not persist DomRes event state: %s", err)
        for event_type, payload in events:
            self.hass.bus.async_fire(
                event_type,
                {"entry_id": self.entry_id, **payload},
            )
        return {"accounts": snapshots}
