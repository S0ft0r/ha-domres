"""Config flow for DomRes."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    DomResApi,
    DomResApiError,
    DomResAuthenticationError,
    DomResNoAccounts,
    DomResVerificationRequired,
)
from .const import CONF_CUSTOMER, DOMAIN, NAME


def _unique_login(login: str) -> str:
    """Normalize a login for config-entry uniqueness without changing its API value."""
    value = login.strip()
    if "@" in value:
        return value.casefold()
    digits = re.sub(r"\D", "", value)
    return digits or value.casefold()


async def _async_validate_credentials(
    hass: HomeAssistant,
    login: str,
    password: str,
    customer: str | None,
) -> None:
    api = DomResApi(async_get_clientsession(hass), login, password, customer)
    await api.async_login()
    accounts = await api.async_get_accounts()
    if not accounts:
        raise DomResNoAccounts("Для учётной записи не найдено лицевых счетов")


class DomResConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow using one autodetected phone/email login field."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            login = str(user_input[CONF_USERNAME]).strip()
            password = str(user_input[CONF_PASSWORD])
            customer = str(user_input.get(CONF_CUSTOMER, "")).strip() or None
            if not login or not password:
                errors["base"] = "invalid_auth"
            else:
                try:
                    await _async_validate_credentials(
                        self.hass, login, password, customer
                    )
                except DomResAuthenticationError:
                    errors["base"] = "invalid_auth"
                except DomResVerificationRequired:
                    errors["base"] = "verification_required"
                except DomResNoAccounts:
                    errors["base"] = "no_accounts"
                except DomResApiError:
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(_unique_login(login))
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=NAME,
                        data={
                            CONF_USERNAME: login,
                            CONF_PASSWORD: password,
                            CONF_CUSTOMER: customer or "",
                        },
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_CUSTOMER, default=""): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )
