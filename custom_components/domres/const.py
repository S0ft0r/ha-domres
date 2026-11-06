"""Constants for the DomRes integration."""

from datetime import timedelta

DOMAIN = "domres"
CONF_CUSTOMER = "customer"
CONF_DEADLINE_DAYS = "deadline_days"
DEFAULT_DEADLINE_DAYS = 3
MIN_DEADLINE_DAYS = 1
MAX_DEADLINE_DAYS = 7
NAME = "ДомРесурс"
API_BASE_URL = "https://domres.ru/api"

# Some DomRes API tenants may require a Customer header. Its value is account- or
# provider-specific and is therefore supplied as an optional config-flow field.
CUSTOMER_HEADER_NAME = "Customer"
USER_AGENT = "HomeAssistant-DomRes"

UPDATE_INTERVAL = timedelta(hours=6)


def normalize_deadline_days(value: object) -> int:
    """Clamp stored legacy options into the currently supported 1–7 day range."""
    try:
        days = int(value)
    except (OverflowError, TypeError, ValueError):
        return DEFAULT_DEADLINE_DAYS
    return min(MAX_DEADLINE_DAYS, max(MIN_DEADLINE_DAYS, days))
