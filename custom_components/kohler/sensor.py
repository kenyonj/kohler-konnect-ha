"""Sensor entities for Kohler Konnect."""

from __future__ import annotations

from datetime import UTC, datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from kohler_anthem import gallons_to_liters

from . import KohlerKonnectCoordinator
from .const import DOMAIN
from .entity import KohlerEntity
from .helpers import from_celsius

KohlerBaseSensor = KohlerEntity  # retained name; all sensors share the base


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
            KohlerSystemStateSensor(coordinator, device),
            KohlerTotalWaterSensor(coordinator, device),
            KohlerLastConnectedSensor(coordinator, device),
        ]
    async_add_entities(entities)


class KohlerConnectionSensor(KohlerBaseSensor, SensorEntity):
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


class KohlerTargetTemperatureSensor(KohlerBaseSensor, SensorEntity):
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
                setpoint = valve.temperature_setpoint
                if not setpoint:
                    return None
                # The API reports the setpoint in Celsius; present it in the
                # account's unit to match native_unit_of_measurement.
                return round(
                    from_celsius(setpoint, self.coordinator.temperature_unit), 1
                )
        return None


class KohlerWarmupStateSensor(KohlerBaseSensor, SensorEntity):
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


class KohlerActivePresetSensor(KohlerBaseSensor, SensorEntity):
    """Reports the currently active preset/experience by name (or 'none')."""

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
        if preset_id is None:
            return "none"
        # Enrich the raw id with the preset's name when the cache has it.
        response = self.coordinator.presets.get(self._device_id)
        if response is not None:
            preset = response.get_preset(preset_id)
            if preset is not None and (preset.title or preset.logical_name):
                return preset.title or preset.logical_name
        return str(preset_id)


class KohlerSystemStateSensor(KohlerBaseSensor, SensorEntity):
    """Reports the controller's system state (normal / shower in progress)."""

    _attr_name = "System State"
    _attr_icon = "mdi:state-machine"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["normalOperation", "showerInProgress"]

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_system_state"

    @property
    def native_value(self) -> str | None:
        state = self._state
        if state is None:
            return None
        return state.state.current_system_state.value


class KohlerTotalWaterSensor(KohlerBaseSensor, SensorEntity):
    """Lifetime water volume used by the device.

    Reads the API's ``totalFlow`` field, documented upstream as the lifetime
    flow in US gallons. (The earlier "Session Volume" sensor read
    ``totalVolume`` — an undocumented, unknown-unit counter that read in the
    hundreds of millions and was wrongly labelled gallons. Kohler exposes no
    per-session volume; both counters are lifetime totals.) Presented in the
    account's water unit and marked total_increasing so it feeds HA's
    long-term statistics / water dashboard.
    """

    _attr_name = "Total Water Used"
    _attr_icon = "mdi:water"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_unit_of_measurement(self) -> str:
        # totalFlow is reported in US gallons; convert to litres for metric
        # ("Liters") accounts.
        if self.coordinator.water_units == "Liters":
            return UnitOfVolume.LITERS
        return UnitOfVolume.GALLONS

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_total_water"

    @property
    def native_value(self) -> float | None:
        state = self._state
        if state is None:
            return None
        gallons = state.state.total_flow
        if self.coordinator.water_units == "Liters":
            return round(gallons_to_liters(gallons), 1)
        return round(gallons, 1)


class KohlerLastConnectedSensor(KohlerBaseSensor, SensorEntity):
    """When the device last checked in with Kohler's cloud."""

    _attr_name = "Last Connected"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_registry_enabled_default = False

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_last_connected"

    @property
    def native_value(self) -> datetime | None:
        state = self._state
        if state is None or not state.last_connected:
            return None
        epoch = state.last_connected
        # The API reports epoch milliseconds; tolerate seconds just in case.
        if epoch > 10**12:
            epoch //= 1000
        return datetime.fromtimestamp(epoch, tz=UTC)
