"""Sensor entities for Kohler Konnect."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities = []
    for device_id, state in coordinator.data.items():
        device = state["device"]
        entities += [
            KohlerConnectionSensor(coordinator, device_id, device),
            KohlerTemperatureSensor(coordinator, device_id, device),
            KohlerWarmupStateSensor(coordinator, device_id, device),
            KohlerCurrentPresetSensor(coordinator, device_id, device),
        ]
    async_add_entities(entities)


class KohlerBaseSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, device_id, device):
        super().__init__(coordinator)
        self._device_id = device_id
        self._device = device

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": self._device.get("logicalName", "Kohler Anthem Shower"),
            "manufacturer": "Kohler",
            "model": "Anthem Shower (GCS)",
            "sw_version": None,
        }

    def _state_data(self):
        return self.coordinator.data.get(self._device_id, {})

    def _advanced(self):
        return self._state_data().get("advanced_state", {})

    def _evo(self):
        return self._state_data().get("evo_state", {})


class KohlerConnectionSensor(KohlerBaseSensor):
    _attr_name = "Connection State"
    _attr_icon = "mdi:wifi"

    @property
    def unique_id(self):
        return f"{self._device_id}_connection"

    @property
    def native_value(self):
        return self._evo().get("connectionState", "Unknown")


class KohlerTemperatureSensor(KohlerBaseSensor):
    _attr_name = "Target Temperature"
    _attr_native_unit_of_measurement = "°C"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_icon = "mdi:thermometer-water"

    @property
    def unique_id(self):
        return f"{self._device_id}_target_temp"

    @property
    def native_value(self):
        try:
            valves = self._advanced().get("state", {}).get("valveState", [])
            if valves:
                return float(valves[0].get("temperatureSetpoint", 0))
        except (ValueError, KeyError, IndexError):
            pass
        return None


class KohlerWarmupStateSensor(KohlerBaseSensor):
    _attr_name = "Warmup State"
    _attr_icon = "mdi:shower-head"

    @property
    def unique_id(self):
        return f"{self._device_id}_warmup_state"

    @property
    def native_value(self):
        try:
            return (
                self._advanced()
                .get("state", {})
                .get("warmUpState", {})
                .get("state", "unknown")
            )
        except (KeyError, AttributeError):
            return "unknown"


class KohlerCurrentPresetSensor(KohlerBaseSensor):
    _attr_name = "Active Preset"
    _attr_icon = "mdi:playlist-play"

    @property
    def unique_id(self):
        return f"{self._device_id}_active_preset"

    @property
    def native_value(self):
        try:
            pid = (
                self._advanced()
                .get("state", {})
                .get("presetOrExperienceId", "0")
            )
            return pid if pid != "0" else "none"
        except (KeyError, AttributeError):
            return "none"
