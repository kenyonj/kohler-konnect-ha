"""Select entities for Kohler Konnect (preset/experience and outlet)."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from kohler_anthem.models import Outlet, Preset

from . import KohlerKonnectCoordinator, run_device_command
from .const import DOMAIN
from .entity import KohlerEntity

_LOGGER = logging.getLogger(__name__)

PRESET_NONE = "none"

OUTLET_OPTIONS = {
    "showerhead": Outlet.SHOWERHEAD,
    "handshower": Outlet.HANDSHOWER,
    "tub filler": Outlet.TUB_FILLER,
    "tub + handheld": Outlet.TUB_HANDHELD,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Kohler Konnect select entities."""
    coordinator: KohlerKonnectCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SelectEntity] = []
    for device in coordinator.devices:
        entities += [
            KohlerPresetSelect(coordinator, device),
            KohlerOutletSelect(coordinator, device),
        ]
    async_add_entities(entities)


class KohlerPresetSelect(KohlerEntity, SelectEntity):
    """Start/stop the shower's presets and experiences.

    Options mirror the presets configured in the Kohler app. Selecting one
    starts it (controlpresetorexperience + valve activation); selecting
    "none" stops the running preset.
    """

    _attr_name = "Preset"
    _attr_icon = "mdi:playlist-play"

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_preset_select"

    def _label_for(self, preset: Preset) -> str:
        title = preset.title or preset.logical_name
        if title:
            return f"{title} ({preset.preset_id})"
        kind = "Experience" if preset.is_experience else "Preset"
        return f"{kind} {preset.preset_id}"

    def _labels_to_presets(self) -> dict[str, Preset]:
        response = self.coordinator.presets.get(self._device_id)
        if response is None:
            return {}
        return {self._label_for(p): p for p in response.presets}

    @property
    def options(self) -> list[str]:
        return [PRESET_NONE, *self._labels_to_presets()]

    @property
    def current_option(self) -> str | None:
        state = self._state
        if state is None:
            return None
        active_id = state.state.active_preset_id
        if active_id is None:
            return PRESET_NONE
        for label, preset in self._labels_to_presets().items():
            if preset.id == active_id:
                return label
        # Active preset we don't have metadata for (e.g. cache still warming).
        return None

    async def async_select_option(self, option: str) -> None:
        client = self.coordinator.client
        tenant_id = self.coordinator.tenant_id

        if option == PRESET_NONE:
            await run_device_command(
                client.stop_preset(tenant_id, self._device_id), "stop preset"
            )
            await self.coordinator.async_request_refresh()
            return

        preset = self._labels_to_presets().get(option)
        if preset is None:
            _LOGGER.warning("Unknown Kohler preset option selected: %s", option)
            return
        # async_start_preset does the correct two-step (select + mode-0x01 valve
        # write) and raises a clear error for experiences, which can't be
        # started from HA. It requests a refresh itself.
        await self.coordinator.async_start_preset(self._device_id, preset)


class KohlerOutletSelect(KohlerEntity, SelectEntity):
    """Which outlet the shower runs when started from Home Assistant.

    The Kohler API has no "select outlet" state on the device itself; the
    choice is held locally and used when the water heater starts. If water is
    already running, the change is applied live.
    """

    _attr_name = "Outlet"
    _attr_icon = "mdi:shower"
    _attr_options = list(OUTLET_OPTIONS)

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_outlet_select"

    @property
    def current_option(self) -> str:
        outlet = self.coordinator.runtime[self._device_id].outlet
        for label, value in OUTLET_OPTIONS.items():
            if value == outlet:
                return label
        return next(iter(OUTLET_OPTIONS))

    async def async_select_option(self, option: str) -> None:
        self.coordinator.runtime[self._device_id].outlet = OUTLET_OPTIONS[option]
        self.async_write_ha_state()
        await self.coordinator.async_apply_runtime(
            self._device_id, f"switch outlet to {option}"
        )
