"""Build custom_components/xgimi_h20/menu_i18n.json from the projector firmware.

Usage:
    adb pull /system_ext/priv-app/XgimiTvSettingsVendor/XgimiTvSettingsVendor.apk
    jadx -d XgimiTvSettingsVendor XgimiTvSettingsVendor.apk
    python3 scripts/build_menu_i18n.py XgimiTvSettingsVendor/resources/res

The table maps settings-panel preference keys to their localised titles and
localised picker labels back to the canonical values the firmware logs. It
contains only short UI labels needed to locate rows in the panel.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys

KEEP = [
    # picture_preference.xml (main page) and picture_advanced_preference.xml
    "picture_auto_brightness_control", "picture_projector_brightness", "picture_iris",
    "picture_luminance_boost", "picture_color_enhancing_filter", "picture_mode",
    "picture_auto_backlight", "picture_auto_brightness", "picture_backlight",
    "picture_brightness", "picture_contrast", "picture_saturation", "picture_hue",
    "picture_sharpness", "picture_auto_switch", "picture_game_control_board",
    "picture_advanced_video", "picture_reset_to_default",
    "power_picture_off", "picture_white_balance11", "picture_white_balance2",
    "picture_color_temperature", "picture_projector_color_temperature",
    "picture_projector_dynamic_black_enhancement", "picture_gamma",
    "picture_projector_color_space", "tv_picture_video_color_space", "picture_color_tune",
    "tv_picture_advance_video_mjc_effect", "picture_list_hdr", "picture_notification",
    "tv_picture_dv_screen_settings", "picture_pq_calibration", "tv_picture_imax_auto_detected",
    "tv_picture_video_hdmi_rgb_range", "picture_hdr_game", "tv_picture_video_game_main",
    "tv_picture_video_allm", "tv_picture_video_pc_mode", "tv_picture_video_vrr",
    "tv_picture_video_psf", "tv_picture_refresh_rate", "tv_picture_advance_video_dnr",
    "tv_picture_advance_video_mpeg_nr", "tv_picture_video_dlg_mode", "tv_picture_video_aipq",
    "tv_picture_video_di_film_mode", "tv_picture_video_maxvivid",
    "tv_picture_video_adaptive_luma_control", "tv_picture_video_local_contrast_control",
    "tv_picture_video_aisr", "tv_picture_video_de_counter", "tv_picture_advanced_3d_setting",
    "tv_picture_video_low_bluelight", "tv_picture_video_dtm", "picture_dark_detail",
    "picture_global_dimming", "tv_picture_video_auto_color", "picture_fmm",
    "tv_picture_video_flesh_tone", "tv_picture_video_blue_stretch", "picture_apo",
    "picture_dv_global_dimming", "picture_light_sense", "picture_precision_rendering",
    "tv_picture_live_tone", "tv_picture_advanced_vac", "tv_picture_advanced_game_direct_mode",
    "picture_advanced_anti_rbe", "picture_advanced_performance_mode",
]
KINDS = {
    "PickerControlPreference": "picker",
    "SwitchControlPreference": "switch",
    "GmSwitchControlPreference": "switch",
    "SeekbarControlPreference": "seekbar",
}
CANON = {
    "Standard", "Movie", "Vivid", "Game", "Sport", "FilmMaker", "User", "Energy Saving",
    "ImaxEnhanced", "DolbyVisionBright", "DolbyVisionDark", "DolbyVisionVivid", "DolbyVisionIQ",
    "DolbyVisionGame", "DolbyVisionFilmmaker", "DolbyVisionIQFilmmaker",
    "Off", "Real_Cinema", "Low", "Middle", "High", "Dark", "Bright", "Auto", "Full",
    "Limited", "On", "off", "3", "2", "1", "7", "4", "16", "0", "17", "Cool", "Warm",
}
# partner label suffix -> canonical picture mode
PARTNER = {
    "standard": "Standard", "movie": "Movie", "sports": "Sport", "game": "Game",
    "isf_day": "User", "isf_night": "Energy Saving", "dovi_bright": "DolbyVisionBright",
    "dovi_dark": "DolbyVisionDark", "dovi_game": "DolbyVisionGame", "dovi_iq": "DolbyVisionIQ",
    "dovi_vivid": "DolbyVisionVivid",
}


def load(directory: str) -> tuple[dict, dict]:
    strings: dict[str, str] = {}
    arrays: dict[str, list[str]] = {}
    path = os.path.join(directory, "strings.xml")
    if os.path.exists(path):
        text = open(path, encoding="utf-8").read()
        for key, value in re.findall(r'<string name="([^"]+)"[^>]*>(.*?)</string>', text, re.S):
            strings[key] = re.sub(r"\\(.)", r"\1", value).strip().strip('"')
    path = os.path.join(directory, "arrays.xml")
    if os.path.exists(path):
        text = open(path, encoding="utf-8").read()
        for match in re.finditer(
            r'<(?:string-)?array name="([^"]+)"[^>]*>(.*?)</(?:string-)?array>', text, re.S
        ):
            arrays[match.group(1)] = re.findall(r"<item>(.*?)</item>", match.group(2), re.S)
    return strings, arrays


def main(res: str) -> None:
    base_s, base_a = load(os.path.join(res, "values"))
    prefs: dict[str, list[str]] = {}
    for page in ("picture_preference", "picture_advanced_preference"):
        text = open(os.path.join(res, "xml", f"{page}.xml"), encoding="utf-8").read()
        for match in re.finditer(r"<([\w.]+)\s([^>]*?)/?>", text):
            attrs = match.group(2)
            key = re.search(r'android:key="([^"]+)"', attrs)
            title = re.search(r'android:title="@string/([^"]+)"', attrs)
            if key and title and key.group(1) in KEEP:
                kind = KINDS.get(match.group(1).split(".")[-1], "basic")
                prefs[key.group(1)] = [kind, page, title.group(1)]

    pairs = []
    for name in base_a:
        if name.endswith("_value"):
            entries = name[: -len("_value")]
            for cand in (entries, entries.replace("_entries", "_entry")):
                if cand in base_a and len(base_a[cand]) == len(base_a[name]):
                    pairs.append((cand, name))
                    break
    pairs.append(("picture_advanced_vedio_di_film_mode_entries", None))

    titles: dict[str, dict] = {}
    values: dict[str, dict] = {}
    for directory in sorted(glob.glob(os.path.join(res, "values*"))):
        loc = os.path.basename(directory)[7:] or "default"
        if re.search(r"-(v\d+|land|port|sw\d|h\d|w\d|night|television|ldrtl|large|dpi)", directory):
            continue
        strings, arrays = load(directory)
        if not strings and not arrays:
            continue
        all_s = {**base_s, **strings}
        all_a = {**base_a, **arrays}

        def resolve(value: str) -> str:
            match = re.match(r"@string/(.+)", value.strip())
            return all_s.get(match.group(1), value) if match else value

        titles[loc] = {
            key: all_s[res_name].replace("\xa0", " ").strip()
            for key, (_k, _p, res_name) in prefs.items()
            if all_s.get(res_name)
        }
        label_map: dict[str, set] = {}
        for entries, value_name in pairs:
            labels = [resolve(i) for i in all_a.get(entries, [])]
            canon = [resolve(i) for i in (base_a.get(value_name) if value_name else base_a.get(entries, []))]
            for label, value in zip(labels, canon):
                if value == "Auto" and value_name is None:
                    value = "On"  # DI film mode logs On/Off
                label_map.setdefault(label.replace("\xa0", " ").strip(), set()).add(value)
        for key, label in all_s.items():
            if key.startswith("partner_arrays_picturemode_") and "_value_" not in key:
                suffix = key[len("partner_arrays_picturemode_"):]
                base = re.sub(r"_(hdr10_adaptive|hdr10plus|hdr10|hlg|tch)$", "", suffix)
                if base in PARTNER:
                    label_map.setdefault(label.replace("\xa0", " ").strip(), set()).add(PARTNER[base])
        values[loc] = {
            label: sorted(c for c in canon if c in CANON)
            for label, canon in label_map.items()
            if any(c in CANON for c in canon) and label not in CANON
        }

    default_t, default_v = titles["default"], values["default"]
    for loc in list(titles):
        if loc == "default":
            continue
        titles[loc] = {k: v for k, v in titles[loc].items() if default_t.get(k) != v}
        values[loc] = {k: v for k, v in values[loc].items() if default_v.get(k) != v}
        if not titles[loc] and not values[loc]:
            del titles[loc], values[loc]

    out = {
        "prefs": {key: [kind, page] for key, (kind, page, _r) in prefs.items()},
        "titles": titles,
        "values": values,
    }
    target = os.path.join(os.path.dirname(__file__), "..", "custom_components", "xgimi_h20", "menu_i18n.json")
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(out, handle, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(prefs)} prefs, {len(titles)} locales -> {os.path.getsize(target)} bytes")


if __name__ == "__main__":
    main(sys.argv[1])
