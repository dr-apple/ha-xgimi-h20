"""Config and options flow for the XGIMI H20."""

from __future__ import annotations

import os
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import XgimiError, XgimiH20
from .const import (
    ANDROIDTV_ADBKEY,
    CONF_ADBKEY,
    CONF_AI_NOTIFY,
    CONF_AI_PROFILER,
    CONF_AI_TASK,
    CONF_SCAN_INTERVAL,
    CONF_SNAPSHOT_ON_START,
    CONF_WRITE_MODE,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WRITE_MODE,
    DOMAIN,
    OWN_ADBKEY,
    PROFILER_DOMAIN,
    WRITE_MODES,
)

H20_MODELS = ("XR13A",)


class XgimiH20ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None

    def _default_key(self) -> str:
        if os.path.isfile(self.hass.config.path(ANDROIDTV_ADBKEY)):
            return ANDROIDTV_ADBKEY
        return OWN_ADBKEY

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            key = user_input[CONF_ADBKEY].strip()
            client = XgimiH20(host, port, self.hass.config.path(key))
            try:
                state = await client.async_get_state()
            except XgimiError:
                errors["base"] = "cannot_connect"
            else:
                serial = state.props.get("ro.serialno") or host
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                model = state.props.get("ro.product.model", "")
                return self.async_create_entry(
                    title=f"XGIMI H20 ({model})" if model else "XGIMI H20",
                    data={CONF_HOST: host, CONF_PORT: port, CONF_ADBKEY: key},
                )
            finally:
                await client.async_close()

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=self._host or ""): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Required(CONF_ADBKEY, default=self._default_key()): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        name = discovery_info.name.split(".")[0]
        if name not in H20_MODELS:
            return self.async_abort(reason="not_supported")
        self._host = str(discovery_info.ip_address)
        self._async_abort_entries_match({CONF_HOST: self._host})
        self.context["title_placeholders"] = {"name": "XGIMI H20"}
        return await self.async_step_user()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return XgimiH20OptionsFlow()


class XgimiH20OptionsFlow(OptionsFlow):
    """Options: write mode, polling interval, menu snapshot, AI picture."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_SCAN_INTERVAL] = int(user_input[CONF_SCAN_INTERVAL])
            if user_input.get(CONF_AI_PROFILER) and PROFILER_DOMAIN not in self.hass.config.components:
                errors[CONF_AI_PROFILER] = "profiler_missing"
            else:
                return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_WRITE_MODE, default=options.get(CONF_WRITE_MODE, DEFAULT_WRITE_MODE)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=WRITE_MODES,
                        mode=SelectSelectorMode.LIST,
                        translation_key=CONF_WRITE_MODE,
                    )
                ),
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(min=10, max=600, step=5, unit_of_measurement="s", mode=NumberSelectorMode.BOX)
                ),
                vol.Required(
                    CONF_SNAPSHOT_ON_START, default=options.get(CONF_SNAPSHOT_ON_START, False)
                ): BooleanSelector(),
                vol.Optional(
                    CONF_AI_PROFILER, description={"suggested_value": options.get(CONF_AI_PROFILER)}
                ): EntitySelector(EntitySelectorConfig(domain="sensor", integration=PROFILER_DOMAIN)),
                vol.Optional(
                    CONF_AI_TASK, description={"suggested_value": options.get(CONF_AI_TASK)}
                ): EntitySelector(EntitySelectorConfig(domain="ai_task")),
                vol.Optional(
                    CONF_AI_NOTIFY, description={"suggested_value": options.get(CONF_AI_NOTIFY)}
                ): EntitySelector(EntitySelectorConfig(domain="notify")),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
