"""Constants for the DomRes integration."""

from datetime import timedelta

DOMAIN = "domres"
CONF_CUSTOMER = "customer"
NAME = "ДомРесурс"
API_BASE_URL = "https://domres.ru/api"

# Some DomRes API tenants may require a Customer header. Its value is account- or
# provider-specific and is therefore supplied as an optional config-flow field.
CUSTOMER_HEADER_NAME = "Customer"
USER_AGENT = "HomeAssistant-DomRes"

UPDATE_INTERVAL = timedelta(hours=6)
PLATFORMS = ("sensor", "binary_sensor")
