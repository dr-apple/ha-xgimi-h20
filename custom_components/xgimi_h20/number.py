"""Number entities (light output, brightness, contrast, ...)."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
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
        XgimiNumber(coordinator, setting) for setting in SETTINGS if setting.kind == "seekbar"
    )


class XgimiNumber(XgimiSettingEntity, NumberEntity):
    """A slider of the picture settings panel."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1

    def __init__(self, coordinator, setting) -> None:
        super().__init__(coordinator, setting)
        self._attr_native_min_value = setting.min_value
        self._attr_native_max_value = setting.max_value

    @property
    def native_value(self) -> float | None:
        value = self.raw_value
        return None if value is None else float(value)

    async def async_set_native_value(self, value: float) -> None:
        await self._async_write(int(round(value)))
