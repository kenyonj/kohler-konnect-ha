"""Water heater entity for the Kohler Konnect Anthem shower."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from kohler_anthem import Outlet
from kohler_anthem.exceptions import KohlerAnthemError
from kohler_anthem.models import Device, DeviceState

from . import KohlerKonnectCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

OPERATION_OFF = "off"
OPERATION_WARMUP = "warmup"
OPERATION_RUNNING = "running"

DEFAULT_TARGET_TEMP = 39.3

SUPPORT_FLAGS = (
    WaterHeaterEntityFeature.TARGET_TEMPERATURE
    | WaterHeaterEntityFeature.OPERATION_MODE
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Kohler Konnect water heater entity."""
    coordinator: KohlerKonnectCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        KohlerAnthemShower(coordinator, device) for device in coordinator.devices
    )


class KohlerAnthemShower(
    CoordinatorEntity[KohlerKonnectCoordinator], WaterHeaterEntity
):
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

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, device: Device
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device.device_id
        self._device = device
        self._optimistic_operation: str | None = None
        # Local target-temperature setpoint. The Kohler API has no "set
        # temperature without running water" command, so we hold the desired
        # temperature locally and apply it when the shower is started or while
        # it is running.
        self._target_temperature: float | None = None

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_shower"

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": self._device.logical_name or "Kohler Anthem Shower",
            "manufacturer": "Kohler",
            "model": "Anthem Shower (GCS)",
            "serial_number": self._device.serial_number,
        }

    # -- state helpers ----------------------------------------------------- #

    @property
    def _state(self) -> DeviceState | None:
        return self.coordinator.data.get(self._device_id)

    def _handle_coordinator_update(self) -> None:
        """Clear optimistic operation when fresh data arrives."""
        self._optimistic_operation = None
        super()._handle_coordinator_update()

    def _real_operation(self) -> str:
        state = self._state
        if state is None:
            return OPERATION_OFF
        if state.is_warming_up:
            return OPERATION_WARMUP
        for valve in state.state.valve_state:
            if valve.is_active or valve.at_flow:
                return OPERATION_RUNNING
        return OPERATION_OFF

    @property
    def current_operation(self) -> str:
        if self._optimistic_operation is not None:
            return self._optimistic_operation
        return self._real_operation()

    @property
    def current_temperature(self) -> float | None:
        """Actual measured outlet temperature (Valve1 / outlet2)."""
        state = self._state
        if state is None:
            return None
        for valve in state.state.valve_state:
            if valve.valve_index == "Valve1":
                for outlet in valve.outlets:
                    if outlet.outlet_index == "outlet2":
                        return outlet.outlet_temp or None
        return None

    @property
    def target_temperature(self) -> float | None:
        if self._target_temperature is not None:
            return self._target_temperature
        state = self._state
        if state is not None:
            for valve in state.state.valve_state:
                if valve.valve_index == "Valve1" and valve.temperature_setpoint:
                    return valve.temperature_setpoint
        return DEFAULT_TARGET_TEMP

    # -- commands ---------------------------------------------------------- #

    async def _run_command_and_refresh(self, operation: str, coro: Any) -> None:
        """Send a command, optimistically update, then re-poll twice."""
        self._optimistic_operation = operation
        self.async_write_ha_state()

        try:
            await coro
        except KohlerAnthemError as err:
            _LOGGER.error("Kohler command failed: %s", err)
            self._optimistic_operation = None
            self.async_write_ha_state()
            return

        await self.coordinator.async_request_refresh()
        await asyncio.sleep(5)
        await self.coordinator.async_request_refresh()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        self._target_temperature = float(temp)
        self.async_write_ha_state()

        # If the shower is actively running, apply the new temperature live.
        if self._real_operation() == OPERATION_RUNNING:
            await self._run_command_and_refresh(
                OPERATION_RUNNING,
                self.coordinator.client.turn_on_outlet(
                    self.coordinator.tenant_id,
                    self._device_id,
                    Outlet.SHOWERHEAD,
                    temperature_celsius=float(temp),
                ),
            )

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        client = self.coordinator.client
        tenant_id = self.coordinator.tenant_id

        if operation_mode == OPERATION_WARMUP:
            coro = client.start_warmup(tenant_id, self._device_id)
        elif operation_mode == OPERATION_OFF:
            coro = client.turn_off(tenant_id, self._device_id)
        elif operation_mode == OPERATION_RUNNING:
            coro = client.turn_on_outlet(
                tenant_id,
                self._device_id,
                Outlet.SHOWERHEAD,
                temperature_celsius=self.target_temperature or DEFAULT_TARGET_TEMP,
            )
        else:
            return

        await self._run_command_and_refresh(operation_mode, coro)
