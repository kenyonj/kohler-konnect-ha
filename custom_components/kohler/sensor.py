"""Sensor entities for Kohler Konnect."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from kohler_anthem.models import Device, DeviceState

from . import KohlerKonnectCoordinator
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Kohler Konnect sensors."""
    coordinator: KohlerKonnectCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []
    for device in coordinator.devices:
        entities += [
            KohlerConnectionSensor(coordinator, device),
            KohlerTargetTemperatureSensor(coordinator, device),
            KohlerWarmupStateSensor(coordinator, device),
            KohlerActivePresetSensor(coordinator, device),
        ]
    async_add_entities(entities)


class KohlerBaseSensor(CoordinatorEntity[KohlerKonnectCoordinator], SensorEntity):
    """Base class for Kohler Konnect sensors."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, device: Device
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device.device_id
        self._device = device

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": self._device.logical_name or "Kohler Anthem Shower",
            "manufacturer": "Kohler",
            "model": "Anthem Shower (GCS)",
            "serial_number": self._device.serial_number,
        }

    @property
    def _state(self) -> DeviceState | None:
        return self.coordinator.data.get(self._device_id)


class KohlerConnectionSensor(KohlerBaseSensor):
    """Reports the device's cloud connection state."""

    _attr_name = "Connection State"
    _attr_icon = "mdi:wifi"

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_connection"

    @property
    def native_value(self) -> str | None:
        state = self._state
        if state is None:
            return None
        return state.connection_state.value


class KohlerTargetTemperatureSensor(KohlerBaseSensor):
    """Reports the primary valve's target temperature in the account's unit."""

    _attr_name = "Target Temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_icon = "mdi:thermometer-water"

    @property
    def native_unit_of_measurement(self) -> str:
        # The API returns the setpoint in the account's unit; label it to match.
        if self.coordinator.temperature_unit == "Fahrenheit":
            return UnitOfTemperature.FAHRENHEIT
        return UnitOfTemperature.CELSIUS

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_target_temp"

    @property
    def native_value(self) -> float | None:
        state = self._state
        if state is None:
            return None
        for valve in state.state.valve_state:
            if valve.valve_index == "Valve1":
                return valve.temperature_setpoint or None
        return None


class KohlerWarmupStateSensor(KohlerBaseSensor):
    """Reports the warmup state machine value."""

    _attr_name = "Warmup State"
    _attr_icon = "mdi:shower-head"

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_warmup_state"

    @property
    def native_value(self) -> str | None:
        state = self._state
        if state is None:
            return None
        return state.state.warm_up_state.state.value


class KohlerActivePresetSensor(KohlerBaseSensor):
    """Reports the currently active preset/experience id (or 'none')."""

    _attr_name = "Active Preset"
    _attr_icon = "mdi:playlist-play"

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_active_preset"

    @property
    def native_value(self) -> str:
        state = self._state
        if state is None:
            return "none"
        preset_id = state.state.active_preset_id
        return str(preset_id) if preset_id is not None else "none"
