"""Config flow for Kohler Konnect."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .api import KohlerKonnectAPI
from .const import DOMAIN

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("username"): str,
        vol.Required("password"): str,
    }
)


class KohlerKonnectConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Kohler Konnect."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            api = KohlerKonnectAPI(
                username=user_input["username"],
                password=user_input["password"],
            )
            try:
                await self.hass.async_add_executor_job(api.authenticate)
                devices = await self.hass.async_add_executor_job(api.get_devices)
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                if not devices:
                    errors["base"] = "no_devices"
                else:
                    await self.async_set_unique_id(user_input["username"])
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"Kohler Konnect ({user_input['username']})",
                        data=user_input,
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
