"""Async read-only DomRes API client.

The only POST in this module is the authentication request. Reading submission
is intentionally not implemented.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import API_BASE_URL, CUSTOMER_HEADER_NAME, USER_AGENT
from .data import login_type


class DomResApiError(Exception):
    """Base class for API errors safe to show in Home Assistant logs."""


class DomResCannotConnect(DomResApiError):
    """The DomRes service could not be reached."""


class DomResAuthenticationError(DomResApiError):
    """Authentication failed."""


class DomResVerificationRequired(DomResApiError):
    """The service requires an additional verification step."""


class DomResNoAccounts(DomResApiError):
    """The authenticated user has no accessible accounts."""


def _decode_response(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def _account_id(account: dict[str, Any]) -> str | None:
    for key in ("id", "accountId", "account_id"):
        value = account.get(key)
        if value is not None:
            return str(value)
    return None


class DomResApi:
    """Small async client exposing only confirmed read-only account endpoints."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        customer_header: str | None = None,
    ) -> None:
        self._session = session
        self._username = username.strip()
        self._password = password
        self._customer_header = customer_header.strip() if customer_header else None
        self._token: str | None = None

    @property
    def authenticated(self) -> bool:
        """Whether a bearer token is currently cached."""
        return self._token is not None

    def _base_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        if self._customer_header:
            headers[CUSTOMER_HEADER_NAME] = self._customer_header
        return headers

    def _headers(self) -> dict[str, str]:
        headers = self._base_headers()
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def async_login(self) -> None:
        """Authenticate and cache the bearer token."""
        payload = {
            "login": self._username,
            "password": self._password,
            "type": login_type(self._username),
        }
        headers = self._base_headers()
        headers.update({"withTotp": "true", "Captcha": "none"})
        try:
            async with self._session.post(
                f"{API_BASE_URL}/v8/users/auth",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                body = _decode_response(await response.text())
                status = response.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise DomResCannotConnect("Не удалось подключиться к ДомРесурсу") from err

        if status in (424, 449):
            raise DomResVerificationRequired(
                "Сервис запросил дополнительную проверку входа"
            )
        if status in (401, 403):
            raise DomResAuthenticationError("Проверьте логин и пароль")
        if status < 200 or status >= 300:
            raise DomResApiError(f"Ошибка авторизации ДомРесурса (HTTP {status})")
        if not isinstance(body, dict) or not body.get("auth"):
            raise DomResAuthenticationError("В ответе авторизации нет токена")
        self._token = str(body["auth"])

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        payload: Any = ...,
        retry_auth: bool = True,
    ) -> Any:
        if not self._token:
            await self.async_login()

        url = f"{API_BASE_URL}/{path.lstrip('/')}"
        kwargs: dict[str, Any] = {
            "headers": self._headers(),
            "timeout": aiohttp.ClientTimeout(total=30),
        }
        if payload is not ...:
            kwargs["json"] = payload

        try:
            async with self._session.request(method, url, **kwargs) as response:
                text = await response.text()
                status = response.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise DomResCannotConnect("Не удалось подключиться к ДомРесурсу") from err

        if status == 401 and retry_auth:
            self._token = None
            await self.async_login()
            return await self._async_request(
                method,
                path,
                payload=payload,
                retry_auth=False,
            )
        if status < 200 or status >= 300:
            raise DomResApiError(f"Ошибка API ДомРесурса для {path} (HTTP {status})")
        return _decode_response(text)

    async def async_get_accounts(self) -> list[dict[str, Any]]:
        """Return the authenticated user's account list."""
        result = await self._async_request("GET", "/v8/accounts/light")
        if isinstance(result, dict):
            for key in ("accounts", "data", "items"):
                if key in result and result[key] is not None:
                    result = result[key]
                    break
        if not isinstance(result, list):
            raise DomResApiError("Не удалось прочитать список лицевых счетов")
        return [item for item in result if isinstance(item, dict)]

    async def async_get_account_snapshot(self, account: dict[str, Any]) -> dict[str, Any]:
        """Fetch the known read-only balance, indication status, availability, and meter data."""
        account_id = _account_id(account)
        if account_id is None:
            raise DomResApiError("В ответе списка лицевых счетов нет идентификатора")
        encoded_id = quote(account_id, safe="")
        endpoints = {
            "balance": ("GET", f"/v1/accounts/{encoded_id}/balance/summary"),
            "availability": (
                "GET",
                f"/v1/accounts/{encoded_id}/indications/after-days",
            ),
            "indication_status": (
                "GET",
                f"/v1/accounts/{encoded_id}/indications/status",
            ),
            "meters": ("GET", f"/v6/accounts/{encoded_id}/meters/info"),
        }
        result: dict[str, Any] = {"account_id": account_id, "errors": {}}
        for key, (method, path) in endpoints.items():
            try:
                result[key] = await self._async_request(method, path)
            except (
                DomResAuthenticationError,
                DomResVerificationRequired,
                DomResCannotConnect,
            ):
                raise
            except DomResApiError as err:
                result[key] = None
                result["errors"][key] = str(err)
        if all(result[key] is None for key in endpoints):
            raise DomResApiError("Не удалось получить ни один набор данных лицевого счёта")
        return result
