"""Drive the official picture settings panel to change values persistently.

MediaTek keeps the picture database behind a signature permission, so the
only supported way for ADB to change e.g. the picture mode *persistently* is
the settings panel itself. This module:

1. opens the panel focused on a known anchor (``PictureHotKey`` extra),
2. finds the target row in a ``uiautomator`` dump using the firmware's own
   (localised) titles,
3. moves the focus there with D-pad events,
4. changes the value step by step and verifies each step with the canonical
   value that ``PictureManager`` writes to logcat.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from .api import MenuItem, XgimiCommandError, XgimiH20, parse_log_line

_LOGGER = logging.getLogger(__name__)

MAIN_PAGE = "picture_preference"
ADVANCED_PAGE = "picture_advanced_preference"
HOTKEY = {MAIN_PAGE: "Picture_Mode", ADVANCED_PAGE: "Gamma"}

KEY_DELAY = 0.45


@dataclass
class MenuResult:
    key: str
    target: Any
    value: Any
    ok: bool
    message: str = ""


def load_i18n() -> dict[str, Any]:
    path = os.path.join(os.path.dirname(__file__), "menu_i18n.json")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class MenuRobot:
    """Settings-panel automation for one projector."""

    def __init__(self, client: XgimiH20, i18n: dict[str, Any]) -> None:
        self._client = client
        self._i18n = i18n
        self._locale_chain: list[str] = ["default"]
        self.last_snapshot: dict[str, Any] = {}

    # ------------------------------------------------------------ i18n
    def set_locale(self, locale: str | None) -> None:
        """Set the device locale, e.g. ``de-DE``."""
        chain: list[str] = []
        if locale:
            lang, _, region = locale.replace("_", "-").partition("-")
            if region:
                chain.append(f"{lang}-r{region}")
            chain.append(lang)
        chain.append("default")
        self._locale_chain = [loc for loc in chain if loc in self._i18n["titles"]]
        if "default" not in self._locale_chain:
            self._locale_chain.append("default")

    def page_of(self, key: str) -> str:
        return self._i18n["prefs"][key][1]

    def _title(self, key: str) -> str | None:
        for loc in self._locale_chain:
            title = self._i18n["titles"].get(loc, {}).get(key)
            if title:
                return title.replace("\xa0", " ").strip()
        return None

    def _keys_for_title(self, title: str, kind: str | None = None) -> list[str]:
        keys = []
        for key, (key_kind, _page) in self._i18n["prefs"].items():
            if self._title(key) == title and (kind is None or key_kind == kind):
                keys.append(key)
        return keys

    def canonical(self, label: str | None, allowed: list[str] | None = None) -> str | None:
        """Translate a displayed picker label into the canonical value."""
        if label is None:
            return None
        label = label.replace("\xa0", " ").strip()
        if allowed and label in allowed:
            return label
        fallback: str | None = None
        for loc in self._locale_chain:
            candidates = self._i18n["values"].get(loc, {}).get(label)
            if candidates:
                if allowed:
                    for cand in candidates:
                        if cand in allowed:
                            return cand
                if fallback is None:
                    fallback = candidates[0]
        return fallback or label

    @staticmethod
    def _kind_matches(key_kind: str, item: MenuItem) -> bool:
        return key_kind == item.kind or key_kind == "any"

    # ------------------------------------------------------------ helpers
    async def _open(self, page: str) -> None:
        await self._client.async_keyevent("WAKEUP")
        await self._client.async_open_picture_menu(HOTKEY[page])
        await asyncio.sleep(0.8)

    async def _press(self, key: str, count: int = 1) -> None:
        if count <= 0:
            return
        await self._client.async_keyevent(*([key] * count))
        await asyncio.sleep(KEY_DELAY + 0.05 * count)

    async def _last_logged(self, since: str, key: str) -> str | None:
        value = None
        for line in await self._client.async_picture_log(since):
            parsed = parse_log_line(line)
            if parsed and parsed[0] == key:
                value = parsed[1]
        return value

    async def _locate(self, key: str, kind: str) -> MenuItem:
        """Move the focus to ``key`` and return its (pre-move) row."""
        title = self._title(key)
        if title is None:
            raise XgimiCommandError(f"No title known for {key}")
        order = list(self._i18n["prefs"])
        target_pos = order.index(key)
        for _attempt in range(6):
            items = await self._client.async_dump_menu()
            focusables = [item for item in items if item.focusable]
            if not focusables:
                raise XgimiCommandError("Picture menu not visible")
            focus_idx = next((i for i, it in enumerate(focusables) if it.focused), None)
            _LOGGER.debug(
                "locate %s: focus=%s visible=%s",
                key,
                focus_idx,
                [(it.title, it.kind, it.focusable) for it in items],
            )
            if focus_idx is None:
                await self._press("DPAD_DOWN")
                continue
            for idx, item in enumerate(focusables):
                if item.title == title and self._kind_matches(kind, item):
                    delta = idx - focus_idx
                    if delta:
                        await self._press("DPAD_DOWN" if delta > 0 else "DPAD_UP", abs(delta))
                    return item
            # Not visible: decide the scroll direction from the XML order.
            disabled = next(
                (it for it in items if it.title == title and not it.focusable), None
            )
            if disabled is not None:
                raise XgimiCommandError(f"{key} is disabled in the current picture mode/source")
            positions = [
                order.index(k)
                for it in focusables
                for k in self._keys_for_title(it.title, it.kind)
                if k in order
            ]
            go_down = not positions or target_pos > max(positions)
            await self._press("DPAD_DOWN" if go_down else "DPAD_UP", len(focusables) - 1)
        raise XgimiCommandError(f"Could not find {key} ({title}) in the picture menu")

    # ------------------------------------------------------------ actions
    async def _set_picker(
        self, key: str, target: str, item: MenuItem, options: list[str], since: str
    ) -> MenuResult:
        current = self.canonical(item.value, options)
        if current == target:
            return MenuResult(key, target, current, True, "unchanged")
        direction = "DPAD_RIGHT"
        if current in options and target in options:
            direction = "DPAD_RIGHT" if options.index(target) > options.index(current) else "DPAD_LEFT"
        flipped = False
        last = current
        for _ in range(len(options) * 2 + 2):
            await self._press(direction)
            await asyncio.sleep(0.25)
            value = await self._last_logged(since, key)
            if value == target:
                return MenuResult(key, target, value, True)
            if value is None or value == last:
                if flipped:
                    break
                direction = "DPAD_LEFT" if direction == "DPAD_RIGHT" else "DPAD_RIGHT"
                flipped = True
            last = value if value is not None else last
        return MenuResult(key, target, last, False, f"could not reach {target}")

    async def _set_seekbar(self, key: str, target: int, item: MenuItem, since: str) -> MenuResult:
        try:
            current = int(item.value) if item.value is not None else None
        except ValueError:
            current = None
        if current is None:
            raise XgimiCommandError(f"Cannot read current value of {key}")
        for _ in range(3):
            delta = target - current
            if delta == 0:
                return MenuResult(key, target, current, True)
            await self._client.async_keyevent(
                *(["DPAD_RIGHT" if delta > 0 else "DPAD_LEFT"] * abs(delta))
            )
            await asyncio.sleep(0.6 + abs(delta) * 0.03)
            logged = await self._last_logged(since, key)
            try:
                current = int(float(logged)) if logged is not None else current
            except ValueError:
                pass
        return MenuResult(key, target, current, current == target)

    async def _set_switch(self, key: str, target: bool, item: MenuItem, since: str) -> MenuResult:
        if item.checked == target:
            return MenuResult(key, target, target, True, "unchanged")
        await self._press("DPAD_CENTER")
        value: bool | None = None
        for _ in range(4):
            await asyncio.sleep(0.4)
            logged = await self._last_logged(since, key)
            if logged is not None:
                value = logged.lower() in ("true", "on", "1")
                break
        if value is None:
            items = await self._client.async_dump_menu()
            title = self._title(key)
            row = next((it for it in items if it.title == title and it.kind == "switch"), None)
            value = row.checked if row else None
        return MenuResult(key, target, value, value == target)

    async def async_apply(
        self, changes: list[tuple[str, Any, str, list[str] | None]]
    ) -> list[MenuResult]:
        """Apply ``(key, target, kind, options)`` changes through the menu."""
        results: list[MenuResult] = []
        by_page: dict[str, list[tuple[str, Any, str, list[str] | None]]] = {}
        for change in changes:
            by_page.setdefault(self.page_of(change[0]), []).append(change)
        try:
            for page in (MAIN_PAGE, ADVANCED_PAGE):
                if page not in by_page:
                    continue
                since = await self._client.async_device_time()
                await self._open(page)
                for key, target, kind, options in by_page[page]:
                    try:
                        item = await self._locate(key, kind)
                        if kind == "picker":
                            result = await self._set_picker(key, target, item, options or [], since)
                        elif kind == "seekbar":
                            result = await self._set_seekbar(key, int(target), item, since)
                        else:
                            result = await self._set_switch(key, bool(target), item, since)
                    except XgimiCommandError as err:
                        result = MenuResult(key, target, None, False, str(err))
                    _LOGGER.debug("Menu result: %s", result)
                    results.append(result)
                    if result.ok:
                        self.last_snapshot[key] = result.value
        finally:
            await self._client.async_close_picture_menu()
        return results

    async def async_snapshot(self, pickers: dict[str, list[str]]) -> dict[str, Any]:
        """Read every visible value of both picture pages."""
        values: dict[str, Any] = {}

        def collect(items: list[MenuItem]) -> None:
            for item in items:
                for key in self._keys_for_title(item.title, item.kind):
                    if item.kind == "switch":
                        values[key] = item.checked
                    elif item.kind == "seekbar":
                        try:
                            values[key] = int(item.value) if item.value else None
                        except ValueError:
                            values[key] = item.value
                    elif item.kind == "picker":
                        values[key] = self.canonical(item.value, pickers.get(key))

        try:
            for page in (MAIN_PAGE, ADVANCED_PAGE):
                await self._open(page)
                if page == ADVANCED_PAGE:
                    await self._press("DPAD_UP", 8)
                seen: set[str] = set()
                for _ in range(5):
                    items = await self._client.async_dump_menu()
                    collect(items)
                    titles = {it.title for it in items}
                    if titles <= seen:
                        break
                    seen |= titles
                    focusables = [it for it in items if it.focusable]
                    await self._client.async_keyevent(*(["DPAD_DOWN"] * max(len(focusables) - 1, 1)))
                    await asyncio.sleep(0.6)
        finally:
            await self._client.async_close_picture_menu()
        self.last_snapshot.update(values)
        return values
