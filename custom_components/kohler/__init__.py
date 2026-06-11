"""Kohler Konnect Home Assistant integration.

Backed by the ``kohler-anthem`` library (PyPI). The library implements the
two-token auth model Kohler's backend now requires:

* a ROPC-policy token for reads, and
* a B2C_1A_signin-policy token (seeded once via a refresh token) for
  ``/commands/*`` writes, which the backend rejects ROPC tokens on with 403.

See https://github.com/yon/kohler-anthem for the library and the reverse
engineering write-up.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from kohler_anthem import KohlerAnthemClient, KohlerConfig
from kohler_anthem.exceptions import AuthenticationError, KohlerAnthemError
from kohler_anthem.models import Device, DeviceState

from .const import (
    CONF_API_RESOURCE,
    CONF_APIM_KEY,
    CONF_B2C_REFRESH_TOKEN,
    CONF_CLIENT_ID,
    CONF_TENANT_ID,
    DEFAULT_API_RESOURCE,
    DEFAULT_CLIENT_ID,
    DOMAIN,
    SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.WATER_HEATER]


def decode_tenant_id(access_token: str | None) -> str | None:
    """Extract the tenant/customer id (the ``oid`` claim) from a B2C JWT.

    Kohler's API keys every device/customer call on the user's object id,
    which is carried as the ``oid`` (falling back to ``sub``) claim in the
    access token. Returns ``None`` if the token can't be decoded.
    """
    if not access_token:
        return None
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, binascii.Error, json.JSONDecodeError) as err:
        _LOGGER.warning("Could not decode access token for tenant id: %s", err)
        return None
    return claims.get("oid") or claims.get("sub")


def build_config(entry: ConfigEntry) -> KohlerConfig:
    """Build a KohlerConfig from a config entry's stored data."""
    return KohlerConfig(
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        client_id=entry.data.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID),
        apim_subscription_key=entry.data[CONF_APIM_KEY],
        api_resource=entry.data.get(CONF_API_RESOURCE, DEFAULT_API_RESOURCE),
        b2c_refresh_token=entry.data.get(CONF_B2C_REFRESH_TOKEN),
    )


class KohlerKonnectCoordinator(DataUpdateCoordinator[dict[str, DeviceState]]):
    """Polls device state for every Anthem device on the account."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: KohlerAnthemClient,
        tenant_id: str,
        devices: list[Device],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL),
        )
        self._entry = entry
        self.client = client
        self.tenant_id = tenant_id
        self.devices = devices

    def _persist_rotated_token(self) -> None:
        """Persist the B2C refresh token if the library rotated it.

        B2C issues a new refresh token on every silent refresh; if we drop it
        the user has to re-seed after the old one expires. Write it back to the
        config entry whenever it changes.
        """
        rotated = self.client.b2c_refresh_token
        if rotated and rotated != self._entry.data.get(CONF_B2C_REFRESH_TOKEN):
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={**self._entry.data, CONF_B2C_REFRESH_TOKEN: rotated},
            )

    async def _async_update_data(self) -> dict[str, DeviceState]:
        try:
            states: dict[str, DeviceState] = {}
            for device in self.devices:
                states[device.device_id] = await self.client.get_device_state(
                    device.device_id
                )
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(
                f"Authentication failed during update: {err}"
            ) from err
        except KohlerAnthemError as err:
            raise UpdateFailed(f"Error communicating with Kohler API: {err}") from err

        # A successful read may have rotated the B2C refresh token.
        self._persist_rotated_token()
        return states


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Kohler Konnect from a config entry."""
    if not entry.data.get(CONF_B2C_REFRESH_TOKEN):
        # Pre-0.3.0 entries (username/password only) can't write to the device
        # under Kohler's current backend. Force reauth to seed a refresh token.
        raise ConfigEntryAuthFailed(
            "Kohler now requires a B2C refresh token for shower control. "
            "Seed one with `python -m kohler_anthem.b2c_signin` and paste it "
            "into the reauth prompt."
        )

    config = build_config(entry)
    client = KohlerAnthemClient(config)

    try:
        await client.connect()
    except AuthenticationError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except KohlerAnthemError as err:
        await client.close()
        raise ConfigEntryNotReady(
            f"Unable to connect to Kohler API: {err}"
        ) from err

    # tenant_id is needed for every customer/device call. Prefer the value
    # captured at config time; fall back to decoding it from the fresh token.
    tenant_id = entry.data.get(CONF_TENANT_ID) or decode_tenant_id(
        client._auth.token.access_token if client._auth.token else None
    )
    if not tenant_id:
        await client.close()
        raise ConfigEntryAuthFailed("Could not determine Kohler tenant id from token")

    try:
        customer = await client.get_customer(tenant_id)
    except AuthenticationError as err:
        await client.close()
        raise ConfigEntryAuthFailed(str(err)) from err
    except KohlerAnthemError as err:
        await client.close()
        raise ConfigEntryNotReady(
            f"Unable to load Kohler devices: {err}"
        ) from err

    devices = [d for d in customer.get_all_devices() if d.sku == "GCS"]
    if not devices:
        _LOGGER.warning("No Anthem (GCS) devices found for this account")

    coordinator = KohlerKonnectCoordinator(hass, entry, client, tenant_id, devices)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options/data change (e.g. after reauth)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: KohlerKonnectCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.close()
    return unload_ok
