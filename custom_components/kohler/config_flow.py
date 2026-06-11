"""Config flow for Kohler Konnect.

Two-step setup:
  1. ``user``   — account email/password (+ pre-filled APIM key).
  2. ``signin`` — the integration shows a Kohler sign-in URL; the user signs
                  in, copies the ``msauth://...?code=...`` redirect URL, and
                  pastes it back. The integration exchanges the code for a B2C
                  refresh token server-side and validates everything.

Reauth (when the stored refresh token is revoked) reuses the ``signin`` step.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from kohler_anthem import KohlerAnthemClient, KohlerConfig
from kohler_anthem.exceptions import AuthenticationError, KohlerAnthemError

from . import decode_tenant_id
from .const import (
    CONF_API_RESOURCE,
    CONF_APIM_KEY,
    CONF_B2C_REFRESH_TOKEN,
    CONF_CLIENT_ID,
    CONF_TEMPERATURE_UNIT,
    CONF_TENANT_ID,
    DEFAULT_API_RESOURCE,
    DEFAULT_APIM_KEY,
    DEFAULT_CLIENT_ID,
    DOMAIN,
)
from .oauth import OAuthError, PendingSignIn, build_sign_in, exchange_code, parse_redirect

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        # Pre-filled with the app-global key; overridable if Kohler rotates it.
        vol.Required(CONF_APIM_KEY, default=DEFAULT_APIM_KEY): str,
    }
)

STEP_SIGNIN_SCHEMA = vol.Schema({vol.Required("redirect_url"): str})


class KohlerKonnectConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Kohler Konnect."""

    VERSION = 1

    def __init__(self) -> None:
        self._creds: dict[str, Any] = {}
        self._pending: PendingSignIn | None = None
        self._reauth_entry: ConfigEntry | None = None

    # ------------------------------------------------------------------ #
    # Step 1: credentials
    # ------------------------------------------------------------------ #
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._creds = {
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_APIM_KEY: user_input[CONF_APIM_KEY],
                CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
                CONF_API_RESOURCE: DEFAULT_API_RESOURCE,
            }
            return await self.async_step_signin()

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA)

    # ------------------------------------------------------------------ #
    # Step 2: browser sign-in + paste-back
    # ------------------------------------------------------------------ #
    async def async_step_signin(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        # First entry into this step: mint a fresh authorize URL to show.
        if self._pending is None:
            self._pending = build_sign_in()

        if user_input is not None:
            try:
                code = parse_redirect(user_input["redirect_url"], self._pending.state)
                refresh_token = await exchange_code(
                    aiohttp_session(self), code, self._pending.code_verifier
                )
            except OAuthError as err:
                _LOGGER.error("B2C sign-in failed: %s", err)
                errors["base"] = "signin_failed"
            else:
                return await self._finish(refresh_token, errors)

        return self.async_show_form(
            step_id="signin",
            data_schema=STEP_SIGNIN_SCHEMA,
            errors=errors,
            description_placeholders={"signin_url": self._pending.authorize_url},
        )

    # ------------------------------------------------------------------ #
    # Reauth: just redo the sign-in step against the existing entry
    # ------------------------------------------------------------------ #
    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        assert self._reauth_entry is not None
        self._creds = dict(self._reauth_entry.data)
        return await self.async_step_signin()

    # ------------------------------------------------------------------ #
    # Validate the freshly seeded token + finalize entry
    # ------------------------------------------------------------------ #
    async def _finish(
        self, refresh_token: str, errors: dict[str, str]
    ) -> ConfigFlowResult:
        config = KohlerConfig(
            username=self._creds[CONF_USERNAME],
            password=self._creds[CONF_PASSWORD],
            client_id=self._creds.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID),
            apim_subscription_key=self._creds[CONF_APIM_KEY],
            api_resource=self._creds.get(CONF_API_RESOURCE, DEFAULT_API_RESOURCE),
            b2c_refresh_token=refresh_token,
        )

        client = KohlerAnthemClient(config)
        try:
            await client.connect()
            tenant_id = decode_tenant_id(
                client._auth.token.access_token if client._auth.token else None
            )
            if not tenant_id:
                errors["base"] = "cannot_connect"
                return self._reshow_signin(errors)

            customer = await client.get_customer(tenant_id)
            temperature_unit = getattr(customer, "temperature_unit", "Fahrenheit")

            # Capture the rotated refresh token (the connect above may rotate it).
            rotated = client.b2c_refresh_token or refresh_token
        except AuthenticationError as err:
            _LOGGER.error("Auth failed after sign-in: %s", err)
            errors["base"] = "invalid_auth"
            return self._reshow_signin(errors)
        except KohlerAnthemError as err:
            _LOGGER.error("Cannot connect after sign-in: %s", err)
            errors["base"] = "cannot_connect"
            return self._reshow_signin(errors)
        finally:
            await client.close()

        data = {
            **self._creds,
            CONF_B2C_REFRESH_TOKEN: rotated,
            CONF_TENANT_ID: tenant_id,
            CONF_TEMPERATURE_UNIT: temperature_unit,
        }

        # Reauth path: update the existing entry in place.
        if self._reauth_entry is not None:
            self.hass.config_entries.async_update_entry(
                self._reauth_entry, data={**self._reauth_entry.data, **data}
            )
            await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        await self.async_set_unique_id(self._creds[CONF_USERNAME].lower())
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Kohler Konnect ({self._creds[CONF_USERNAME]})",
            data=data,
        )

    def _reshow_signin(self, errors: dict[str, str]) -> ConfigFlowResult:
        """Re-render the sign-in step with a fresh URL after a failure."""
        self._pending = build_sign_in()
        return self.async_show_form(
            step_id="signin",
            data_schema=STEP_SIGNIN_SCHEMA,
            errors=errors,
            description_placeholders={"signin_url": self._pending.authorize_url},
        )


def aiohttp_session(flow: ConfigFlow):
    """Return HA's shared aiohttp session (lazy import to keep module light)."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    return async_get_clientsession(flow.hass)
