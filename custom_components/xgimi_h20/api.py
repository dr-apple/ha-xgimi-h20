"""Async ADB client for the XGIMI H20 (Google TV, MediaTek platform).

Everything here talks to the projector over the Android Debug Bridge using the
``adb-shell`` library (the same library Home Assistant's Android TV
integration ships with), so no companion APK is required on the projector.

Three data sources are used:

* MediaTek PQ HAL (``vendor.mediatek.hardware.pq.IPq/default``) via
  ``service call``: returns the complete live picture-quality JSON
  (picture mode, brightness, contrast, MEMC, AI PQ, noise reduction, ...).
* ``Settings.Global`` keys owned by XGIMI's MiscKey service (light output,
  colour space, colour filter, super frame, ...). MiscKey observes these keys
  and applies changes to the DLP engine immediately and persistently.
* The official picture settings panel (``com.mediatek.tv.settings``). Picture
  values that live in MediaTek's protected settings database can only be
  stored persistently through this panel, so the client can drive it with
  remote-control key events and verifies every step against the PQ HAL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from adb_shell.adb_device_async import AdbDeviceTcpAsync
from adb_shell.auth.keygen import keygen
from adb_shell.auth.sign_pythonrsa import PythonRSASigner

_LOGGER = logging.getLogger(__name__)

PQ_SERVICE = "vendor.mediatek.hardware.pq.IPq/default"
PQ_TX_GET_GLOBAL_NON_AWARE = 54
PQ_TX_SET_PARAMS_BY_GLOBAL = 160
PQ_TX_SET_PARAMS = 159
FOCUS_SERVICE = "xgimi.hardware.gmpf.IProjectorFocusManager/default"

PICTURE_ACTIVITY = (
    "com.mediatek.tv.settings/.displayandsound.picture.PictureActivity"
)

_HEX_WORD = re.compile(r"(?<![0-9a-fA-Fx])[0-9a-fA-F]{8}(?![0-9a-fA-F])")


class XgimiError(Exception):
    """Base error."""


class XgimiConnectionError(XgimiError):
    """Projector not reachable or ADB not authorised."""


class XgimiCommandError(XgimiError):
    """A command was sent but did not have the expected effect."""


def parse_parcel_words(output: str) -> list[int]:
    """Parse the hex words of a ``service call`` Parcel dump."""
    words: list[int] = []
    for line in output.splitlines():
        # Drop the ASCII column ('....') and the offset prefix (0x00000000:).
        line = re.sub(r"'[^']*'", " ", line)
        line = re.sub(r"0x[0-9a-fA-F]+:", " ", line)
        line = line.replace("Result: Parcel(", " ").replace(")", " ")
        words.extend(int(tok, 16) for tok in _HEX_WORD.findall(line))
    return words


def decode_status_return_string(words: list[int]) -> tuple[int, int, str] | None:
    """Decode ``status, int[1] return, String[1]`` NDK-AIDL out parameters."""
    if len(words) < 5 or words[1] != 1 or words[3] != 1:
        return None
    length = words[4]
    chars: list[str] = []
    for word in words[5:]:
        for shift in (0, 16):
            codepoint = (word >> shift) & 0xFFFF
            if len(chars) >= length:
                break
            chars.append(chr(codepoint))
    return words[0], words[2], "".join(chars)


def shell_quote(value: str) -> str:
    """Single-quote a string for the Android shell."""
    return "'" + value.replace("'", "'\\''") + "'"


@dataclass
class XgimiState:
    """Snapshot of everything readable from the projector."""

    available: bool = False
    awake: bool = False
    wakefulness: str | None = None
    pq: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, str | None] = field(default_factory=dict)
    props: dict[str, str] = field(default_factory=dict)
    foreground_package: str | None = None
    updated: float = 0.0


# Settings.Global keys read on every refresh.
GLOBAL_KEYS: tuple[str, ...] = (
    "gm_projector_brightness",
    "gm_color_space",
    "gm_color_enhancing_filter",
    "gm_projector_color_temperature",
    "CFG_SUPER_FRAME",
    "picture_advanced_performance_mode",
    "picture_list_hdr",
    "game_picture_mode",
    "TvSourcesStatus",
    "running_package_name",
    "low_noise_mode",
    "hdmi_auto_switch",
    "no_signal_auto_power_off",
    "power_sleep_timer",
    "tv_life_time",
)

SYSTEM_KEYS: tuple[str, ...] = (
    "autokeystoneend",
    "xgimi_auto_focus_show",
)

PROPS: tuple[str, ...] = (
    "ro.product.model",
    "ro.product.name",
    "ro.build.display.id",
    "ro.build.version.incremental",
    "ro.build.version.release",
    "ro.serialno",
    "persist.sys.locale",
)

_SEP = "@@XG@@"


class XgimiH20:
    """Async ADB client for one projector."""

    def __init__(self, host: str, port: int, adbkey_path: str) -> None:
        self.host = host
        self.port = port
        self._adbkey_path = adbkey_path
        self._device: AdbDeviceTcpAsync | None = None
        self._signer: PythonRSASigner | None = None
        self._lock = asyncio.Lock()
        self._menu_lock = asyncio.Lock()

    # ------------------------------------------------------------------ ADB
    def _load_signer(self) -> PythonRSASigner:
        """Load (or create) the ADB key. Blocking: run in an executor."""
        import os

        if not os.path.isfile(self._adbkey_path):
            keygen(self._adbkey_path)
        with open(self._adbkey_path, encoding="utf-8") as priv:
            private = priv.read()
        public = None
        if os.path.isfile(self._adbkey_path + ".pub"):
            with open(self._adbkey_path + ".pub", encoding="utf-8") as pub:
                public = pub.read()
        return PythonRSASigner(public, private)

    async def async_connect(self) -> None:
        """Open the ADB transport."""
        if self._signer is None:
            loop = asyncio.get_running_loop()
            self._signer = await loop.run_in_executor(None, self._load_signer)
        device = AdbDeviceTcpAsync(self.host, self.port, default_transport_timeout_s=9.0)
        try:
            ok = await device.connect(
                rsa_keys=[self._signer],
                auth_timeout_s=10.0,
                read_timeout_s=10.0,
                transport_timeout_s=9.0,
            )
        except Exception as err:  # noqa: BLE001 - adb_shell raises many types
            await self._safe_close(device)
            raise XgimiConnectionError(f"ADB connect to {self.host} failed: {err}") from err
        if not ok:
            await self._safe_close(device)
            raise XgimiConnectionError(f"ADB connect to {self.host} was refused")
        self._device = device

    async def async_close(self) -> None:
        """Close the ADB transport."""
        device, self._device = self._device, None
        if device is not None:
            await self._safe_close(device)

    @staticmethod
    async def _safe_close(device: AdbDeviceTcpAsync) -> None:
        try:
            await device.close()
        except Exception:  # noqa: BLE001
            pass

    @property
    def connected(self) -> bool:
        return self._device is not None and self._device.available

    async def shell(self, command: str, timeout: float = 15.0) -> str:
        """Run a shell command, reconnecting once if needed."""
        async with self._lock:
            for attempt in (1, 2):
                if not self.connected:
                    await self.async_connect()
                assert self._device is not None
                try:
                    result = await self._device.shell(
                        command,
                        transport_timeout_s=timeout,
                        read_timeout_s=timeout,
                        timeout_s=timeout,
                    )
                    return result if isinstance(result, str) else result.decode()
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("ADB shell failed (attempt %s): %s", attempt, err)
                    await self.async_close()
                    if attempt == 2:
                        raise XgimiConnectionError(str(err)) from err
        raise XgimiConnectionError("unreachable")

    # ------------------------------------------------------------- reading
    async def async_read_pq(self) -> dict[str, Any]:
        """Read the live MediaTek PQ JSON."""
        out = await self.shell(
            f"service call {PQ_SERVICE} {PQ_TX_GET_GLOBAL_NON_AWARE} i32 0 i32 0"
        )
        return self._decode_pq(out)

    @staticmethod
    def _decode_pq(out: str) -> dict[str, Any]:
        decoded = decode_status_return_string(parse_parcel_words(out))
        if decoded is None:
            raise XgimiCommandError(f"Unexpected PQ reply: {out[:200]}")
        status, ret, text = decoded
        if status != 0 or ret != 0:
            raise XgimiCommandError(f"PQ read failed status={status} ret={ret}")
        try:
            return json.loads(text)
        except json.JSONDecodeError as err:
            raise XgimiCommandError(f"PQ JSON invalid: {err}") from err

    async def async_get_state(self) -> XgimiState:
        """Read the complete projector state in a single shell round trip."""
        parts = [
            "dumpsys power 2>/dev/null | grep -m1 mWakefulness=",
            f"service call {PQ_SERVICE} {PQ_TX_GET_GLOBAL_NON_AWARE} i32 0 i32 0",
        ]
        parts += [f"settings get global {key}" for key in GLOBAL_KEYS]
        parts += [f"settings get system {key}" for key in SYSTEM_KEYS]
        parts += [f"getprop {prop}" for prop in PROPS]
        command = f"; echo {_SEP}; ".join(parts)
        out = await self.shell(command, timeout=20.0)
        chunks = [chunk.strip() for chunk in out.split(_SEP)]
        if len(chunks) != len(parts):
            raise XgimiCommandError(f"Unexpected state reply ({len(chunks)} parts)")

        state = XgimiState(available=True, updated=time.time())
        wake = chunks[0].split("=", 1)[-1].strip() if "=" in chunks[0] else None
        state.wakefulness = wake
        state.awake = wake in ("Awake", "Dreaming")
        try:
            state.pq = self._decode_pq(chunks[1])
        except XgimiError as err:
            _LOGGER.debug("PQ read failed: %s", err)
        idx = 2
        for key in GLOBAL_KEYS:
            value = chunks[idx]
            state.settings[key] = None if value in ("", "null") else value
            idx += 1
        for key in SYSTEM_KEYS:
            value = chunks[idx]
            state.settings[key] = None if value in ("", "null") else value
            idx += 1
        for prop in PROPS:
            state.props[prop] = chunks[idx]
            idx += 1
        state.foreground_package = state.settings.get("running_package_name")
        return state

    # ------------------------------------------------------------- writing
    async def async_put_global(self, key: str, value: str | int) -> None:
        """Write a Settings.Global key observed by XGIMI MiscKey."""
        await self.shell(f"settings put global {key} {shell_quote(str(value))}")

    async def async_put_system(self, key: str, value: str | int) -> None:
        await self.shell(f"settings put system {key} {shell_quote(str(value))}")

    async def async_set_pq_live(self, values: dict[str, Any], perstream: bool = False) -> None:
        """Apply PQ values directly in the HAL (instant, not persisted)."""
        payload = json.dumps(values, separators=(",", ":"))
        if perstream:
            args = f"{PQ_TX_SET_PARAMS} i32 0 s16 {shell_quote(payload)}"
        else:
            args = f"{PQ_TX_SET_PARAMS_BY_GLOBAL} s16 {shell_quote(payload)}"
        out = await self.shell(f"service call {PQ_SERVICE} {args}")
        words = parse_parcel_words(out)
        if len(words) < 2 or words[0] != 0:
            raise XgimiCommandError(f"PQ live write failed: {out.strip()[:200]}")

    async def async_keyevent(self, *keys: str) -> None:
        if keys:
            await self.shell("input keyevent " + " ".join(keys))

    async def async_autofocus(self) -> None:
        await self.shell(f"service call {FOCUS_SERVICE} 3 i32 2")

    # --------------------------------------------------- picture menu robot
    async def async_open_picture_menu(self, hotkey: str) -> None:
        """Open the picture settings panel focused on ``hotkey``.

        Supported hotkeys: Picture_Mode, Brightness, Backlight, Gamma,
        Low_Latency, power_picture_off.
        """
        await self.shell(
            f"am start -W -n {PICTURE_ACTIVITY} --activity-clear-task "
            f"--es PictureHotKey {hotkey}"
        )

    async def async_close_picture_menu(self) -> None:
        out = await self.shell("dumpsys window 2>/dev/null | grep -m1 mCurrentFocus")
        if "com.mediatek.tv.settings" in out:
            await self.async_keyevent("BACK")
            await asyncio.sleep(0.3)
            out = await self.shell("dumpsys window 2>/dev/null | grep -m1 mCurrentFocus")
            if "com.mediatek.tv.settings" in out:
                await self.async_keyevent("BACK")

    # ------------------------------------------------------- menu snapshot
    async def async_device_time(self) -> str:
        """Device epoch time (seconds.millis) for logcat -T filters."""
        out = (await self.shell("date +%s")).strip()
        return f"{int(out)}.000" if out.isdigit() else "1"

    async def async_dump_menu(self) -> list[MenuItem]:
        """Dump the currently shown settings panel via uiautomator."""
        out = await self.shell(
            "uiautomator dump /sdcard/xgimi_h20_ui.xml >/dev/null 2>&1; "
            "cat /sdcard/xgimi_h20_ui.xml; rm -f /sdcard/xgimi_h20_ui.xml",
            timeout=20.0,
        )
        start = out.find("<?xml")
        if start < 0:
            raise XgimiCommandError("uiautomator dump failed")
        return parse_menu_dump(out[start:])

    async def async_picture_log(self, since: str, tag: str = "PictureManager") -> list[str]:
        out = await self.shell(f"logcat -d -v epoch -T {since} -s {tag}:D")
        return out.splitlines()


@dataclass
class MenuItem:
    """One row of the picture settings panel."""

    title: str
    kind: str  # picker | switch | seekbar | basic
    value: str | None
    checked: bool | None
    focusable: bool
    focused: bool


def parse_menu_dump(xml_text: str) -> list[MenuItem]:
    """Parse a uiautomator dump of a leanback settings panel."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_text)
    grid = None
    for node in root.iter("node"):
        if node.attrib.get("resource-id", "").endswith("/list"):
            grid = node
            break
    if grid is None:
        return []
    items: list[MenuItem] = []
    for row in grid.findall("node"):
        attrs = row.attrib
        title = None
        kind = "basic"
        value = None
        checked = None
        for child in row.iter("node"):
            rid = child.attrib.get("resource-id", "").split("/")[-1]
            text = child.attrib.get("text", "")
            if rid in ("title", "widget_title") and title is None:
                title = text
            elif rid == "picker_icon_title_container":
                kind = "picker"
            elif rid == "switch_widget":
                kind = "switch"
                checked = child.attrib.get("checked") == "true"
            elif rid == "seekbar_value":
                kind = "seekbar"
                value = text
            elif rid == "" and text and child.attrib.get("class", "").endswith("TextView"):
                if kind == "picker" and value is None:
                    value = text
        if title is None:
            continue
        items.append(
            MenuItem(
                title=title.replace("\xa0", " ").strip(),
                kind=kind,
                value=value,
                checked=checked,
                focusable=attrs.get("focusable") == "true",
                focused=attrs.get("focused") == "true",
            )
        )
    return items


_PM_VALUE = re.compile(
    r"setPreferenceValue\(\)\s+key = (?P<key>[\w.]+), newValue = (?P<value>.*?), pqMode = "
)
_PQMODE = re.compile(
    r"(?:pqMode = |getPQMode\(\) called = |pqModeStateFlow\(\) called = |"
    r"PqModeManager changed, value = )(?P<mode>[A-Za-z][\w ]*?)(?:[,，]|\s*$)"
)


def parse_log_line(line: str) -> tuple[str, str] | None:
    """Return ``(key, value)`` for a picture-relevant log line.

    ``key`` is a settings-panel preference key, or ``picture_mode``.
    """
    match = _PM_VALUE.search(line)
    if match:
        value = match.group("value").strip()
        if value.startswith("Pair{"):
            return None
        return match.group("key"), value
    match = _PQMODE.search(line)
    if match and match.group("mode") != "null":
        return "picture_mode", match.group("mode").strip()
    return None


class XgimiLogStream:
    """Follow PictureManager / PqModeManager logcat output on a 2nd ADB link."""

    COMMAND = "logcat -v epoch -T 1 -s PictureManager:D PqModeManager:D GtvProjectorMapper:D"

    def __init__(self, host: str, port: int, signer_source: XgimiH20) -> None:
        self._host = host
        self._port = port
        self._source = signer_source
        self._device: AdbDeviceTcpAsync | None = None

    async def run(self, callback) -> None:
        """Stream until the connection drops; ``callback(key, value)`` per event."""
        if self._source._signer is None:  # noqa: SLF001
            loop = asyncio.get_running_loop()
            self._source._signer = await loop.run_in_executor(None, self._source._load_signer)  # noqa: SLF001
        # No transport timeout: logcat may stay silent for hours.
        device = AdbDeviceTcpAsync(self._host, self._port, default_transport_timeout_s=None)
        self._device = device
        try:
            ok = await device.connect(
                rsa_keys=[self._source._signer],  # noqa: SLF001
                transport_timeout_s=9.0,
                auth_timeout_s=10.0,
            )
            if not ok:
                raise XgimiConnectionError("log stream connect refused")
            buffer = ""
            async for chunk in device.streaming_shell(
                self.COMMAND, transport_timeout_s=None, read_timeout_s=10**8
            ):
                buffer += chunk if isinstance(chunk, str) else chunk.decode(errors="ignore")
                *lines, buffer = buffer.split("\n")
                for line in lines:
                    parsed = parse_log_line(line)
                    if parsed:
                        callback(*parsed)
        finally:
            self._device = None
            await XgimiH20._safe_close(device)  # noqa: SLF001

    async def stop(self) -> None:
        if self._device is not None:
            await XgimiH20._safe_close(self._device)  # noqa: SLF001
