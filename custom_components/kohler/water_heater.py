"""Water heater entity for the Kohler Konnect Anthem shower."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from kohler_anthem.models import Device

from . import KohlerKonnectCoordinator, run_device_command
from .const import (
    DOMAIN,
    SERVICE_PAUSE_SHOWER,
    SERVICE_START_PRESET,
    SERVICE_START_WARMUP,
    SERVICE_STOP_SHOWER,
    WARMUP_DISABLED_MESSAGE,
)
from .entity import KohlerEntity
from .helpers import build_off_control, clamp_encode_temp, from_celsius, to_celsius

OPERATION_OFF = "off"
OPERATION_WARMUP = "warmup"
OPERATION_RUNNING = "running"
OPERATION_PAUSE = "pause"

# Temperature bounds expressed per unit. The Kohler API reports and accepts
# setpoints in the *account's* unit, so the entity presents that unit directly
# and only converts to Celsius at the library's write boundary.
TEMP_MIN_C, TEMP_MAX_C, TEMP_DEFAULT_C = 15.0, 45.0, 39.3
TEMP_MIN_F, TEMP_MAX_F, TEMP_DEFAULT_F = 60.0, 113.0, 103.0

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

    # Entity services documented in the README; targetable at the shower
    # entity from automations (e.g. kohler.start_preset with preset_id).
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_START_PRESET,
        {vol.Required("preset_id"): cv.positive_int},
        "async_start_preset",
    )
    platform.async_register_entity_service(
        SERVICE_START_WARMUP, None, "async_start_warmup"
    )
    platform.async_register_entity_service(
        SERVICE_STOP_SHOWER, None, "async_stop_shower"
    )
    platform.async_register_entity_service(
        SERVICE_PAUSE_SHOWER, None, "async_pause_shower"
    )


class KohlerAnthemShower(KohlerEntity, WaterHeaterEntity):
    """Represents the Kohler Anthem shower as a water heater entity."""

    _attr_name = "Anthem Shower"
    _attr_icon = "mdi:shower-head"
    _attr_target_temperature_step = 0.5
    _attr_supported_features = SUPPORT_FLAGS
    _attr_operation_list = [
        OPERATION_OFF,
        OPERATION_WARMUP,
        OPERATION_RUNNING,
        OPERATION_PAUSE,
    ]

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, device: Device
    ) -> None:
        super().__init__(coordinator, device)
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

    # -- state helpers ----------------------------------------------------- #

    @property
    def _runtime(self):
        return self.coordinator.runtime[self._device_id]

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
        paused = False
        for valve in state.state.valve_state:
            if valve.is_active or valve.at_flow:
                return OPERATION_RUNNING
            if valve.pause_flag:
                paused = True
        return OPERATION_PAUSE if paused else OPERATION_OFF

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
                        temp_c = outlet.outlet_temp
                        if not temp_c:
                            return None
                        # The API reports temperatures in Celsius; present in
                        # the account's unit to match _attr_temperature_unit.
                        return round(
                            from_celsius(temp_c, self.coordinator.temperature_unit), 1
                        )
        return None

    @property
    def target_temperature(self) -> float | None:
        if self._target_temperature is not None:
            return self._target_temperature
        state = self._state
        if state is not None:
            for valve in state.state.valve_state:
                if valve.valve_index == "Valve1" and valve.temperature_setpoint:
                    # API setpoint is Celsius; present in the account's unit.
                    return round(
                        from_celsius(
                            valve.temperature_setpoint,
                            self.coordinator.temperature_unit,
                        ),
                        1,
                    )
        return self._default_target

    # -- commands ---------------------------------------------------------- #

    def _guard_warmup_enabled(self) -> None:
        """Block a warmup command when the feature is disabled on the fixture.

        Kohler's cloud accepts the warmup command even when warmup is turned
        off on the shower, then the device ignores it — so without this guard
        the warmup control silently does nothing. Raise a clear error instead.
        Unknown state (no data read yet) is allowed through.
        """
        if self.coordinator.is_warmup_enabled(self._device_id) is False:
            raise HomeAssistantError(WARMUP_DISABLED_MESSAGE)

    def _target_celsius(self) -> float:
        """Current target as Celsius for the library write API."""
        target = self.target_temperature or self._default_target
        return to_celsius(target, self.coordinator.temperature_unit)

    def _turn_on_coro(self, temperature_celsius: float | None = None) -> Any:
        """Coroutine that starts water on the selected outlet at the desired
        flow (both held by the coordinator's per-device runtime settings)."""
        runtime = self._runtime
        return self.coordinator.client.turn_on_outlet(
            self.coordinator.tenant_id,
            self._device_id,
            runtime.outlet,
            temperature_celsius=(
                temperature_celsius
                if temperature_celsius is not None
                else self._target_celsius()
            ),
            flow_percent=runtime.flow_percent,
        )

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
            tenant_id,
            self._device_id,
            build_off_control(state, self._target_celsius()),
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
                self._turn_on_coro(
                    to_celsius(float(temp), self.coordinator.temperature_unit)
                ),
            )

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        client = self.coordinator.client
        tenant_id = self.coordinator.tenant_id

        if operation_mode == OPERATION_WARMUP:
            self._guard_warmup_enabled()
            coro = client.start_warmup(tenant_id, self._device_id)
        elif operation_mode == OPERATION_OFF:
            coro = self._async_turn_off()
        elif operation_mode == OPERATION_RUNNING:
            coro = self._turn_on_coro()
        elif operation_mode == OPERATION_PAUSE:
            coro = self._pause_coro()
        else:
            return

        await self._run_command_and_refresh(operation_mode, coro)

    def _pause_coro(self) -> Any:
        """Coroutine that pauses water flow but keeps the session active."""
        runtime = self._runtime
        return self.coordinator.client.pause(
            self.coordinator.tenant_id,
            self._device_id,
            temperature_celsius=clamp_encode_temp(self._target_celsius()),
            flow_percent=runtime.flow_percent,
        )

    # -- entity services ---------------------------------------------------- #

    async def async_start_preset(self, preset_id: int) -> None:
        """Start a preset by id (kohler.start_preset service).

        Routes through the coordinator's async_start_preset, which sends the
        corrected two-step (select + mode-0x01 valve write) and raises a clear
        error for experiences, which can't be started from HA.
        """
        presets = self.coordinator.presets.get(self._device_id)
        preset = presets.get_preset(preset_id) if presets else None
        if preset is None:
            known = (
                ", ".join(p.preset_id for p in presets.presets)
                if presets and presets.presets
                else "none"
            )
            raise HomeAssistantError(
                f"Unknown Kohler preset id {preset_id} (known ids: {known})"
            )
        self._optimistic_operation = OPERATION_RUNNING
        self.async_write_ha_state()
        try:
            await self.coordinator.async_start_preset(self._device_id, preset)
        except Exception:
            self._optimistic_operation = None
            self.async_write_ha_state()
            raise

    async def async_start_warmup(self) -> None:
        """Start warmup (kohler.start_warmup service)."""
        self._guard_warmup_enabled()
        await self._run_command_and_refresh(
            OPERATION_WARMUP,
            self.coordinator.client.start_warmup(
                self.coordinator.tenant_id, self._device_id
            ),
        )

    async def async_stop_shower(self) -> None:
        """Stop all water flow (kohler.stop_shower service)."""
        await self._run_command_and_refresh(OPERATION_OFF, self._async_turn_off())

    async def async_pause_shower(self) -> None:
        """Pause water flow, keeping the session active (kohler.pause_shower)."""
        await self._run_command_and_refresh(OPERATION_PAUSE, self._pause_coro())
