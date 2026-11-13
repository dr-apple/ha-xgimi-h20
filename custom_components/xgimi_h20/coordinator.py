"""Coordinator: polling, live log stream, persistence and write routing."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import (
    XgimiCommandError,
    XgimiConnectionError,
    XgimiError,
    XgimiH20,
    XgimiLogStream,
    XgimiState,
)
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_SNAPSHOT_ON_START,
    CONF_WRITE_MODE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WRITE_MODE,
    DOMAIN,
    PQ_PERSTREAM_KEYS,
    SETTINGS,
    SETTINGS_BY_KEY,
    WRITE_MODE_LIVE,
    PictureSetting,
)
from .menu import MenuRobot, load_i18n

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
STREAM_MAX_AGE = 900


@dataclass
class XgimiData:
    """Data shared with the entities."""

    state: XgimiState = field(default_factory=XgimiState)
    values: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    last_result: str | None = None
    busy: bool = False


class XgimiCoordinator(DataUpdateCoordinator[XgimiData]):
    """Keeps one projector in sync."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: XgimiH20) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {client.host}",
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.robot: MenuRobot | None = None
        self._store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._values: dict[str, Any] = {}
        self._sources: dict[str, str] = {}
        self._state = XgimiState()
        self._last_result: str | None = None
        self._busy = False
        self._write_lock = asyncio.Lock()
        self._stream_task: asyncio.Task | None = None
        self._stream: XgimiLogStream | None = None
        self._snapshot_done = False

    # -------------------------------------------------------------- setup
    @property
    def write_mode(self) -> str:
        return self.config_entry.options.get(CONF_WRITE_MODE, DEFAULT_WRITE_MODE)

    async def async_setup(self) -> None:
        stored = await self._store.async_load() or {}
        self._values = dict(stored.get("values", {}))
        self._sources = {key: "stored" for key in self._values}
        i18n = await self.hass.async_add_executor_job(load_i18n)
        self.robot = MenuRobot(self.client, i18n)

    def start_log_stream(self) -> None:
        if self._stream_task is None:
            self._stream_task = self.config_entry.async_create_background_task(
                self.hass, self._stream_loop(), f"{DOMAIN}_logstream"
            )

    async def async_shutdown(self) -> None:
        if self._stream is not None:
            await self._stream.stop()
        if self._stream_task is not None:
            self._stream_task.cancel()
            self._stream_task = None
        await self.client.async_close()
        await super().async_shutdown()

    async def _stream_loop(self) -> None:
        delay = 5
        while True:
            self._stream = XgimiLogStream(self.client.host, self.client.port, self.client)
            try:
                # Re-establish periodically so a silently dead TCP link heals.
                async with asyncio.timeout(STREAM_MAX_AGE):
                    await self._stream.run(self._on_log_event)
                delay = 5
            except TimeoutError:
                delay = 1
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Log stream ended: %s", err)
                delay = min(delay * 2, 120)
            await asyncio.sleep(delay)

    @callback
    def _on_log_event(self, key: str, value: str) -> None:
        setting = SETTINGS_BY_KEY.get(key)
        if setting is None:
            return
        normalized = self._normalize(setting, value)
        if normalized is None or self._values.get(key) == normalized:
            return
        _LOGGER.debug("Log event %s = %s", key, normalized)
        self._set_value(key, normalized, "device")
        self.async_set_updated_data(self._data())

    # ------------------------------------------------------------- values
    @staticmethod
    def _normalize(setting: PictureSetting, value: Any) -> Any:
        if value is None:
            return None
        if setting.kind == "switch":
            if isinstance(value, bool):
                return value
            return str(value).lower() in ("true", "on", "1")
        if setting.kind == "seekbar":
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return None
        value = str(value)
        value = setting.aliases.get(value, value)
        return value

    def _set_value(self, key: str, value: Any, source: str) -> None:
        self._values[key] = value
        self._sources[key] = source
        self._store.async_delay_save(lambda: {"values": dict(self._values)}, 5)

    def _data(self) -> XgimiData:
        return XgimiData(
            state=self._state,
            values=dict(self._values),
            sources=dict(self._sources),
            last_result=self._last_result,
            busy=self._busy,
        )

    # ------------------------------------------------------------ polling
    async def _async_update_data(self) -> XgimiData:
        try:
            state = await self.client.async_get_state()
        except XgimiError as err:
            if self._state.available:
                _LOGGER.info("XGIMI H20 %s not reachable: %s", self.client.host, err)
            self._state = XgimiState(available=False, props=self._state.props)
            return self._data()
        if not self._state.available and self._stream_task is not None:
            # Projector (re)appeared: restart the log stream right away.
            self._stream_task.cancel()
            self._stream_task = None
            self.start_log_stream()
        self._state = state
        if self.robot is not None:
            self.robot.set_locale(state.props.get("persist.sys.locale"))
        for setting in SETTINGS:
            if setting.global_key and state.settings.get(setting.global_key) is not None:
                value = self._normalize(setting, state.settings[setting.global_key])
                if self._values.get(setting.key) != value:
                    self._set_value(setting.key, value, "device")
        if (
            not self._snapshot_done
            and state.awake
            and self.config_entry.options.get(CONF_SNAPSHOT_ON_START, False)
        ):
            self._snapshot_done = True
            self.hass.async_create_task(self.async_read_menu())
        return self._data()

    # ------------------------------------------------------------- writes
    async def async_set_values(self, changes: dict[str, Any], mode: str | None = None) -> None:
        """Change one or more picture settings."""
        mode = mode or self.write_mode
        menu_changes: list[tuple[str, Any, str, list[str] | None]] = []
        live: dict[str, Any] = {}
        live_perstream: dict[str, Any] = {}
        global_writes: list[tuple[PictureSetting, Any]] = []

        for key, raw in changes.items():
            setting = SETTINGS_BY_KEY.get(key)
            if setting is None:
                raise HomeAssistantError(f"Unknown setting {key}")
            value = self._normalize(setting, raw)
            if setting.kind == "picker" and setting.options and value not in setting.options:
                raise HomeAssistantError(f"{value} is not valid for {key}")
            if setting.kind == "seekbar" and not setting.min_value <= value <= setting.max_value:
                raise HomeAssistantError(f"{value} out of range for {key}")
            if setting.global_key:
                global_writes.append((setting, value))
            elif mode == WRITE_MODE_LIVE and setting.native:
                native_value = setting.native_map.get(str(value), value)
                target = live_perstream if setting.native in PQ_PERSTREAM_KEYS else live
                target[setting.native] = native_value
            else:
                menu_changes.append(
                    (key, value, setting.kind, list(setting.options) or None)
                )

        async with self._write_lock:
            self._busy = True
            self.async_set_updated_data(self._data())
            errors: list[str] = []
            try:
                for setting, value in global_writes:
                    await self.client.async_put_global(setting.global_key, value)
                    self._set_value(setting.key, value, "set")
                if live:
                    await self.client.async_set_pq_live(live)
                if live_perstream:
                    await self.client.async_set_pq_live(live_perstream, perstream=True)
                for key, raw in changes.items():
                    setting = SETTINGS_BY_KEY[key]
                    if setting.native and (setting.native in live or setting.native in live_perstream):
                        self._set_value(key, self._normalize(setting, raw), "live")
                if menu_changes:
                    assert self.robot is not None
                    results = await self.robot.async_apply(menu_changes)
                    for result in results:
                        if result.value is not None:
                            setting = SETTINGS_BY_KEY[result.key]
                            self._set_value(result.key, self._normalize(setting, result.value), "menu")
                        if not result.ok:
                            errors.append(f"{result.key}: {result.message or 'not applied'}")
            except XgimiConnectionError as err:
                errors.append(f"Projector not reachable: {err}")
            except XgimiCommandError as err:
                errors.append(str(err))
            finally:
                self._busy = False
                self._last_result = "; ".join(errors) if errors else "ok"
                self.async_set_updated_data(self._data())
        if errors:
            raise HomeAssistantError("; ".join(errors))

    async def async_read_menu(self) -> dict[str, Any]:
        """Read all picture settings through the settings panel."""
        assert self.robot is not None
        async with self._write_lock:
            self._busy = True
            self.async_set_updated_data(self._data())
            try:
                pickers = {s.key: list(s.options) for s in SETTINGS if s.kind == "picker"}
                values = await self.robot.async_snapshot(pickers)
            except XgimiError as err:
                raise HomeAssistantError(str(err)) from err
            finally:
                self._busy = False
            for key, value in values.items():
                setting = SETTINGS_BY_KEY.get(key)
                if setting is not None and value is not None:
                    self._set_value(key, self._normalize(setting, value), "menu")
            self._last_result = "ok"
            self.async_set_updated_data(self._data())
            return values
