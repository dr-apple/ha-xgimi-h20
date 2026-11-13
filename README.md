# XGIMI H20 für Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)

Vollwertige lokale Integration für den **XGIMI H20** (Google TV, Modell `XR13A`) – ohne Cloud und ohne Zusatz-App auf dem Beamer. Gesteuert wird alles über **ADB** im lokalen Netz.

*English summary below.*

## Was geht

**Bildeinstellungen (lesen + schreiben)**

| Entität | Menü-Key (für `set_settings`) | Werte |
|---|---|---|
| Bildmodus | `picture_mode` | Standard, Movie, Vivid, ImaxEnhanced (HDR), Game, Sport, FilmMaker, User (ISF-Tag), Energy Saving (ISF-Nacht); bei Dolby Vision: DolbyVisionBright, DolbyVisionDark, DolbyVisionVivid, DolbyVisionIQ, DolbyVisionGame, DolbyVisionFilmmaker, DolbyVisionIQFilmmaker |
| Projektor-Lichtleistung | `picture_projector_brightness` | 1–10 |
| Helligkeit / Kontrast / Sättigung | `picture_brightness` / `picture_contrast` / `picture_saturation` | 0–100 |
| Schärfe | `picture_sharpness` | 0–20 |
| Farbtemperatur | `picture_projector_color_temperature` | Standard, D65, Movie, Color temperature 2, Fresh (je nach Bildmodus) |
| Gamma | `picture_gamma` | Dark, Middle, Bright |
| Farbraum | `picture_projector_color_space` | 4 = Nativ, 3 = Rec.709, 2 = DCI-P3, 1 = BT.2020, 7 = Adobe RGB |
| MEMC | `tv_picture_advance_video_mjc_effect` | Off, Real_Cinema, Low, Middle, High |
| AI-Bildoptimierung (AI PQ) | `tv_picture_video_aipq` | true / false |
| Rauschunterdrückung (DNR) | `tv_picture_advance_video_dnr` | Off, Low, Middle, High |
| MPEG-Rauschunterdrückung | `tv_picture_advance_video_mpeg_nr` | Off, Low, Middle, High |
| DI-Filmmodus | `tv_picture_video_di_film_mode` | Off, On (= Automatisch) |
| MaxLebhaft | `tv_picture_video_maxvivid` | Off, Low, Middle, High |
| Adaptive Luma-Steuerung | `tv_picture_video_adaptive_luma_control` | Off, Low, Middle, High |
| Lokaler Kontrast | `tv_picture_video_local_contrast_control` | Off, Low, Middle, High |
| Konturenglättung | `tv_picture_video_de_counter` | Off, Low, Middle, High |
| Niedriger Blaulichtanteil | `tv_picture_video_low_bluelight` | Off, Low, Middle, High |
| HDMI-RGB-Spektrum, PC-Modus | `tv_picture_video_hdmi_rgb_range`, `tv_picture_video_pc_mode` | Auto/Full/Limited, Off/On/Auto |
| Dyn. Schwarzwertverbesserung, Super Frame, Hohe Leistung, Leuchtdichte-Boost, Umgebungsangepasste Lichtleistung, Optischer Farbfilter, Filmmaker-Auto | Schalter | true / false |

Der H20 speichert Bildwerte **pro Profil** – also pro Quelle (Google TV, HDMI 1 …), Signaltyp (SDR, HDR10, HLG, Dolby Vision …) und Bildmodus. Die Integration merkt sich die Werte genauso: Startet ein Dolby-Vision-Film, zeigen die Entitäten die Werte des Dolby-Vision-Profils, danach wieder die des vorherigen. Einstellungen, die der Beamer im aktuellen Profil sperrt (z. B. *Hohe Leistung* bei Dolby Vision), erscheinen als „nicht verfügbar“.

**Status**: Betriebszustand (an / Bildschirmschoner / Standby / offline), Eingangsquelle, HDR-Format (SDR, HDR10, HLG, Dolby Vision …), aktive App, Betriebsstunden, Firmware, letzter Befehl, native Live-Bildwerte (Diagnose).

**Aktionen**: Autofokus, Bildmenü öffnen, Bildeinstellungen einlesen, beliebige Tasten senden.

## Wie es funktioniert – und die zwei Schreibmodi

Die Bild-Datenbank des MediaTek-Chips ist für ADB gesperrt (nur System-Apps dürfen schreiben). Deshalb gibt es drei Wege, die die Integration automatisch wählt:

1. **XGIMI-Systemeinstellungen** (Lichtleistung, Farbraum, Farbtemperatur): werden direkt gesetzt – **unsichtbar und dauerhaft**, der XGIMI-Dienst übernimmt sie sofort.
2. **Dauerhaft (über das Bildmenü)** – Standard: Die Integration bedient das offizielle Bildmenü des Beamers per Fernbedienungs-Tasten. Das Seitenpanel ist dabei **3–20 Sekunden sichtbar**. Jeder Schritt wird über das Systemlog verifiziert, der Wert ist danach genau so gespeichert, als hättest du ihn per Fernbedienung geändert. Funktioniert in allen Sprachen der Firmware.
3. **Live (unsichtbar)**: Werte werden direkt in die Bildpipeline geschrieben – **sofort und unsichtbar**, aber **nicht dauerhaft** (weg beim App-/Quellenwechsel, das Beamer-Menü zeigt weiter den alten Wert). Pro-Stream-Werte (Helligkeit, Kontrast, Schärfe, AI PQ, lokaler Kontrast) wirken live nur während einer laufenden Wiedergabe. Bildmodus und Werte ohne nativen Schlüssel laufen immer über das Menü.

Den Modus stellst du jederzeit unter **Einstellungen → Geräte & Dienste → XGIMI H20 → Konfigurieren** um. Pro Aufruf lässt er sich im Dienst `xgimi_h20.set_settings` mit `mode: live` oder `mode: menu` überschreiben.

**Aktuelle Werte – ohne Menü**: Die Integration liest fast alles still mit:
- **Während ein Video läuft** (HDMI-Eingang oder App mit aktiver Wiedergabe) übernimmt sie alle 30 s die Werte direkt aus der Bildpipeline: Helligkeit, Kontrast, Sättigung, Schärfe, Gamma, Rauschunterdrückung, MPEG-NR, AI PQ, DI-Filmmodus, Adaptive Luma, lokaler Kontrast, MaxLebhaft, Konturenglättung, Blaulicht, HDMI-RGB, PC-Modus. (Ohne Video liefert die Pipeline nur Standardwerte – dann bleibt der zuletzt bekannte Stand stehen.)
- **Bildmodus** (Standard, Film, Lebhaft, Spiel, Sport) aus dem XGIMI-Displaydienst; Dolby-Vision-, Filmmaker- und ISF-Modi aus dem Logstream.
- **Lichtleistung, Farbraum, Farbtemperatur, Super Frame, Hohe Leistung** aus den XGIMI-Systemeinstellungen.
- **Jede Änderung im Bildmenü** – auch per Fernbedienung – kommt über einen dauerhaften Logcat-Stream in Echtzeit an.

Nur MEMC-Stufe, dynamischer Schwarzwert, Leuchtdichte-Boost, Auto-Lichtleistung und Filmmaker-Automatik stehen nirgends außer im Menü. Für die gibt es den Button **„Bildeinstellungen einlesen"** bzw. die Option **„Neue Bildprofile automatisch einlesen"**, die das Menü nur bei einem noch völlig unbekannten Profil einmal liest. Der letzte Stand wird über Neustarts gespeichert.

## Installation

1. HACS → ⋮ → *Benutzerdefinierte Repositories* → `https://github.com/dr-apple/ha-xgimi-h20`, Kategorie *Integration* → installieren → Home Assistant neu starten.
2. Am Beamer ADB aktivieren: *Einstellungen → System → Info → 7× auf „Android TV OS-Build"*, dann *Entwickleroptionen → USB-Debugging / Netzwerk-Debugging*.
3. *Einstellungen → Geräte & Dienste → Integration hinzufügen → XGIMI H20* (wird auch automatisch per Zeroconf gefunden), IP-Adresse eintragen.
   - Ist die **Android-TV-Integration** von Home Assistant schon mit dem Beamer verbunden, wird deren Schlüssel (`.storage/androidtv_adbkey`) benutzt – es ist **keine neue Freigabe** nötig.
   - Sonst erzeugt die Integration einen eigenen Schlüssel; die Freigabe-Abfrage am Beamer mit „Immer zulassen" bestätigen.

## Dienste

```yaml
# Mehrere Werte auf einmal (ein Menü-Durchgang)
action: xgimi_h20.set_settings
data:
  device_id: <XGIMI H20>
  settings:
    picture_mode: Movie
    tv_picture_advance_video_mjc_effect: High
    picture_brightness: 52
  mode: menu   # optional: menu | live

# Alle Werte aus dem Menü einlesen (liefert sie auch als Antwort zurück)
action: xgimi_h20.read_picture_menu
data:
  device_id: <XGIMI H20>

# Tasten senden
action: xgimi_h20.send_keys
data:
  device_id: <XGIMI H20>
  keys: [HOME]
```

## Hinweise

- Getestet mit Firmware `UKN6.260116.134` (Android 14).
- Ein- und Ausschalten, Lautstärke und App-Start erledigen weiterhin die Android-TV- bzw. Google-Cast-Integration; diese Integration ergänzt alles rund ums Bild.
- Im Standby ist der Beamer per ADB nicht erreichbar – die Bild-Entitäten sind dann „nicht verfügbar", der Betriebszustand zeigt „offline".

---

## English summary

Local Home Assistant integration for the **XGIMI H20** (Google TV) using ADB – no cloud, no companion app. Reads and controls picture mode, light output, brightness/contrast/saturation/sharpness, gamma, colour space/temperature, MEMC, AI PQ, noise reduction, local contrast and more, plus status sensors and autofocus.

MediaTek protects the picture database, so persistent changes are made by driving the projector's own picture menu (panel briefly visible, every step verified via logcat). Alternatively a **live** mode writes directly into the PQ pipeline (invisible, instant, not persistent). Switch between them in the integration options. Changes made with the remote are picked up in real time via a logcat stream. Reuses the Android TV integration's ADB key when available.

License: MIT. Not affiliated with XGIMI.
