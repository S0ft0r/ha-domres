"""Config flow for DomRes."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, OptionsFlowWithReload
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    DomResApi,
    DomResApiError,
    DomResAuthenticationError,
    DomResNoAccounts,
    DomResVerificationRequired,
)
from .const import (
    CONF_CUSTOMER,
    CONF_DEADLINE_DAYS,
    DEFAULT_DEADLINE_DAYS,
    DOMAIN,
    MAX_DEADLINE_DAYS,
    MIN_DEADLINE_DAYS,
    NAME,
    normalize_deadline_days,
)


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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DomResOptionsFlow:
        """Expose the configurable notice threshold for indication deadlines."""
        return DomResOptionsFlow()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Let the user renew credentials without deleting the existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            login = str(
                user_input.get(CONF_USERNAME, entry.data[CONF_USERNAME])
            ).strip()
            password = str(
                user_input.get(CONF_PASSWORD) or entry.data[CONF_PASSWORD]
            )
            customer = str(user_input.get(CONF_CUSTOMER, "")).strip() or None
            if not login or not password:
                errors["base"] = "invalid_auth"
            else:
                try:
                    await _async_validate_credentials(
                        self.hass,
                        login,
                        password,
                        customer,
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
                    self._abort_if_unique_id_mismatch(reason="wrong_account")
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_USERNAME: login,
                            CONF_PASSWORD: password,
                            CONF_CUSTOMER: customer or "",
                        },
                    )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME,
                    default=entry.data[CONF_USERNAME],
                ): str,
                vol.Optional(CONF_PASSWORD, default=""): str,
                vol.Optional(
                    CONF_CUSTOMER,
                    default=entry.data.get(CONF_CUSTOMER, ""),
                ): str,
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )

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


class DomResOptionsFlow(OptionsFlowWithReload):
    """Configure how many days before the deadline the event is raised."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        default_days = normalize_deadline_days(
            self.config_entry.options.get(
                CONF_DEADLINE_DAYS,
                DEFAULT_DEADLINE_DAYS,
            )
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DEADLINE_DAYS,
                    default=default_days,
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_DEADLINE_DAYS, max=MAX_DEADLINE_DAYS),
                )
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema,
                {CONF_DEADLINE_DAYS: default_days},
            ),
        )
