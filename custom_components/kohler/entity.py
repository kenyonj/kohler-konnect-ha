"""Shared entity base for Kohler Konnect platforms."""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from kohler_anthem.models import Device, DeviceState

from . import KohlerKonnectCoordinator
from .const import DOMAIN


class KohlerEntity(CoordinatorEntity[KohlerKonnectCoordinator]):
    """Base entity: wires up the coordinator and shared device registry info."""

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
