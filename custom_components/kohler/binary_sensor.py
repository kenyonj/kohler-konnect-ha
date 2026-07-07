"""Binary sensors for Kohler Konnect."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KohlerKonnectCoordinator
from .const import DOMAIN
from .entity import KohlerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Kohler Konnect binary sensors."""
    coordinator: KohlerKonnectCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []
    for device in coordinator.devices:
        entities += [
            KohlerRunningBinarySensor(coordinator, device),
            KohlerValveProblemBinarySensor(coordinator, device),
            KohlerWarmupEnabledBinarySensor(coordinator, device),
        ]
    async_add_entities(entities)


class KohlerRunningBinarySensor(KohlerEntity, BinarySensorEntity):
    """On while water is flowing from any valve."""

    _attr_name = "Water Running"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:water"

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_running"

    @property
    def is_on(self) -> bool | None:
        state = self._state
        if state is None:
            return None
        return any(
            valve.is_active or valve.at_flow for valve in state.state.valve_state
        )


class KohlerValveProblemBinarySensor(KohlerEntity, BinarySensorEntity):
    """On when any valve reports an error; codes exposed as attributes."""

    _attr_name = "Valve Problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_valve_problem"

    @property
    def is_on(self) -> bool | None:
        state = self._state
        if state is None:
            return None
        return any(valve.error_flag for valve in state.state.valve_state)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._state
        if state is None:
            return {}
        return {
            f"{valve.valve_index}_error_code": valve.error_code
            for valve in state.state.valve_state
            if valve.error_flag
        }


class KohlerWarmupEnabledBinarySensor(KohlerEntity, BinarySensorEntity):
    """On when the warmup feature is enabled on the fixture.

    Diagnostic sensor for the device-state ``warmUpState.warmUp`` flag. When
    this is off, warmup commands are accepted by Kohler's cloud but ignored by
    the device, so the warmup switch appears to do nothing — this surfaces why.
    """

    _attr_name = "Warmup Enabled"
    _attr_icon = "mdi:shower-head"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_warmup_enabled"

    @property
    def is_on(self) -> bool | None:
        # None (unknown) until the first state read; keeps the entity from
        # reporting a definitive "off" before we've heard from the device.
        return self.coordinator.is_warmup_enabled(self._device_id)
