"""Buttons: autofocus, read picture menu, open picture menu."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import XgimiConfigEntry
from .api import XgimiError
from .coordinator import XgimiCoordinator
from .entity import XgimiEntity


@dataclass(frozen=True, kw_only=True)
class XgimiButtonDescription(ButtonEntityDescription):
    press: Callable[[XgimiCoordinator], Awaitable]


BUTTONS = (
    XgimiButtonDescription(
        key="autofocus", translation_key="autofocus", icon="mdi:image-filter-center-focus-strong",
        press=lambda c: c.client.async_autofocus(),
    ),
    XgimiButtonDescription(
        key="read_picture_menu", translation_key="read_picture_menu", icon="mdi:sync",
        entity_category=EntityCategory.DIAGNOSTIC,
        press=lambda c: c.async_read_menu(),
    ),
    XgimiButtonDescription(
        key="open_picture_menu", translation_key="open_picture_menu", icon="mdi:tune",
        press=lambda c: c.client.async_open_picture_menu("Picture_Mode"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: XgimiConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(XgimiButton(coordinator, description) for description in BUTTONS)


class XgimiButton(XgimiEntity, ButtonEntity):
    entity_description: XgimiButtonDescription

    def __init__(self, coordinator, description: XgimiButtonDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        try:
            await self.entity_description.press(self.coordinator)
        except XgimiError as err:
            raise HomeAssistantError(str(err)) from err
