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
    GM_PICTURE_MODES,
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
    profile: str | None = None
    disabled: frozenset[str] = frozenset()
    app_labels: dict[str, str] = field(default_factory=dict)
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
        # Picture values are stored per firmware profile:
        #   modes[signal] -> picture mode, profiles[signal|mode] -> {key: value}
        # where signal = "<source>|<hdr type>", e.g. "hdmi1|DOLBYVISION".
        self._modes: dict[str, str] = {}
        self._profiles: dict[str, dict[str, Any]] = {}
        self._globals: dict[str, Any] = {}
        self._disabled: dict[str, list[str]] = {}
        self._labels: dict[str, str] = {}
        self._label_tried: set[str] = set()
        self._auto_tried: set[str] = set()
        self._pending_signal: str | None = None
        self._native_signal: str | None = None
        self._native_base: tuple | None = None
        self._native_trusted = False
        self._sources: dict[str, str] = {}
        self._state = XgimiState()
        self._last_result: str | None = None
        self._busy = False
        self._write_lock = asyncio.Lock()
        self._stream_task: asyncio.Task | None = None
        self._stream: XgimiLogStream | None = None

    # -------------------------------------------------------------- setup
    @property
    def write_mode(self) -> str:
        return self.config_entry.options.get(CONF_WRITE_MODE, DEFAULT_WRITE_MODE)

    async def async_setup(self) -> None:
        stored = await self._store.async_load() or {}
        self._modes = dict(stored.get("modes", {}))
        self._profiles = {k: dict(v) for k, v in stored.get("profiles", {}).items()}
        self._disabled = {k: list(v) for k, v in stored.get("disabled", {}).items()}
        self._labels = dict(stored.get("labels", {}))
        if "values" in stored and not self._profiles:
            # v1.0.x stored one flat dict (read in Google TV / SDR).
            old = dict(stored["values"])
            mode = old.pop("picture_mode", None)
            if mode:
                self._modes["mm|SDR"] = mode
                self._profiles[f"mm|SDR|{mode}"] = {
                    k: v for k, v in old.items() if not SETTINGS_BY_KEY.get(k) or not SETTINGS_BY_KEY[k].global_key
                }
        i18n = await self.hass.async_add_executor_job(load_i18n)
        self.robot = MenuRobot(self.client, i18n)

    def start_log_stream(self) -> None:
        if self._stream_task is None:
            self._stream_task = self.config_entry.async_create_background_task(
                self.hass, self._stream_loop(), f"{DOMAIN}_logstream"
            )

    async def async_shutdown(self) -> None:
        # Cancel the stream task first: its blocking read holds the ADB
        # stream lock, so closing the link before would deadlock.
        task, self._stream_task = self._stream_task, None
        if task is not None:
            task.cancel()
            await asyncio.wait({task}, timeout=5)
        if self._stream is not None:
            await self._stream.stop()
        try:
            await asyncio.wait_for(self.client.async_close(), 5)
        except TimeoutError:
            pass
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
    def _on_log_event(self, key: str, value: str, meta: dict[str, str] | None = None) -> None:
        setting = SETTINGS_BY_KEY.get(key)
        if setting is None:
            return
        normalized = self._normalize(setting, value)
        if normalized is None:
            return
        if key == "picture_mode":
            # Mode changes race with signal changes: only accept lines that
            # name the HDR type themselves, and only plausible combinations.
            meta = meta or {}
            hdr = meta.get("hdr")
            if not hdr:
                if not meta.get("menu"):
                    return
                hdr = self._signal().partition("|")[2]
            dolby_signal = hdr in ("DOLBYVISION", "DV")
            if str(normalized).startswith("DolbyVision") != dolby_signal:
                return
        signal = self._signal(meta)
        if self._current_value(key, signal) == normalized:
            return
        _LOGGER.debug("Log event %s = %s (%s)", key, normalized, signal)
        self._set_value(key, normalized, "device", signal)
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

    def _signal(self, meta: dict[str, str] | None = None) -> str:
        """Current "<source>|<hdr>" signal key, refined by log metadata."""
        raw = self._state.settings.get("TvSourcesStatus") or "mm|SDR"
        source, _, hdr = raw.partition("|")
        if meta:
            source = meta.get("source", source)
            hdr = meta.get("hdr", hdr)
        return f"{source.lower()}|{(hdr or 'SDR').upper()}"

    def _profile_key(self, signal: str | None = None) -> str | None:
        signal = signal or self._signal()
        mode = self._modes.get(signal)
        return f"{signal}|{mode}" if mode else None

    @staticmethod
    def _is_live_key(setting: PictureSetting | None) -> bool:
        return bool(setting and (setting.global_key or setting.read_key))

    def _current_value(self, key: str, signal: str | None = None) -> Any:
        setting = SETTINGS_BY_KEY.get(key)
        if self._is_live_key(setting):
            return self._globals.get(key)
        signal = signal or self._signal()
        if key == "picture_mode":
            return self._modes.get(signal)
        profile = self._profile_key(signal)
        return self._profiles.get(profile, {}).get(key) if profile else None

    def _set_value(self, key: str, value: Any, source: str, signal: str | None = None) -> None:
        setting = SETTINGS_BY_KEY.get(key)
        if self._is_live_key(setting):
            self._globals[key] = value
        else:
            signal = signal or self._signal()
            if key == "picture_mode":
                self._modes[signal] = value
            else:
                profile = self._profile_key(signal)
                if profile is None:
                    return
                self._profiles.setdefault(profile, {})[key] = value
        self._sources[key] = source
        self._store.async_delay_save(
            self._store_payload,
            5,
        )

    def _store_payload(self) -> dict[str, Any]:
        return {
            "modes": dict(self._modes),
            "profiles": {k: dict(v) for k, v in self._profiles.items()},
            "disabled": {k: list(v) for k, v in self._disabled.items()},
            "labels": dict(self._labels),
        }

    def _data(self) -> XgimiData:
        signal = self._signal()
        profile = self._profile_key(signal)
        values: dict[str, Any] = dict(self._profiles.get(profile, {})) if profile else {}
        if signal in self._modes:
            values["picture_mode"] = self._modes[signal]
        values.update(self._globals)
        return XgimiData(
            state=self._state,
            values=values,
            sources={k: v for k, v in self._sources.items() if k in values},
            last_result=self._last_result,
            busy=self._busy,
            profile=profile,
            disabled=frozenset(self._disabled.get(profile, ())) if profile else frozenset(),
            app_labels=dict(self._labels),
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
                if self._globals.get(setting.key) != value:
                    self._set_value(setting.key, value, "device")
        self._maybe_fetch_label(state)
        self._sync_read_keys(state)
        self._sync_native(state)
        self._maybe_auto_read(state)
        return self._data()

    def _maybe_fetch_label(self, state: XgimiState) -> None:
        package = state.foreground_package
        if not package or package in self._labels or package in self._label_tried:
            return
        self._label_tried.add(package)
        locale = state.props.get("persist.sys.locale")
        self.config_entry.async_create_background_task(
            self.hass, self._fetch_label(package, locale), f"{DOMAIN}_app_label"
        )

    async def _fetch_label(self, package: str, locale: str | None) -> None:
        try:
            label = await self.client.async_app_label(package, (locale or "en")[:2])
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("App label for %s failed: %s", package, err)
            return
        if label:
            self._labels[package] = label
            self._store.async_delay_save(self._store_payload, 5)
            self.async_set_updated_data(self._data())

    def _sync_read_keys(self, state: XgimiState) -> None:
        """Values XGIMI keeps in Settings.Global but only the menu writes."""
        for setting in SETTINGS:
            if not setting.read_key:
                continue
            raw = state.settings.get(setting.read_key)
            if raw is None:
                continue
            value = raw.split(":", 1)[0].strip().lower() in ("1", "true", "on")
            if self._globals.get(setting.key) != value:
                self._set_value(setting.key, value, "device")

    def _sync_native(self, state: XgimiState) -> None:
        """Take picture values from the PQ pipeline while a video is shown.

        Without an active video stream the HAL reports a stale default
        profile, so the values are only trusted for HDMI inputs and for apps
        with an active media session.
        """
        signal = self._signal()
        hdr = signal.partition("|")[2]
        is_dolby = hdr in ("DOLBYVISION", "DV")
        gm_mode = GM_PICTURE_MODES.get(state.gm_picture_mode) if state.gm_picture_mode is not None else None
        stored_mode = self._modes.get(signal)
        if gm_mode and not is_dolby and (stored_mode is None or stored_mode in GM_PICTURE_MODES.values()):
            # The XGIMI HAL only tracks Vivid/Movie/Game/Standard/Sport; never
            # overwrite a FilmMaker/ISF mode learned from the log stream.
            if stored_mode != gm_mode:
                self._set_value("picture_mode", gm_mode, "device", signal)
        video = signal.startswith("hdmi") or state.playing
        if not video or not state.pq or hdr != "SDR":
            # Only SDR matches the picture menu 1:1. Dolby Vision runs through
            # its own pipeline (the status keeps the previous SDR values) and
            # HDR10 reports another stream's per-stream values (contrast, NR).
            return
        fingerprint = tuple(
            str(state.pq.get(s.native)) for s in SETTINGS if s.native and s.native_read
        )
        if signal != self._native_signal:
            # After a signal change only trust the pipeline once it has
            # actually re-applied values (a frozen status stays identical).
            first = self._native_signal is None
            self._native_signal = signal
            self._native_base = fingerprint
            self._native_trusted = first
        elif not self._native_trusted and fingerprint != self._native_base:
            self._native_trusted = True
        if not self._native_trusted:
            return
        profile = self._profile_key(signal)
        if profile is None:
            return
        current = self._profiles.get(profile, {})
        for setting in SETTINGS:
            if not setting.native or not setting.native_read or self._is_live_key(setting):
                continue
            raw = state.pq.get(setting.native)
            if raw is None:
                continue
            inverse = {str(v): k for k, v in setting.native_map.items()}
            value = self._normalize(setting, inverse.get(str(raw), raw))
            if setting.kind == "picker" and setting.options and value not in setting.options:
                continue
            if current.get(setting.key) != value:
                self._set_value(setting.key, value, "native", signal)

    def _maybe_auto_read(self, state: XgimiState) -> None:
        """Read the picture menu once for every new, stable picture profile."""
        if not self.config_entry.options.get(CONF_SNAPSHOT_ON_START, False):
            return
        if state.wakefulness != "Awake" or self._busy:
            self._pending_signal = None
            return
        signal = self._signal()
        profile = self._profile_key(signal)
        token = profile or signal
        known = profile is not None and bool(self._profiles.get(profile))
        if known or token in self._auto_tried:
            self._pending_signal = None
            return
        if self._pending_signal != token:
            # Wait one more poll so a short-lived signal (menus, intros) is ignored.
            self._pending_signal = token
            return
        self._auto_tried.add(token)
        self._pending_signal = None
        _LOGGER.debug("Reading picture menu for new profile %s", signal)
        self.config_entry.async_create_background_task(
            self.hass, self._auto_read(), f"{DOMAIN}_auto_read"
        )

    async def _auto_read(self) -> None:
        try:
            await self.async_read_menu()
        except HomeAssistantError as err:
            _LOGGER.debug("Automatic picture menu read failed: %s", err)

    # ------------------------------------------------------------- writes
    async def async_set_values(self, changes: dict[str, Any], mode: str | None = None) -> None:
        """Change one or more picture settings."""
        mode = mode or self.write_mode
        menu_changes: list[tuple[str, Any, str, list[str] | None]] = []
        live: dict[str, Any] = {}
        live_perstream: dict[str, Any] = {}
        global_writes: list[tuple[PictureSetting, Any]] = []

        current_disabled = self._data().disabled
        for key, raw in changes.items():
            setting = SETTINGS_BY_KEY.get(key)
            if setting is None:
                raise HomeAssistantError(f"Unknown setting {key}")
            if key in current_disabled:
                raise HomeAssistantError(
                    f"{key} is locked by the projector in the current picture mode / signal"
                )
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
                    results.sort(key=lambda r: r.key != "picture_mode")
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
            ordered = sorted(values.items(), key=lambda kv: kv[0] != "picture_mode")
            for key, value in ordered:
                setting = SETTINGS_BY_KEY.get(key)
                if setting is not None and value is not None:
                    self._set_value(key, self._normalize(setting, value), "menu")
            profile = self._profile_key()
            if profile is not None:
                self._disabled[profile] = sorted(self.robot.last_disabled)
                self._store.async_delay_save(self._store_payload, 5)
            self._last_result = "ok"
            self.async_set_updated_data(self._data())
            return values
