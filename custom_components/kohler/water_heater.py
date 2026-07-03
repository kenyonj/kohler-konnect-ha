"""Water heater entity for the Kohler Konnect Anthem shower."""

from __future__ import annotations

import asyncio
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

from kohler_anthem import Outlet, encode_valve_command
from kohler_anthem.models import (
    Device,
    DeviceState,
    ValveControlModel,
    ValveMode,
    ValvePrefix,
)

from . import KohlerKonnectCoordinator, run_device_command
from .const import DOMAIN

OPERATION_OFF = "off"
OPERATION_WARMUP = "warmup"
OPERATION_RUNNING = "running"

# Temperature bounds expressed per unit. The Kohler API reports and accepts
# setpoints in the *account's* unit, so the entity presents that unit directly
# and only converts to Celsius at the library's write boundary.
TEMP_MIN_C, TEMP_MAX_C, TEMP_DEFAULT_C = 15.0, 45.0, 39.3
TEMP_MIN_F, TEMP_MAX_F, TEMP_DEFAULT_F = 60.0, 113.0, 103.0

SUPPORT_FLAGS = (
    WaterHeaterEntityFeature.TARGET_TEMPERATURE
    | WaterHeaterEntityFeature.OPERATION_MODE
)

# Maps the API's valveIndex names to the solowritesystem payload field and the
# valve-prefix byte the firmware expects in each 4-byte command.
VALVE_FIELD_AND_PREFIX = {
    "Valve1": ("primary_valve1", ValvePrefix.PRIMARY),
    "Valve2": ("secondary_valve1", ValvePrefix.SECONDARY_1),
    "Valve3": ("secondary_valve2", ValvePrefix.SECONDARY_2),
    "Valve4": ("secondary_valve3", ValvePrefix.SECONDARY_3),
    "Valve5": ("secondary_valve4", ValvePrefix.SECONDARY_4),
    "Valve6": ("secondary_valve5", ValvePrefix.SECONDARY_5),
    "Valve7": ("secondary_valve6", ValvePrefix.SECONDARY_6),
    "Valve8": ("secondary_valve7", ValvePrefix.SECONDARY_7),
}

# encode_valve_command's accepted Celsius range.
ENCODE_TEMP_MIN_C, ENCODE_TEMP_MAX_C = 15.0, 49.0


def _to_celsius(value: float, unit: str) -> float:
    """Convert an account-unit temperature to Celsius for library writes."""
    if unit == "Fahrenheit":
        return (value - 32.0) * 5.0 / 9.0
    return value


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
        # Local target-temperature setpoint (in the account's unit). The Kohler
        # API has no "set temperature without running water" command, so we hold
        # the desired temperature locally and apply it on start / while running.
        self._target_temperature: float | None = None

        # Present temperatures in the account's unit so values round-trip with
        # what the API returns (it does not convert).
        fahrenheit = coordinator.temperature_unit == "Fahrenheit"
        self._attr_temperature_unit = (
            UnitOfTemperature.FAHRENHEIT if fahrenheit else UnitOfTemperature.CELSIUS
        )
        self._attr_min_temp = TEMP_MIN_F if fahrenheit else TEMP_MIN_C
        self._attr_max_temp = TEMP_MAX_F if fahrenheit else TEMP_MAX_C
        self._default_target = TEMP_DEFAULT_F if fahrenheit else TEMP_DEFAULT_C

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
        """Measured outlet temperature (Valve1 / outlet2), in the account unit."""
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
        return self._default_target

    # -- commands ---------------------------------------------------------- #

    def _target_celsius(self) -> float:
        """Current target as Celsius for the library write API."""
        target = self.target_temperature or self._default_target
        return _to_celsius(target, self.coordinator.temperature_unit)

    def _build_off_control(self) -> ValveControlModel:
        """Build a solowritesystem payload that actually turns the water off.

        The library's ``turn_off()`` sends an all-zero ``primaryValve1``
        (``"00000000"``). The firmware ignores that command because its
        prefix byte (0x00) doesn't address any valve — which is why users
        could turn the shower on but never off. The mobile app instead sends
        ``[prefix][temp][flow]`` with mode ``0x00`` per valve (e.g.
        ``"0179c800"``); reproduce that here for every valve the device
        reports.
        """
        temp_c = min(
            max(self._target_celsius(), ENCODE_TEMP_MIN_C), ENCODE_TEMP_MAX_C
        )
        kwargs: dict[str, str] = {}
        state = self._state
        valves = state.state.valve_state if state is not None else []
        for valve in valves:
            mapping = VALVE_FIELD_AND_PREFIX.get(valve.valve_index)
            if mapping is None:
                continue
            field, prefix = mapping
            # Temp/flow bytes are ignored for OFF; they just need to be valid.
            flow = min(max(valve.flow_setpoint, 0), 100) or 100
            kwargs[field] = encode_valve_command(
                temperature_celsius=temp_c,
                flow_percent=flow,
                mode=ValveMode.OFF,
                prefix=prefix,
            )
        if "primary_valve1" not in kwargs:
            kwargs["primary_valve1"] = encode_valve_command(
                temperature_celsius=temp_c,
                flow_percent=100,
                mode=ValveMode.OFF,
                prefix=ValvePrefix.PRIMARY,
            )
        return ValveControlModel(**kwargs)

    async def _async_turn_off(self) -> None:
        """Stop any session-level activity, then close the valves."""
        client = self.coordinator.client
        tenant_id = self.coordinator.tenant_id
        state = self._state

        # Warmup and presets are session-level state on the controller;
        # clear them first so it doesn't keep the valves open. stop_warmup
        # sends presetOrExperienceId "0", which stops both.
        if state is not None and (
            state.is_warming_up or state.state.active_preset_id is not None
        ):
            await client.stop_warmup(tenant_id, self._device_id)

        await client.control_valve(
            tenant_id, self._device_id, self._build_off_control()
        )

    async def _run_command_and_refresh(self, operation: str, coro: Any) -> None:
        """Send a command, optimistically update, then re-poll twice.

        On failure (including device-offline), clear the optimistic state and
        let run_device_command raise a clean HomeAssistantError for the UI.
        """
        self._optimistic_operation = operation
        self.async_write_ha_state()

        try:
            await run_device_command(coro, operation)
        except Exception:
            # Revert optimistic state so the UI reflects reality, then re-raise
            # so HA surfaces the (already user-friendly) message.
            self._optimistic_operation = None
            self.async_write_ha_state()
            raise

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
                    temperature_celsius=_to_celsius(
                        float(temp), self.coordinator.temperature_unit
                    ),
                ),
            )

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        client = self.coordinator.client
        tenant_id = self.coordinator.tenant_id

        if operation_mode == OPERATION_WARMUP:
            coro = client.start_warmup(tenant_id, self._device_id)
        elif operation_mode == OPERATION_OFF:
            coro = self._async_turn_off()
        elif operation_mode == OPERATION_RUNNING:
            coro = client.turn_on_outlet(
                tenant_id,
                self._device_id,
                Outlet.SHOWERHEAD,
                temperature_celsius=self._target_celsius(),
            )
        else:
            return

        await self._run_command_and_refresh(operation_mode, coro)
