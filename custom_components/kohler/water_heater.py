"""Water heater entity for Kohler Konnect Anthem shower."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
    STATE_OFF,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

OPERATION_OFF = "off"
OPERATION_WARMUP = "warmup"
OPERATION_RUNNING = "running"

SUPPORT_FLAGS = (
    WaterHeaterEntityFeature.TARGET_TEMPERATURE
    | WaterHeaterEntityFeature.OPERATION_MODE
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    api = data["api"]

    entities = []
    for device_id, state in coordinator.data.items():
        device = state["device"]
        entities.append(
            KohlerAnthemShower(coordinator, api, device_id, device)
        )
    async_add_entities(entities)


class KohlerAnthemShower(CoordinatorEntity, WaterHeaterEntity):
    """Represents the Kohler Anthem shower as a water heater entity."""

    _attr_has_entity_name = True
    _attr_name = "Anthem Shower"
    _attr_icon = "mdi:shower-head"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 15.0
    _attr_max_temp = 45.0
    _attr_target_temperature_step = 0.5
    _attr_supported_features = SUPPORT_FLAGS
    _attr_operation_list = [OPERATION_OFF, OPERATION_WARMUP, OPERATION_RUNNING]

    def __init__(self, coordinator, api, device_id: str, device: dict) -> None:
        super().__init__(coordinator)
        self._api = api
        self._device_id = device_id
        self._device = device
        self._optimistic_operation: str | None = None

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_shower"

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": self._device.get("logicalName", "Kohler Anthem Shower"),
            "manufacturer": "Kohler",
            "model": "Anthem Shower (GCS)",
            "serial_number": self._device.get("serialNumber"),
        }

    def _handle_coordinator_update(self) -> None:
        """Clear optimistic state when real data arrives."""
        self._optimistic_operation = None
        super()._handle_coordinator_update()

    def _adv(self) -> dict:
        return (
            self.coordinator.data.get(self._device_id, {})
            .get("advanced_state", {})
            .get("state", {})
        )

    def _real_operation(self) -> str:
        adv = self._adv()
        warmup_state = adv.get("warmUpState", {}).get("state", "warmUpNotInProgress")
        if warmup_state != "warmUpNotInProgress":
            return OPERATION_WARMUP

        for valve in adv.get("valveState", []):
            if float(valve.get("atFlow", "0")) > 0:
                return OPERATION_RUNNING

        return OPERATION_OFF

    @property
    def current_operation(self) -> str:
        # Return optimistic state immediately after a command
        if self._optimistic_operation is not None:
            return self._optimistic_operation
        return self._real_operation()

    @property
    def current_temperature(self) -> float | None:
        """Return actual outlet temperature (Valve1/Outlet2)."""
        try:
            for valve in self._adv().get("valveState", []):
                if valve.get("valveIndex") == "Valve1":
                    for outlet in valve.get("outlets", []):
                        if outlet.get("outletIndex") == "outlet2":
                            t = outlet.get("outletTemp", "0")
                            v = float(t) if t else 0.0
                            return v if v > 0 else None
        except (ValueError, KeyError, TypeError):
            pass
        return None

    @property
    def target_temperature(self) -> float | None:
        try:
            for valve in self._adv().get("valveState", []):
                if valve.get("valveIndex") == "Valve1":
                    return float(valve.get("temperatureSetpoint", 39.3))
        except (ValueError, KeyError, TypeError):
            pass
        return 39.3

    async def _send_command_and_refresh(self, operation: str, coro) -> None:
        """Send a command, optimistically update state, then re-poll after a delay."""
        # Set optimistic state immediately so UI updates right away
        self._optimistic_operation = operation
        self.async_write_ha_state()

        try:
            await coro
        except Exception as err:
            _LOGGER.error("Kohler command failed: %s", err)
            self._optimistic_operation = None
            self.async_write_ha_state()
            return

        # Poll once immediately, then again after 5s to catch delayed API updates
        await self.coordinator.async_request_refresh()
        await asyncio.sleep(5)
        await self.coordinator.async_request_refresh()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get("temperature")
        if temp is None:
            return
        await self.hass.async_add_executor_job(
            self._api.write_outlet_config,
            self._device_id,
            "Valve1",
            "2",
            float(temp),
            19,
        )
        await self.coordinator.async_request_refresh()

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        if operation_mode == OPERATION_WARMUP:
            coro = self.hass.async_add_executor_job(
                self._api.start_warmup, self._device_id
            )
        elif operation_mode == OPERATION_OFF:
            coro = self.hass.async_add_executor_job(
                self._api.stop_shower, self._device_id
            )
        elif operation_mode == OPERATION_RUNNING:
            coro = self.hass.async_add_executor_job(
                self._api.start_preset, self._device_id, "1"
            )
        else:
            return

        await self._send_command_and_refresh(operation_mode, coro)
