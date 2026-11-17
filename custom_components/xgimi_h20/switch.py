"""Switch entities (AI PQ, super frame, dynamic black, ...)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import XgimiConfigEntry
from .const import SETTINGS
from .entity import XgimiEntity, XgimiSettingEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: XgimiConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = [
        XgimiSwitch(coordinator, setting) for setting in SETTINGS if setting.kind == "switch"
    ]
    if coordinator.picture_ai is not None:
        entities.append(PictureAISwitch(coordinator))
    async_add_entities(entities)


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


class PictureAISwitch(XgimiEntity, SwitchEntity):
    """Turns the AI picture optimisation on or off."""

    _attr_translation_key = "picture_ai"
    _attr_icon = "mdi:creation"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "picture_ai")
        self.ai = coordinator.picture_ai

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.ai.add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None

    @property
    def is_on(self) -> bool:
        return self.ai.enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"running": self.ai.running, "profiler": self.ai.profiler, "ai_task": self.ai.ai_task}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.ai.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.ai.async_set_enabled(False)
