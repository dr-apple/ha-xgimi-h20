"""Constants and the setting catalogue for the XGIMI H20 integration."""

from __future__ import annotations

from dataclasses import dataclass, field

DOMAIN = "xgimi_h20"
MANUFACTURER = "XGIMI"

CONF_ADBKEY = "adbkey"
CONF_WRITE_MODE = "write_mode"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_SNAPSHOT_ON_START = "snapshot_on_start"

WRITE_MODE_MENU = "menu"
WRITE_MODE_LIVE = "live"
WRITE_MODES = [WRITE_MODE_MENU, WRITE_MODE_LIVE]

DEFAULT_PORT = 5555
DEFAULT_SCAN_INTERVAL = 30
DEFAULT_WRITE_MODE = WRITE_MODE_MENU
ANDROIDTV_ADBKEY = ".storage/androidtv_adbkey"
OWN_ADBKEY = ".storage/xgimi_h20_adbkey"

PQ_PERSTREAM_KEYS = {
    "AI_PQ",
    "AISR",
    "Brightness",
    "Contrast",
    "Gaming_MJC_Lvl",
    "Hue",
    "Local_Contrast",
    "MJC_Deblur",
    "MJC_Dejudder",
    "MJC_Effect",
    "SR_Strength",
}


@dataclass(frozen=True)
class PictureSetting:
    """One picture setting of the projector.

    ``key`` is the preference key of the firmware's picture settings panel.
    ``kind`` is the widget type (picker / seekbar / switch).
    ``options`` are the canonical values the firmware logs (pickers).
    ``aliases`` map other canonical spellings (e.g. from the menu labels).
    ``global_key`` routes writes to a Settings.Global key observed by MiscKey
    (invisible + persistent; preferred over the menu).
    ``native`` is the MediaTek PQ JSON key used for "live" writes, and
    ``native_map`` translates canonical values into native ones.
    """

    key: str
    translation_key: str
    kind: str
    icon: str | None = None
    options: tuple[str, ...] = ()
    aliases: dict[str, str] = field(default_factory=dict)
    min_value: int = 0
    max_value: int = 100
    global_key: str | None = None
    global_map: dict[str, str] = field(default_factory=dict)
    native: str | None = None
    native_map: dict[str, str] = field(default_factory=dict)
    entity_category: str | None = None
    enabled_default: bool = True


LEVELS = ("Off", "Low", "Middle", "High")

SETTINGS: tuple[PictureSetting, ...] = (
    # ------------------------------------------------------------ main page
    PictureSetting(
        "picture_mode",
        "picture_mode",
        "picker",
        icon="mdi:image-filter-hdr",
        options=(
            "Standard",
            "Movie",
            "Vivid",
            "ImaxEnhanced",
            "Game",
            "Sport",
            "FilmMaker",
            "User",
            "Energy Saving",
            "DolbyVisionBright",
            "DolbyVisionDark",
            "DolbyVisionVivid",
            "DolbyVisionIQ",
            "DolbyVisionGame",
            "DolbyVisionFilmmaker",
            "DolbyVisionIQFilmmaker",
        ),
    ),
    PictureSetting(
        "picture_projector_brightness",
        "light_output",
        "seekbar",
        icon="mdi:brightness-6",
        min_value=1,
        max_value=10,
        global_key="gm_projector_brightness",
    ),
    PictureSetting("picture_brightness", "brightness", "seekbar", icon="mdi:brightness-5", native="Brightness"),
    PictureSetting("picture_contrast", "contrast", "seekbar", icon="mdi:contrast-box", native="Contrast"),
    PictureSetting("picture_saturation", "saturation", "seekbar", icon="mdi:palette", native="Saturation"),
    PictureSetting(
        "picture_sharpness", "sharpness", "seekbar", icon="mdi:image-filter-center-focus", max_value=20, native="SR_Strength"
    ),
    PictureSetting(
        "picture_auto_brightness_control", "ambient_light_output", "switch", icon="mdi:theme-light-dark"
    ),
    PictureSetting("picture_luminance_boost", "luminance_boost", "switch", icon="mdi:white-balance-sunny"),
    PictureSetting(
        "picture_color_enhancing_filter", "optical_color_filter", "switch", icon="mdi:filter", enabled_default=False
    ),
    PictureSetting(
        "picture_auto_switch", "filmmaker_auto_switch", "switch", icon="mdi:movie-open-cog", enabled_default=False
    ),
    # -------------------------------------------------------- advanced page
    PictureSetting(
        "picture_projector_color_temperature",
        "color_temperature",
        "picker",
        icon="mdi:thermometer",
        options=("Standard", "D65", "Movie", "Color temperature 2", "Fresh"),
    ),
    PictureSetting(
        "picture_projector_dynamic_black_enhancement", "dynamic_black", "switch", icon="mdi:circle-half-full"
    ),
    PictureSetting(
        "picture_gamma", "gamma", "picker", icon="mdi:gamma", options=("Dark", "Middle", "Bright"), native="Gamma"
    ),
    PictureSetting(
        "picture_projector_color_space",
        "color_space",
        "picker",
        icon="mdi:palette-swatch",
        options=("4", "3", "2", "1", "7"),
        global_key="gm_color_space",
    ),
    PictureSetting(
        "tv_picture_advance_video_mjc_effect",
        "memc",
        "picker",
        icon="mdi:motion-play",
        options=("Off", "Real_Cinema", "Low", "Middle", "High"),
    ),
    PictureSetting(
        "tv_picture_video_hdmi_rgb_range",
        "hdmi_rgb_range",
        "picker",
        icon="mdi:hdmi-port",
        options=("Auto", "Full", "Limited"),
        native="HDMI_RGB_Range",
        enabled_default=False,
    ),
    PictureSetting(
        "tv_picture_video_pc_mode",
        "pc_mode",
        "picker",
        icon="mdi:monitor",
        options=("Off", "On", "Auto"),
        native="PC_Mode",
        enabled_default=False,
    ),
    PictureSetting("tv_picture_video_psf", "super_frame", "switch", icon="mdi:animation-play"),
    PictureSetting(
        "tv_picture_advance_video_dnr", "noise_reduction", "picker", icon="mdi:blur", options=LEVELS, native="NR"
    ),
    PictureSetting(
        "tv_picture_advance_video_mpeg_nr",
        "mpeg_noise_reduction",
        "picker",
        icon="mdi:blur-linear",
        options=LEVELS,
        native="MPEG_NR",
    ),
    PictureSetting(
        "tv_picture_video_aipq",
        "ai_pq",
        "switch",
        icon="mdi:creation",
        native="AI_PQ",
        native_map={"True": "On", "False": "Off"},
    ),
    PictureSetting(
        "tv_picture_video_di_film_mode",
        "di_film_mode",
        "picker",
        icon="mdi:filmstrip",
        options=("Off", "On"),
        aliases={"Auto": "On"},
        native="Film_Mode",
    ),
    PictureSetting("tv_picture_video_maxvivid", "max_vivid", "picker", icon="mdi:palette-advanced", options=LEVELS),
    PictureSetting(
        "tv_picture_video_adaptive_luma_control",
        "adaptive_luma",
        "picker",
        icon="mdi:brightness-auto",
        options=LEVELS,
        native="DLC_STRENGTH",
    ),
    PictureSetting(
        "tv_picture_video_local_contrast_control",
        "local_contrast",
        "picker",
        icon="mdi:contrast",
        options=LEVELS,
        native="Local_Contrast",
    ),
    PictureSetting(
        "tv_picture_video_de_counter", "de_contour", "picker", icon="mdi:gradient-horizontal", options=LEVELS
    ),
    PictureSetting(
        "tv_picture_video_low_bluelight",
        "low_blue_light",
        "picker",
        icon="mdi:eye",
        options=LEVELS,
        aliases={"off": "Off"},
        native="Low_Blue_Light",
        native_map={"Off": "off"},
    ),
    PictureSetting(
        "picture_advanced_performance_mode", "high_power", "switch", icon="mdi:lightning-bolt"
    ),
)

SETTINGS_BY_KEY = {setting.key: setting for setting in SETTINGS}

COLOR_SPACE_NAMES = {"4": "original", "3": "rec709", "2": "dci_p3", "1": "bt2020", "7": "adobe_rgb"}

SERVICE_SET_SETTINGS = "set_settings"
SERVICE_READ_PICTURE_MENU = "read_picture_menu"
SERVICE_SEND_KEYS = "send_keys"
ATTR_SETTINGS = "settings"
ATTR_MODE = "mode"
ATTR_KEYS = "keys"
