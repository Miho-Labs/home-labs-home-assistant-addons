"""Config flow: set up by the Home Labs Backup add-on, or by hand as a fallback.

The add-on posts {api_url, api_token} to Supervisor discovery; HA hands it
to `async_step_hassio`. Installing the add-on and pasting the token there
is the owner's consent, so the entry is created without another click. A
new token in the add-on's options arrives the same way and updates the entry.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from . import create_api
from .api import ApiAuthError, ApiError
from .const import CONF_API_TOKEN, CONF_API_URL, DEFAULT_API_URL, DOMAIN, TITLE

# HA core moved from voluptuous to its successor, probatio; take whichever is there.
try:
    import probatio as vol
except ImportError:  # Home Assistant before the switch
    import voluptuous as vol

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_URL, default=DEFAULT_API_URL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
        vol.Required(CONF_API_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


class HomeLabsBackupConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        config = discovery_info.config or {}
        api_url = str(config.get(CONF_API_URL) or DEFAULT_API_URL).rstrip("/")
        token = str(config.get(CONF_API_TOKEN) or "").strip()
        if not token:
            return self.async_abort(reason="missing_token")
        data = {CONF_API_URL: api_url, CONF_API_TOKEN: token}
        await self.async_set_unique_id(DOMAIN)
        # Existing entry: take the new token/address and reload.
        self._abort_if_unique_id_configured(updates=data)
        return self.async_create_entry(title=TITLE, data=data)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_API_URL: user_input[CONF_API_URL].strip().rstrip("/"),
                CONF_API_TOKEN: user_input[CONF_API_TOKEN].strip(),
            }
            try:
                await create_api(self.hass, data[CONF_API_URL], data[CONF_API_TOKEN]).status()
            except ApiAuthError:
                errors["base"] = "invalid_token"
            except ApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title=TITLE, data=data)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )
