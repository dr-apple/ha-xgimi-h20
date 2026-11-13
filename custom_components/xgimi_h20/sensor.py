"""Sensors: power state, signal, light-source hours, native PQ, ..."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import XgimiConfigEntry
from .coordinator import XgimiData
from .entity import XgimiEntity

HDR_NAMES = {
    "SDR": "SDR",
    "HDR10": "HDR10",
    "HDR10PLUS": "HDR10+",
    "HLG": "HLG",
    "DOLBYVISION": "Dolby Vision",
    "DV": "Dolby Vision",
    "TECHNT": "Technicolor",
    "HDRVIVID": "HDR Vivid",
}

POWER_STATES = {"Awake": "on", "Dreaming": "screensaver", "Asleep": "standby", "Dozing": "standby"}

NATIVE_ATTRS = (
    "Picture_Mode", "Backlight", "Brightness", "Contrast", "Saturation", "Hue", "SR_Strength",
    "Gamma", "Color_Temperature", "AI_PQ", "AISR", "MJC_Effect", "MJC_Deblur", "MJC_Dejudder",
    "Local_Contrast", "NR", "MPEG_NR", "DLC_STRENGTH", "Low_Blue_Light", "Film_Mode", "HDR_Mode",
    "Color space", "Global_Dimming", "Dark_Detail", "Low_Latency", "VRR", "PC_Mode",
    "HDMI_RGB_Range", "Dynamic_Color_Booster", "EDGE_ENHANCER", "3D_Mode",
)


def _signal(data: XgimiData, index: int) -> str | None:
    raw = data.state.settings.get("TvSourcesStatus")
    if not raw or "|" not in raw:
        return None
    value = raw.split("|")[index]
    if index == 0:
        lower = value.lower()
        if lower in ("mm", "dtv", ""):
            return "Google TV"
        if lower.startswith("hdmi"):
            return f"HDMI {lower[4:]}".strip()
    if index == 1:
        return HDR_NAMES.get(value.upper(), value)
    return value


def _hours(data: XgimiData) -> float | None:
    raw = data.state.settings.get("tv_life_time")
    try:
        return round(int(raw) / 60, 1)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, kw_only=True)
class XgimiSensorDescription(SensorEntityDescription):
    value: Callable[[XgimiData], Any]
    attrs: Callable[[XgimiData], dict[str, Any]] | None = None
    always_available: bool = False


SENSORS = (
    XgimiSensorDescription(
        key="power_state", translation_key="power_state", device_class=SensorDeviceClass.ENUM,
        options=["on", "screensaver", "standby", "offline"], icon="mdi:projector",
        value=lambda d: POWER_STATES.get(d.state.wakefulness or "", "standby") if d.state.available else "offline",
        always_available=True,
    ),
    XgimiSensorDescription(
        key="input_source", translation_key="input_source", icon="mdi:video-input-hdmi",
        value=lambda d: _signal(d, 0),
    ),
    XgimiSensorDescription(
        key="hdr_format", translation_key="hdr_format", icon="mdi:hdr",
        value=lambda d: _signal(d, 1),
    ),
    XgimiSensorDescription(
        key="foreground_app", translation_key="foreground_app", icon="mdi:application",
        value=lambda d: d.state.foreground_package,
    ),
    XgimiSensorDescription(
        key="operating_hours", translation_key="operating_hours", icon="mdi:timer-outline",
        native_unit_of_measurement=UnitOfTime.HOURS, device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING, suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC, value=_hours,
    ),
    XgimiSensorDescription(
        key="firmware", translation_key="firmware", icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda d: d.state.props.get("ro.build.display.id"),
    ),
    XgimiSensorDescription(
        key="last_result", translation_key="last_result", icon="mdi:check-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda d: ("busy" if d.busy else (d.last_result or "-"))[:250],
        always_available=True,
    ),
    XgimiSensorDescription(
        key="native_pq", translation_key="native_pq", icon="mdi:tune-vertical",
        entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False,
        value=lambda d: d.state.pq.get("Backlight") if d.state.pq else None,
        attrs=lambda d: {key: d.state.pq.get(key) for key in NATIVE_ATTRS if key in d.state.pq},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: XgimiConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(XgimiSensor(coordinator, description) for description in SENSORS)


class XgimiSensor(XgimiEntity, SensorEntity):
    entity_description: XgimiSensorDescription

    def __init__(self, coordinator, description: XgimiSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        if self.entity_description.always_available:
            return self.coordinator.data is not None
        return super().available

    @property
    def native_value(self):
        return self.entity_description.value(self.coordinator.data)

    @property
    def extra_state_attributes(self):
        if self.entity_description.attrs is None:
            return None
        return self.entity_description.attrs(self.coordinator.data)
