"""Switch entity for Kohler Konnect (shower warmup)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from kohler_anthem.exceptions import KohlerAnthemError
from kohler_anthem.models import Device

from . import KohlerKonnectCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Kohler Konnect warmup switch."""
    coordinator: KohlerKonnectCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        KohlerWarmupSwitch(coordinator, device) for device in coordinator.devices
    )


class KohlerWarmupSwitch(CoordinatorEntity[KohlerKonnectCoordinator], SwitchEntity):
    """Switch to start/stop shower warmup."""

    _attr_has_entity_name = True
    _attr_name = "Shower Warmup"
    _attr_icon = "mdi:shower"

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, device: Device
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device.device_id
        self._device = device

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_warmup_switch"

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
    def is_on(self) -> bool:
        state = self.coordinator.data.get(self._device_id)
        return bool(state and state.is_warming_up)

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.client.start_warmup(
                self.coordinator.tenant_id, self._device_id
            )
        except KohlerAnthemError as err:
            _LOGGER.error("Failed to start warmup: %s", err)
            return
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.client.stop_warmup(
                self.coordinator.tenant_id, self._device_id
            )
        except KohlerAnthemError as err:
            _LOGGER.error("Failed to stop warmup: %s", err)
            return
        await self.coordinator.async_request_refresh()
