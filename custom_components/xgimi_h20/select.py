"""Select entities (picture mode, MEMC, gamma, noise reduction, ...)."""

from __future__ import annotations

import re

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import XgimiConfigEntry
from .const import COLOR_SPACE_NAMES, SETTINGS, PictureSetting
from .entity import XgimiSettingEntity


def to_option(setting: PictureSetting, value: str) -> str:
    if setting.key == "picture_projector_color_space":
        return COLOR_SPACE_NAMES.get(value, value)
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


async def async_setup_entry(
    hass: HomeAssistant, entry: XgimiConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        XgimiSelect(coordinator, setting) for setting in SETTINGS if setting.kind == "picker"
    )


class XgimiSelect(XgimiSettingEntity, SelectEntity):
    """A picker of the picture settings panel."""

    def __init__(self, coordinator, setting: PictureSetting) -> None:
        super().__init__(coordinator, setting)
        self._to_canonical = {to_option(setting, opt): opt for opt in setting.options}
        self._attr_options = list(self._to_canonical)

    @property
    def current_option(self) -> str | None:
        value = self.raw_value
        if value is None:
            return None
        option = to_option(self.setting, str(value))
        return option if option in self._to_canonical else None

    @property
    def extra_state_attributes(self):
        attrs = super().extra_state_attributes
        attrs["raw_value"] = self.raw_value
        return attrs

    async def async_select_option(self, option: str) -> None:
        await self._async_write(self._to_canonical[option])
