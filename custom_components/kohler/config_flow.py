"""Config flow for Kohler Konnect."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from kohler_anthem import KohlerAnthemClient, KohlerConfig
from kohler_anthem.exceptions import AuthenticationError, KohlerAnthemError

from .const import (
    CONF_API_RESOURCE,
    CONF_APIM_KEY,
    CONF_B2C_REFRESH_TOKEN,
    CONF_CLIENT_ID,
    CONF_TENANT_ID,
    DEFAULT_API_RESOURCE,
    DEFAULT_CLIENT_ID,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_APIM_KEY): str,
        vol.Required(CONF_B2C_REFRESH_TOKEN): str,
        # Advanced — defaulted to the app-global values. Only change these if
        # Kohler rotates the mobile app's client_id / API resource GUID.
        vol.Optional(CONF_CLIENT_ID, default=DEFAULT_CLIENT_ID): str,
        vol.Optional(CONF_API_RESOURCE, default=DEFAULT_API_RESOURCE): str,
    }
)

STEP_REAUTH_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_B2C_REFRESH_TOKEN): str,
    }
)


def _decode_tenant_id(access_token: str | None) -> str | None:
    """Decode the ``oid`` claim from a B2C access token."""
    # Local import keeps the module import-light and avoids a circular import.
    from . import decode_tenant_id

    return decode_tenant_id(access_token)


class KohlerKonnectConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Kohler Konnect."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            config = KohlerConfig(
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
                client_id=user_input[CONF_CLIENT_ID],
                apim_subscription_key=user_input[CONF_APIM_KEY],
                api_resource=user_input[CONF_API_RESOURCE],
                b2c_refresh_token=user_input[CONF_B2C_REFRESH_TOKEN],
            )

            tenant_id, error = await self._async_validate(config)
            if error:
                errors["base"] = error
            elif not tenant_id:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Kohler Konnect ({user_input[CONF_USERNAME]})",
                    data={**user_input, CONF_TENANT_ID: tenant_id},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Triggered when the stored B2C refresh token is revoked/expired."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-prompt for just a freshly seeded B2C refresh token."""
        errors: dict[str, str] = {}
        entry = self._reauth_entry
        assert entry is not None

        if user_input is not None:
            new_token = user_input[CONF_B2C_REFRESH_TOKEN]
            config = KohlerConfig(
                username=entry.data[CONF_USERNAME],
                password=entry.data[CONF_PASSWORD],
                client_id=entry.data.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID),
                apim_subscription_key=entry.data[CONF_APIM_KEY],
                api_resource=entry.data.get(CONF_API_RESOURCE, DEFAULT_API_RESOURCE),
                b2c_refresh_token=new_token,
            )
            tenant_id, error = await self._async_validate(config)
            if error:
                errors["base"] = error
            else:
                rotated = new_token
                # Capture the rotated token if validation refreshed it.
                client = KohlerAnthemClient(config)
                try:
                    await client.connect()
                    await client._b2c_auth.refresh(client._session)
                    rotated = client.b2c_refresh_token or new_token
                except KohlerAnthemError:
                    pass
                finally:
                    await client.close()

                self.hass.config_entries.async_update_entry(
                    entry,
                    data={
                        **entry.data,
                        CONF_B2C_REFRESH_TOKEN: rotated,
                        **({CONF_TENANT_ID: tenant_id} if tenant_id else {}),
                    },
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            errors=errors,
        )

    async def _async_validate(
        self, config: KohlerConfig
    ) -> tuple[str | None, str | None]:
        """Validate credentials + B2C refresh token.

        Returns ``(tenant_id, error_key)``. ``error_key`` is ``None`` on
        success. ``tenant_id`` is the decoded customer id used for API calls.
        """
        client = KohlerAnthemClient(config)
        try:
            await client.connect()
            tenant_id = _decode_tenant_id(
                client._auth.token.access_token if client._auth.token else None
            )
            if not tenant_id:
                return None, "cannot_connect"

            # Confirm the account has devices and the ROPC read path works.
            await client.get_customer(tenant_id)

            # Validate the B2C refresh token by forcing a silent refresh; a bad
            # token raises AuthenticationError here rather than at first write.
            try:
                await client._b2c_auth.refresh(client._session)
            except AuthenticationError as err:
                _LOGGER.error("B2C refresh token rejected: %s", err)
                return None, "invalid_b2c_refresh_token"

            return tenant_id, None
        except AuthenticationError as err:
            _LOGGER.error("Authentication failed: %s", err)
            return None, "invalid_auth"
        except KohlerAnthemError as err:
            _LOGGER.error("Cannot connect to Kohler API: %s", err)
            return None, "cannot_connect"
        finally:
            await client.close()
