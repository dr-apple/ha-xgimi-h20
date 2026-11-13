"""Switch entities (AI PQ, super frame, dynamic black, ...)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import XgimiConfigEntry
from .const import SETTINGS
from .entity import XgimiSettingEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: XgimiConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        XgimiSwitch(coordinator, setting) for setting in SETTINGS if setting.kind == "switch"
    )


class XgimiSwitch(XgimiSettingEntity, SwitchEntity):
    """A toggle of the picture settings panel."""

    @property
    def is_on(self) -> bool | None:
        value = self.raw_value
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_write(False)
