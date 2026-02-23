"""Switch entities for Kohler Konnect (warmup, presets)."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
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
    api = data["api"]

    entities = []
    for device_id, state in coordinator.data.items():
        device = state["device"]
        # Warmup switch
        entities.append(KohlerWarmupSwitch(coordinator, api, device_id, device))

    async_add_entities(entities)


class KohlerWarmupSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to trigger shower warmup."""

    _attr_name = "Shower Warmup"
    _attr_icon = "mdi:shower"

    def __init__(self, coordinator, api, device_id, device):
        super().__init__(coordinator)
        self._api = api
        self._device_id = device_id
        self._device = device
        self._is_on = False

    @property
    def unique_id(self):
        return f"{self._device_id}_warmup_switch"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": self._device.get("logicalName", "Kohler Anthem Shower"),
            "manufacturer": "Kohler",
            "model": "Anthem Shower (GCS)",
        }

    @property
    def is_on(self):
        try:
            state = (
                self.coordinator.data.get(self._device_id, {})
                .get("advanced_state", {})
                .get("state", {})
                .get("warmUpState", {})
                .get("state", "warmUpNotInProgress")
            )
            return state != "warmUpNotInProgress"
        except (KeyError, AttributeError):
            return False

    async def async_turn_on(self, **kwargs):
        await self.hass.async_add_executor_job(
            self._api.start_warmup, self._device_id
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        await self.hass.async_add_executor_job(
            self._api.stop_shower, self._device_id
        )
        await self.coordinator.async_request_refresh()
