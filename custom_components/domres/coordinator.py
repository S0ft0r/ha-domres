"""Coordinator for read-only DomRes account data."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DomResApi, DomResApiError
from .const import NAME, UPDATE_INTERVAL
from .data import account_name, normalize_meters

_LOGGER = logging.getLogger(__name__)


class DomResCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch DomRes read-only account data on a low-frequency schedule."""

    def __init__(self, hass: HomeAssistant, api: DomResApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=NAME,
            update_interval=UPDATE_INTERVAL,
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            accounts = await self.api.async_get_accounts()
            snapshots: list[dict[str, Any]] = []
            for account in accounts:
                try:
                    raw = await self.api.async_get_account_snapshot(account)
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
                        "meters": normalize_meters(raw.get("meters")),
                        "errors": raw.get("errors", {}),
                    }
                )
        except DomResApiError as err:
            raise UpdateFailed(f"Не удалось получить данные ДомРесурса: {err}") from err

        if accounts and not snapshots:
            raise UpdateFailed("Не удалось получить данные ни по одному лицевому счёту")
        return {"accounts": snapshots}
