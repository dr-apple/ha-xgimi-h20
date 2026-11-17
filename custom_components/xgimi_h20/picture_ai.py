"""AI picture optimisation: adapt the picture to what is playing.

The title comes from the Entertainment Profiler integration
(https://github.com/dr-apple/ha-entertainment-profiler): its active-profile
sensor names the app and series / movie (``Plex|The Expanse``) and, since
v0.3.0, whether it is a series or a movie. An ``ai_task`` entity classifies
each title once (genre and how dark it is); the result is kept per title.

For every title the projector gets genre defaults (light output, brightness,
contrast, saturation, sharpness, noise reduction, local contrast, adaptive
luma, de-contour, MaxVivid, colour temperature). Whatever the user changes by
hand while a title plays is learned for that title and HDR format and wins
next time. Only values that differ are written (live, invisible); picture mode
and MEMC go through the menu and are only set from learned values.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AI_NOTIFY,
    CONF_AI_PROFILER,
    CONF_AI_TASK,
    DOMAIN,
    EVENT_PICTURE_AI_APPLIED,
    SETTINGS_BY_KEY,
)

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
APPLY_DELAY = 20  # s after a title / HDR change
LEARN_STABLE = 90  # s values must stay unchanged before they are learned
LEARN_MIN_TITLE_AGE = 120  # s the title must already be playing
NOTIFY_WAIT = 25  # s to wait for the profiler's own apply (and its notification)
NOTIFY_GAP = 6  # s after that, so both notifications do not overlap

L, H, K, S = (
    "picture_projector_brightness",
    "picture_brightness",
    "picture_contrast",
    "picture_saturation",
)
SHARP = "picture_sharpness"
MODE = "picture_mode"
MEMC = "tv_picture_advance_video_mjc_effect"
DNR = "tv_picture_advance_video_dnr"
LOCAL_CONTRAST = "tv_picture_video_local_contrast_control"
LUMA = "tv_picture_video_adaptive_luma_control"
DECONTOUR = "tv_picture_video_de_counter"
MAXVIVID = "tv_picture_video_maxvivid"
COLOR_TEMP = "picture_projector_color_temperature"

LEARN_KEYS = (L, H, K, S, MODE, MEMC)

GENRES = ("drama", "thriller", "action", "comedy", "animation", "documentary", "horror", "sport", "scifi")
DARKNESS = ("bright", "medium", "dark")
KINDS = ("series", "movie")

# light output, brightness, contrast, saturation
GENRE_BASE: dict[str, tuple[int, int, int, int]] = {
    "drama": (8, 50, 45, 50),
    "thriller": (7, 49, 46, 48),
    "horror": (7, 48, 48, 46),
    "action": (9, 50, 50, 54),
    "scifi": (9, 50, 48, 52),
    "comedy": (8, 50, 45, 52),
    "animation": (9, 50, 48, 58),
    "documentary": (8, 50, 46, 52),
    "sport": (10, 50, 50, 54),
}
ENHANCE_BASE: dict[str, Any] = {
    SHARP: 3,
    DNR: "Middle",
    LOCAL_CONTRAST: "Off",
    LUMA: "Off",
    DECONTOUR: "Middle",
    MAXVIVID: "Off",
}
ENHANCE_GENRE: dict[str, dict[str, Any]] = {
    "animation": {SHARP: 5, DNR: "Low", DECONTOUR: "High"},
    "documentary": {SHARP: 5, MAXVIVID: "Low"},
}
ENHANCE_DARK = {LOCAL_CONTRAST: "Middle", LUMA: "Low"}
COLOR_TEMP_MOVIE = "16"  # D65
COLOR_TEMP_OTHER = "24"  # "Movie" preset

HDR_NAMES = {
    "SDR": "SDR", "HDR10": "HDR10", "HDR10PLUS": "HDR10+", "HLG": "HLG",
    "DOLBYVISION": "Dolby Vision", "DV": "Dolby Vision",
}

# Older (German) slugs from the YAML version of this feature.
LEGACY_GENRES = {"komoedie": "comedy", "doku": "documentary"}
LEGACY_DARKNESS = {"hell": "bright", "mittel": "medium", "dunkel": "dark"}
LEGACY_KINDS = {"Serie": "series", "Film": "movie"}
LEGACY_KEYS = {"L": L, "H": H, "K": K, "S": S, "m": MODE, "M": MEMC}

TEXT = {
    "de": {
        "names": {
            L: "Lichtleistung", H: "Helligkeit", K: "Kontrast", S: "Sättigung", SHARP: "Schärfe",
            MODE: "Bildmodus", MEMC: "MEMC", DNR: "Rauschunterdrückung", LOCAL_CONTRAST: "Lokaler Kontrast",
            LUMA: "Adaptive Luma", DECONTOUR: "Konturenglättung", MAXVIVID: "MaxLebhaft",
            COLOR_TEMP: "Farbtemperatur",
        },
        "values": {
            "Off": "Aus", "Low": "Niedrig", "Middle": "Mittel", "High": "Hoch", "On": "An",
            "16": "D65", "24": "Film", "0": "Standard", "17": "Frisch", "-1": "Warm",
            "Movie": "Film", "Vivid": "Lebhaft", "Game": "Spiel", "Real_Cinema": "Echtes Kino",
        },
        "genres": {
            "drama": "Drama", "thriller": "Thriller", "action": "Action", "comedy": "Komödie",
            "animation": "Animation", "documentary": "Doku", "horror": "Horror", "sport": "Sport",
            "scifi": "Sci-Fi",
        },
        "darkness": {"bright": "hell", "medium": "mittel", "dark": "dunkel"},
        "title": "Beamer-Bild",
    },
    "en": {
        "names": {
            L: "Light output", H: "Brightness", K: "Contrast", S: "Saturation", SHARP: "Sharpness",
            MODE: "Picture mode", MEMC: "MEMC", DNR: "Noise reduction", LOCAL_CONTRAST: "Local contrast",
            LUMA: "Adaptive luma", DECONTOUR: "De-contour", MAXVIVID: "MaxVivid",
            COLOR_TEMP: "Colour temperature",
        },
        "values": {
            "16": "D65", "24": "Movie", "0": "Standard", "17": "Fresh", "-1": "Warm",
            "Real_Cinema": "Real cinema",
        },
        "genres": {
            "drama": "Drama", "thriller": "Thriller", "action": "Action", "comedy": "Comedy",
            "animation": "Animation", "documentary": "Documentary", "horror": "Horror",
            "sport": "Sport", "scifi": "Sci-fi",
        },
        "darkness": {"bright": "bright", "medium": "medium", "dark": "dark"},
        "title": "Projector picture",
    },
}

IDLE_STATES = ("none", "unknown", "unavailable", "")


def genre_targets(genre: str | None, darkness: str | None, kind: str | None) -> dict[str, Any]:
    """Default picture values for a genre (before learned values)."""
    genre = genre if genre in GENRE_BASE else "drama"
    light, bright, contrast, sat = GENRE_BASE[genre]
    bright += 2 if darkness == "dark" else (-1 if darkness == "bright" else 0)
    target: dict[str, Any] = {L: light, H: bright, K: contrast, S: sat, **ENHANCE_BASE}
    target.update(ENHANCE_GENRE.get(genre, {}))
    if darkness == "dark" or genre in ("horror", "thriller"):
        target.update(ENHANCE_DARK)
    target[COLOR_TEMP] = COLOR_TEMP_MOVIE if kind == "movie" else COLOR_TEMP_OTHER
    return target


def diff(target: dict[str, Any], current: dict[str, Any], locked: frozenset[str]) -> dict[str, Any]:
    """Values of ``target`` that differ from ``current`` and are not locked."""
    return {
        key: value
        for key, value in target.items()
        if key in SETTINGS_BY_KEY and key not in locked and str(current.get(key)) != str(value)
    }


def normalize_entry(raw: dict[str, Any]) -> dict[str, Any]:
    """Accept the stored format and the older YAML/todo format."""
    genre = raw.get("genre", raw.get("g"))
    darkness = raw.get("darkness", raw.get("d"))
    kind = raw.get("kind", raw.get("t"))
    learned = raw.get("learned", raw.get("u")) or {}
    return {
        "genre": LEGACY_GENRES.get(genre, genre) if genre in GENRES or genre in LEGACY_GENRES else None,
        "darkness": LEGACY_DARKNESS.get(darkness, darkness) if darkness else None,
        "kind": LEGACY_KINDS.get(kind, kind) if kind in KINDS or kind in LEGACY_KINDS else None,
        "learned": {
            hdr: {LEGACY_KEYS.get(k, k): v for k, v in values.items()}
            for hdr, values in learned.items()
            if isinstance(values, dict)
        },
    }


class PictureAI:
    """Runs the optimisation for one projector."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.profiler: str = entry.options[CONF_AI_PROFILER]
        self.ai_task: str | None = entry.options.get(CONF_AI_TASK) or None
        self.notify: str | None = entry.options.get(CONF_AI_NOTIFY) or None
        self._store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}.picture_ai")
        self.enabled = False
        self.titles: dict[str, dict[str, Any]] = {}
        self.last_change: dict[str, Any] | None = None
        self._plan: dict[str, Any] | None = None  # what was applied last (for learning)
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._apply_timer: CALLBACK_TYPE | None = None
        self._learn_timer: CALLBACK_TYPE | None = None
        self._learn_snapshot: dict[str, Any] | None = None
        self._key_since: datetime = dt_util.utcnow()
        self._last_signal: str | None = None
        self._lock = asyncio.Lock()
        self.running = False

    # ------------------------------------------------------------------ setup
    async def async_start(self) -> None:
        stored = await self._store.async_load() or {}
        self.enabled = bool(stored.get("enabled", False))
        self.titles = {k: normalize_entry(v) for k, v in stored.get("titles", {}).items()}
        self.last_change = stored.get("last_change")
        self._plan = stored.get("plan")
        self._unsubs.append(
            async_track_state_change_event(self.hass, [self.profiler], self._on_profiler)
        )
        self._unsubs.append(self.coordinator.async_add_listener(self._on_projector))
        self._last_signal = self._hdr()

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for timer in (self._apply_timer, self._learn_timer):
            if timer:
                timer()
        self._apply_timer = self._learn_timer = None

    def _save(self) -> None:
        self._store.async_delay_save(
            lambda: {
                "enabled": self.enabled,
                "titles": self.titles,
                "last_change": self.last_change,
                "plan": self._plan,
            },
            2,
        )

    def add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    @callback
    def _changed(self) -> None:
        for update in list(self._listeners):
            update()

    # ------------------------------------------------------------- read state
    @property
    def language(self) -> str:
        return "de" if (self.hass.config.language or "").startswith("de") else "en"

    @property
    def key(self) -> str | None:
        state = self.hass.states.get(self.profiler)
        if state is None or state.state in IDLE_STATES or not state.attributes.get("source"):
            return None
        if not state.attributes.get("title"):
            # App without a series / movie title (YouTube, a news app, idle):
            # nothing to classify, leave the picture as it is.
            return None
        return state.state

    def _profiler_attr(self, name: str) -> Any:
        state = self.hass.states.get(self.profiler)
        return state.attributes.get(name) if state else None

    def _hdr(self) -> str:
        raw = self.coordinator.data.state.settings.get("TvSourcesStatus") if self.coordinator.data else None
        hdr = (raw or "mm|SDR").partition("|")[2].upper() or "SDR"
        return HDR_NAMES.get(hdr, hdr)

    def _awake(self) -> bool:
        data = self.coordinator.data
        return bool(data and data.state.available and data.state.awake)

    def entry_for(self, key: str | None) -> dict[str, Any] | None:
        return self.titles.get(key) if key else None

    # ----------------------------------------------------------- triggers
    @callback
    def _on_profiler(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if old is not None and new is not None and old.state == new.state:
            return
        self._key_since = dt_util.utcnow()
        self._cancel_learn()
        self._changed()
        self._schedule_apply()

    @callback
    def _on_projector(self) -> None:
        signal = self._hdr()
        if signal != self._last_signal:
            self._last_signal = signal
            self._cancel_learn()
            self._schedule_apply()
        self._check_learn()

    @callback
    def _schedule_apply(self) -> None:
        if self._apply_timer:
            self._apply_timer()
            self._apply_timer = None
        if not self.enabled or self.key is None:
            return

        @callback
        def _run(_now: datetime) -> None:
            self._apply_timer = None
            self.entry.async_create_background_task(
                self.hass, self._safe_apply(), f"{DOMAIN}_picture_ai_apply"
            )

        self._apply_timer = async_call_later(self.hass, APPLY_DELAY, _run)

    async def _safe_apply(self) -> None:
        try:
            await self.async_apply()
        except HomeAssistantError as err:
            _LOGGER.warning("Picture AI could not adjust the picture: %s", err)

    async def async_set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self._save()
        self._changed()
        if enabled:
            self.entry.async_create_background_task(
                self.hass, self._safe_apply(), f"{DOMAIN}_picture_ai_apply"
            )
        else:
            self._cancel_learn()
            if self._apply_timer:
                self._apply_timer()
                self._apply_timer = None

    # ------------------------------------------------------------- classify
    async def _classify(self, key: str) -> dict[str, Any]:
        entry = dict(self.titles.get(key) or normalize_entry({}))
        kind = self._kind()
        if kind:
            entry["kind"] = kind
        if entry.get("genre") is None:
            genre, darkness = await self._ask_ai(key, entry.get("kind"))
            entry["genre"], entry["darkness"] = genre, darkness
        self.titles[key] = entry
        self._save()
        return entry

    def _kind(self) -> str | None:
        kind = self._profiler_attr("media_kind")
        return kind if kind in KINDS else None

    async def _ask_ai(self, key: str, kind: str | None) -> tuple[str, str]:
        if not self.ai_task:
            return "drama", "medium"
        title = self._profiler_attr("title") or key.split("|")[-1]
        summary = ""
        rating = None
        player = self._profiler_attr("media_player")
        if player and (state := self.hass.states.get(player)):
            summary = str(state.attributes.get("media_summary") or "")[:300]
            rating = state.attributes.get("media_content_rating")
        instructions = (
            "Classify this title for the picture tuning of a home-cinema projector. "
            f"Title: {title}. Type: {kind or 'unknown'}. "
            f"Description of an example episode or the film: {summary or 'none'}. "
            f"Age rating: {rating or 'unknown'}. "
            "Choose exactly one genre (the typical main genre of the whole title, not just this "
            "episode) and how dark the images are on average."
        )
        try:
            response = await self.hass.services.async_call(
                "ai_task",
                "generate_data",
                {
                    "entity_id": self.ai_task,
                    "task_name": "Classify title for picture",
                    "instructions": instructions,
                    "structure": {
                        "genre": {"selector": {"select": {"options": list(GENRES)}}, "required": True},
                        "darkness": {"selector": {"select": {"options": list(DARKNESS)}}, "required": True},
                    },
                },
                blocking=True,
                return_response=True,
            )
            data = (response or {}).get("data") or {}
            genre = data.get("genre") if data.get("genre") in GENRES else "drama"
            darkness = data.get("darkness") if data.get("darkness") in DARKNESS else "medium"
            return genre, darkness
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("AI task %s failed, using drama: %s", self.ai_task, err)
            return "drama", "medium"

    # ---------------------------------------------------------------- apply
    async def async_apply(self, dry_run: bool = False) -> dict[str, Any]:
        """Work out and (unless ``dry_run``) apply the picture for the title."""
        key = self.key
        if key is None:
            return {"key": None, "changes": {}, "reason": "nothing playing"}
        if not self._awake():
            return {"key": key, "changes": {}, "reason": "projector not on"}
        async with self._lock:
            self.running = True
            self._changed()
            try:
                return await self._apply(key, dry_run)
            finally:
                self.running = False
                self._changed()

    async def _apply(self, key: str, dry_run: bool) -> dict[str, Any]:
        entry = await self._classify(key) if not dry_run else (self.titles.get(key) or normalize_entry({}))
        hdr = self._hdr()
        target = genre_targets(entry.get("genre"), entry.get("darkness"), entry.get("kind"))
        target.update(entry.get("learned", {}).get(hdr, {}))
        data = self.coordinator.data
        current = dict(data.values)
        changes = diff(target, current, data.disabled)
        result = {
            "key": key, "hdr": hdr, "genre": entry.get("genre"), "darkness": entry.get("darkness"),
            "kind": entry.get("kind"), "target": target, "changes": changes,
        }
        if dry_run:
            return result
        self._plan = {
            "key": key,
            "hdr": hdr,
            "values": {k: target.get(k, current.get(k)) for k in LEARN_KEYS},
        }
        if changes:
            try:
                await self.coordinator.async_set_values(changes, "live")
            except HomeAssistantError as err:
                _LOGGER.warning("Picture AI: some values were not applied: %s", err)
        rows = self._rows(changes, current)
        self.last_change = {
            "time": dt_util.utcnow().isoformat(),
            "key": key, "hdr": hdr, "genre": entry.get("genre"), "darkness": entry.get("darkness"),
            "kind": entry.get("kind"), "changes": rows,
        }
        self._save()
        self._changed()
        self.hass.bus.async_fire(
            EVENT_PICTURE_AI_APPLIED,
            {"entry_id": self.entry.entry_id, **self.last_change},
        )
        if rows and self.notify:
            self.entry.async_create_background_task(
                self.hass, self._send_notification(rows, entry), f"{DOMAIN}_picture_ai_notify"
            )
        return result

    def _label(self, key: str, value: Any) -> str:
        if value is None or value == -1:
            return "–"
        return TEXT[self.language]["values"].get(str(value), str(value))

    def _rows(self, changes: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
        names = TEXT[self.language]["names"]
        return [
            {
                "setting": key,
                "name": names.get(key, key),
                "old": self._label(key, current.get(key)),
                "new": self._label(key, value),
            }
            for key, value in changes.items()
        ]

    def describe(self, entry: dict[str, Any] | None) -> str:
        if not entry:
            return ""
        text = TEXT[self.language]
        parts = [text["genres"].get(entry.get("genre"), entry.get("genre") or "")]
        if entry.get("darkness"):
            parts.append(text["darkness"].get(entry["darkness"], entry["darkness"]))
        return ", ".join(p for p in parts if p)

    async def _send_notification(self, rows: list[dict[str, Any]], entry: dict[str, Any]) -> None:
        # Let the Entertainment Profiler finish (it may show its own notification first).
        for _ in range(NOTIFY_WAIT):
            if not self._profiler_attr("applying"):
                break
            await asyncio.sleep(1)
        await asyncio.sleep(NOTIFY_GAP)
        message = "{}: {}".format(
            self.describe(entry),
            ", ".join(f"{r['name']} {r['old']} → {r['new']}" for r in rows),
        )
        try:
            await self.hass.services.async_call(
                "notify",
                "send_message",
                {"entity_id": self.notify, "title": TEXT[self.language]["title"], "message": message},
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Picture AI notification failed: %s", err)

    # ---------------------------------------------------------------- learn
    def _learn_values(self) -> dict[str, Any] | None:
        values = self.coordinator.data.values if self.coordinator.data else {}
        current = {k: values.get(k) for k in LEARN_KEYS}
        return None if any(v is None for v in current.values()) else current

    @callback
    def _cancel_learn(self) -> None:
        if self._learn_timer:
            self._learn_timer()
        self._learn_timer = None
        self._learn_snapshot = None

    @callback
    def _check_learn(self) -> None:
        if not self.enabled or self.running or self._plan is None or not self._awake():
            self._cancel_learn()
            return
        if self.coordinator.data.busy:
            return
        key, hdr = self.key, self._hdr()
        if key != self._plan.get("key") or hdr != self._plan.get("hdr"):
            self._cancel_learn()
            return
        current = self._learn_values()
        if current is None:
            return
        planned = self._plan.get("values", {})
        if all(str(current[k]) == str(planned.get(k)) for k in LEARN_KEYS if planned.get(k) is not None):
            self._cancel_learn()
            return
        if self._learn_snapshot == current and self._learn_timer:
            return
        self._cancel_learn()
        self._learn_snapshot = current

        @callback
        def _learn(_now: datetime) -> None:
            self._learn_timer = None
            if self._learn_values() != self._learn_snapshot or self.key != key:
                return
            if (dt_util.utcnow() - self._key_since).total_seconds() < LEARN_MIN_TITLE_AGE:
                return
            self._remember(key, hdr, current)

        self._learn_timer = async_call_later(self.hass, LEARN_STABLE, _learn)

    @callback
    def _remember(self, key: str, hdr: str, values: dict[str, Any]) -> None:
        entry = self.titles.setdefault(key, normalize_entry({}))
        entry.setdefault("learned", {})[hdr] = dict(values)
        if self._plan:
            self._plan["values"] = dict(values)
        _LOGGER.info("Picture AI learned %s for %s (%s)", values, key, hdr)
        self._save()
        self._changed()

    # -------------------------------------------------------------- import
    def import_titles(self, titles: dict[str, Any], replace: bool = False) -> int:
        if replace:
            self.titles = {}
        for key, raw in titles.items():
            if isinstance(raw, str):
                raw = json.loads(raw or "{}")
            self.titles[key] = normalize_entry(raw)
        self._save()
        self._changed()
        return len(titles)

    def forget(self, key: str) -> bool:
        removed = self.titles.pop(key, None) is not None
        if removed:
            self._save()
            self._changed()
        return removed
