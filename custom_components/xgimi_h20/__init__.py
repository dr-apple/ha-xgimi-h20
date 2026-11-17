"""XGIMI H20 projector integration (ADB, no companion app)."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .api import XgimiH20
from .const import (
    ATTR_DRY_RUN,
    ATTR_KEY,
    ATTR_KEYS,
    ATTR_REPLACE,
    ATTR_TITLES,
    CONF_AI_PROFILER,
    ATTR_MODE,
    ATTR_SETTINGS,
    CONF_ADBKEY,
    DOMAIN,
    SERVICE_PICTURE_AI_APPLY,
    SERVICE_PICTURE_AI_FORGET,
    SERVICE_PICTURE_AI_IMPORT,
    SERVICE_READ_PICTURE_MENU,
    SERVICE_SEND_KEYS,
    SERVICE_SET_SETTINGS,
    WRITE_MODES,
)
from .coordinator import XgimiCoordinator
from .picture_ai import PictureAI

PLATFORMS = [Platform.BUTTON, Platform.NUMBER, Platform.SELECT, Platform.SENSOR, Platform.SWITCH]

type XgimiConfigEntry = ConfigEntry[XgimiCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ATTR_DEVICE_ID = "device_id"

SET_SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_SETTINGS): dict,
        vol.Optional(ATTR_MODE): vol.In(WRITE_MODES),
    }
)
DEVICE_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE_ID): cv.string})
AI_APPLY_SCHEMA = vol.Schema(
    {vol.Required(ATTR_DEVICE_ID): cv.string, vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean}
)
AI_IMPORT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_TITLES): dict,
        vol.Optional(ATTR_REPLACE, default=False): cv.boolean,
    }
)
AI_FORGET_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE_ID): cv.string, vol.Required(ATTR_KEY): cv.string})
SEND_KEYS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_KEYS): vol.All(cv.ensure_list, [cv.string]),
    }
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the integration services."""

    def coordinator_for(call: ServiceCall) -> XgimiCoordinator:
        device = dr.async_get(hass).async_get(call.data[ATTR_DEVICE_ID])
        if device is None:
            raise ServiceValidationError("Unknown device")
        for entry_id in device.config_entries:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry and entry.domain == DOMAIN and hasattr(entry, "runtime_data"):
                return entry.runtime_data
        raise ServiceValidationError("Device is not an XGIMI H20")

    async def set_settings(call: ServiceCall) -> None:
        await coordinator_for(call).async_set_values(
            call.data[ATTR_SETTINGS], call.data.get(ATTR_MODE)
        )

    async def read_picture_menu(call: ServiceCall) -> ServiceResponse:
        values = await coordinator_for(call).async_read_menu()
        return {"values": values}

    async def send_keys(call: ServiceCall) -> None:
        keys = [key.upper() for key in call.data[ATTR_KEYS]]
        if not all(key.replace("_", "").isalnum() for key in keys):
            raise ServiceValidationError("Invalid key name")
        try:
            await coordinator_for(call).client.async_keyevent(*keys)
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(str(err)) from err

    hass.services.async_register(DOMAIN, SERVICE_SET_SETTINGS, set_settings, SET_SETTINGS_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_READ_PICTURE_MENU,
        read_picture_menu,
        DEVICE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(DOMAIN, SERVICE_SEND_KEYS, send_keys, SEND_KEYS_SCHEMA)

    def picture_ai_for(call: ServiceCall) -> PictureAI:
        ai = coordinator_for(call).picture_ai
        if ai is None:
            raise ServiceValidationError(
                "AI picture optimisation is not set up (options: Entertainment Profiler sensor)"
            )
        return ai

    async def picture_ai_apply(call: ServiceCall) -> ServiceResponse:
        return await picture_ai_for(call).async_apply(dry_run=call.data[ATTR_DRY_RUN])

    async def picture_ai_import(call: ServiceCall) -> ServiceResponse:
        count = picture_ai_for(call).import_titles(call.data[ATTR_TITLES], call.data[ATTR_REPLACE])
        return {"imported": count}

    async def picture_ai_forget(call: ServiceCall) -> ServiceResponse:
        return {"removed": picture_ai_for(call).forget(call.data[ATTR_KEY])}

    hass.services.async_register(
        DOMAIN, SERVICE_PICTURE_AI_APPLY, picture_ai_apply, AI_APPLY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PICTURE_AI_IMPORT, picture_ai_import, AI_IMPORT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PICTURE_AI_FORGET, picture_ai_forget, AI_FORGET_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: XgimiConfigEntry) -> bool:
    """Set up one projector."""
    client = XgimiH20(
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        hass.config.path(entry.data[CONF_ADBKEY]),
    )
    coordinator = XgimiCoordinator(hass, entry, client)
    await coordinator.async_setup()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    coordinator.start_log_stream()
    if entry.options.get(CONF_AI_PROFILER):
        coordinator.picture_ai = PictureAI(hass, entry, coordinator)
        await coordinator.picture_ai.async_start()
        entry.async_on_unload(coordinator.picture_ai.async_stop)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: XgimiConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: XgimiConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
