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
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from kohler_anthem import KohlerAnthemClient, KohlerConfig
from kohler_anthem.exceptions import AuthenticationError, KohlerAnthemError
from kohler_anthem.models import Device, DeviceState, Outlet, PresetResponse

from .const import (
    CONF_API_RESOURCE,
    CONF_APIM_KEY,
    CONF_B2C_REFRESH_TOKEN,
    CONF_CLIENT_ID,
    CONF_TEMPERATURE_UNIT,
    CONF_TENANT_ID,
    DEFAULT_API_RESOURCE,
    DEFAULT_CLIENT_ID,
    DOMAIN,
    PRESET_REFRESH_CYCLES,
    SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
]

# Kohler's backend returns this when the physical device is powered off or has
# lost its network/cloud link. It is an expected, transient condition — not an
# error in the integration — so we surface it gently rather than as a traceback.
KOHLER_OFFLINE_STATUS = 900


def is_offline_error(err: KohlerAnthemError) -> bool:
    """True if an API error means the device is offline (vs a real failure)."""
    raw = getattr(err, "raw_response", None)
    if isinstance(raw, dict) and raw.get("statusCode") == KOHLER_OFFLINE_STATUS:
        return True
    # Fallback: some responses only carry the message text.
    text = str(raw) if raw is not None else str(err)
    return "product is offline" in text.lower()


async def run_device_command(coro: "Any", action: str) -> None:
    """Await a Kohler command coroutine, translating failures for the UI.

    Raises HomeAssistantError with a clean message so HA shows a tidy notice
    instead of a traceback. Device-offline is logged at INFO (expected); other
    failures at ERROR.
    """
    from homeassistant.exceptions import HomeAssistantError

    try:
        await coro
    except KohlerAnthemError as err:
        if is_offline_error(err):
            _LOGGER.info("Cannot %s: the shower is offline", action)
            raise HomeAssistantError(
                "The Kohler shower is offline. Check that it's powered on and "
                "connected to Wi-Fi, then try again."
            ) from err
        _LOGGER.error("Failed to %s: %s", action, err)
        raise HomeAssistantError(f"Kohler command failed: {err}") from err


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


@dataclass
class DeviceRuntime:
    """Per-device settings shared across entity platforms.

    The Kohler API has no "set flow/outlet without running water" command, so
    the number/select entities store the user's choice here and the
    water_heater applies it when starting (or live-updates a running shower).
    """

    flow_percent: int = 100
    outlet: Outlet = Outlet.SHOWERHEAD


class KohlerKonnectCoordinator(DataUpdateCoordinator[dict[str, DeviceState]]):
    """Polls device state for every Anthem device on the account."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: KohlerAnthemClient,
        tenant_id: str,
        devices: list[Device],
        temperature_unit: str = "Fahrenheit",
        water_units: str = "Standard",
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
        # The account's water volume unit ("Gallons"/"Liters"/"Standard").
        self.water_units = water_units
        # The Kohler account's temperature unit ("Celsius"/"Fahrenheit"). The
        # API returns and accepts setpoints in this unit on reads, so entities
        # present temperatures in it and convert to Celsius only at the
        # library's write boundary.
        self.temperature_unit = temperature_unit
        # Presets/experiences per device. They change rarely (only when the
        # user edits them in the Kohler app), so they're refreshed every
        # PRESET_REFRESH_CYCLES state polls instead of every poll.
        self.presets: dict[str, PresetResponse] = {}
        self._preset_poll_countdown = 0
        self.runtime: dict[str, DeviceRuntime] = {
            device.device_id: DeviceRuntime() for device in devices
        }

    def device_is_running(self, device_id: str) -> bool:
        """True if any valve on the device is actively flowing water."""
        state = (self.data or {}).get(device_id)
        if state is None:
            return False
        return any(
            valve.is_active or valve.at_flow for valve in state.state.valve_state
        )

    def current_setpoint_celsius(self, device_id: str) -> float:
        """The primary valve's temperature setpoint, in Celsius.

        The Kohler API already reports the setpoint in Celsius, so it can be
        passed straight to the library's Celsius write methods with no
        conversion.
        """
        state = (self.data or {}).get(device_id)
        if state is not None:
            for valve in state.state.valve_state:
                if valve.valve_index == "Valve1" and valve.temperature_setpoint:
                    return valve.temperature_setpoint
        return 38.0

    async def async_apply_runtime(self, device_id: str, action: str) -> None:
        """Re-send the running command with the current runtime flow/outlet.

        Used by the flow number and outlet select entities so changes take
        effect immediately while the shower is running. No-op when the water
        is off (the setting is simply applied on the next start).
        """
        if not self.device_is_running(device_id):
            return
        runtime = self.runtime[device_id]
        await run_device_command(
            self.client.turn_on_outlet(
                self.tenant_id,
                device_id,
                runtime.outlet,
                temperature_celsius=self.current_setpoint_celsius(device_id),
                flow_percent=runtime.flow_percent,
            ),
            action,
        )
        await self.async_request_refresh()

    async def _async_refresh_presets(self) -> None:
        """Fetch presets for every device; failures keep the previous cache."""
        for device in self.devices:
            try:
                self.presets[device.device_id] = await self.client.get_presets(
                    device.device_id
                )
            except AuthenticationError:
                raise
            except KohlerAnthemError as err:
                _LOGGER.debug(
                    "Could not refresh presets for %s: %s", device.device_id, err
                )

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
        # Start from the last-known states so a single device's transient read
        # failure (e.g. it's briefly offline) doesn't blank out every entity by
        # failing the whole coordinator update.
        states: dict[str, DeviceState] = dict(self.data or {})
        any_success = False
        errors: list[str] = []

        if self._preset_poll_countdown <= 0:
            try:
                await self._async_refresh_presets()
            except AuthenticationError as err:
                raise ConfigEntryAuthFailed(
                    f"Authentication failed during update: {err}"
                ) from err
            self._preset_poll_countdown = PRESET_REFRESH_CYCLES
        self._preset_poll_countdown -= 1

        for device in self.devices:
            try:
                states[device.device_id] = await self.client.get_device_state(
                    device.device_id
                )
                any_success = True
            except AuthenticationError as err:
                # Auth problems are not per-device — bail to reauth immediately.
                raise ConfigEntryAuthFailed(
                    f"Authentication failed during update: {err}"
                ) from err
            except KohlerAnthemError as err:
                # Keep this device's previous state; log offline gently.
                if is_offline_error(err):
                    _LOGGER.debug(
                        "Device %s is offline; keeping last-known state",
                        device.device_id,
                    )
                else:
                    errors.append(f"{device.device_id}: {err}")

        # Only fail the whole update if we have no states at all AND nothing
        # succeeded — otherwise entities stay available with last-known data.
        if not states and not any_success:
            raise UpdateFailed(
                "Error communicating with Kohler API: " + "; ".join(errors)
                if errors
                else "No device state available"
            )
        if errors:
            _LOGGER.warning("Kohler update had errors: %s", "; ".join(errors))

        # A successful read may have rotated the B2C refresh token.
        if any_success:
            self._persist_rotated_token()
        return states


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Kohler Konnect from a config entry."""
    if not entry.data.get(CONF_B2C_REFRESH_TOKEN):
        # Pre-0.3.0 entries (username/password only) can't write to the device
        # under Kohler's current backend. Force reauth, which walks the user
        # through the in-app Kohler sign-in to seed a refresh token.
        raise ConfigEntryAuthFailed(
            "Kohler now requires sign-in for shower control. Use the "
            "integration's reauth prompt to sign in."
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

    # The account's temperature unit governs how the API reports/accepts
    # setpoints. Prefer the value captured at config time; fall back to the
    # live customer record.
    temperature_unit = entry.data.get(CONF_TEMPERATURE_UNIT) or getattr(
        customer, "temperature_unit", "Fahrenheit"
    )
    water_units = getattr(customer, "water_units", "Standard")

    coordinator = KohlerKonnectCoordinator(
        hass, entry, client, tenant_id, devices, temperature_unit, water_units
    )
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
