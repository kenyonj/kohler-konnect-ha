"""Number entity for Kohler Konnect (water flow percentage)."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KohlerKonnectCoordinator
from .const import DOMAIN
from .entity import KohlerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Kohler Konnect flow number entity."""
    coordinator: KohlerKonnectCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        KohlerFlowNumber(coordinator, device) for device in coordinator.devices
    )


class KohlerFlowNumber(KohlerEntity, NumberEntity):
    """Desired water flow, as a percentage of the valve's maximum.

    Like the target temperature, the Kohler API has no "set flow without
    running water" command, so the value is held locally and used when the
    shower starts. If water is already running, the change is applied live.
    """

    _attr_name = "Flow"
    _attr_icon = "mdi:water-percent"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 10
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_mode = NumberMode.SLIDER

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_flow"

    @property
    def native_value(self) -> float:
        return float(self.coordinator.runtime[self._device_id].flow_percent)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.runtime[self._device_id].flow_percent = int(value)
        self.async_write_ha_state()
        await self.coordinator.async_apply_runtime(
            self._device_id, f"set flow to {int(value)}%"
        )
