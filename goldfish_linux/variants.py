"""Varianten-Bündelung — Python-Port von `groupVariants` aus dem Browser.

Eine Bibliothek kann denselben Film in mehreren Auflösungen enthalten (etwa
1080p und 4K als getrennte Dateien). Diese Dateien sind einzelne Items mit
derselben `metadataId`. Ohne Bündelung zeigt das Raster für JEDEN Film mehrere
Kacheln (User-Report 2026-09-18 für den Fire-TV-Client, galt hier genauso:
„es darf pro Film immer nur eine Kachel gezeigt werden").

Autoritativ ist die Browser-Oberfläche
(`internal/webassets/web/app.js`, Funktion `groupVariants`) — dieselbe Logik
gibt es in Swift (`groupVariants` in GoldfishApple) und Kotlin
(`util/groupVariants` in GoldfishAndroid). Wird sie hier geändert, muss sie
dort mitgezogen werden; die vier Implementierungen sind bewusst identisch.

Regeln (1:1 wie im Browser):

* Gruppiert wird über `metadataId`. Items ohne `metadataId` bleiben eigene
  Kacheln.
* **`variantSplit` ist die Ausnahme**: „🔀 Als eigene Kacheln trennen" im
  Detail-Dialog ist eine bewusste Nutzerentscheidung — solche Items bleiben
  eigene Kacheln, obwohl sie sich die `metadataId` mit Geschwistern teilen.
* Repräsentant einer Gruppe ist die Variante mit der größeren Höhe; bei
  gleicher Höhe die mit der höheren Bitrate. Dessen Werte (Pfad, Auflösung,
  Laufzeit, Poster-Grundlage) landen auf der Kachel, damit die angezeigten
  Angaben zu der Datei passen, die beim Klick startet.
* Die Reihenfolge der Kacheln bleibt die Reihenfolge der Liste: die Gruppe
  steht an der Stelle ihres ersten Vorkommens (so wie im Browser).
"""

from __future__ import annotations

from .formatting import format_resolution, format_size


def variant_count(item: dict) -> int:
    """Zahl der Dateien hinter einer Kachel (Badge „×N"). Wie im Browser
    (`cards.js`): bevorzugt die lokal gebündelten `_variants`, sonst das
    `variantCount` des Servers (zählt auch Geschwister in anderen Bibliotheken)."""
    local = len(item.get("_variants") or [])
    server = item.get("variantCount") or 0
    return max(local if local > 1 else 0, server if server > 1 else 0)


def variant_label(v: dict) -> str:
    """Beschriftung einer Variante wie `variantLabel` im Browser: DATEINAME plus
    technische Kenndaten. Der Dateiname ist nötig, weil „Teil 1/Teil 2" oder
    „DVD 1/DVD 2" dieselbe `metadataId` und oft dieselbe Auflösung haben."""
    name = (v.get("relPath") or v.get("path") or "").replace("\\", "/").rsplit("/", 1)[-1]
    parts = [(v.get("container") or "").upper() or "?"]
    res = format_resolution(v.get("width") or 0, v.get("height") or 0)
    if res:
        parts.append(res)
    if v.get("sizeBytes"):
        parts.append(format_size(v["sizeBytes"]))
    if v.get("bitrateKbps"):
        parts.append(f"{round(v['bitrateKbps'] / 100) / 10:g} Mbps")
    if v.get("videoCodec"):
        parts.append(str(v["videoCodec"]).upper())
    if v.get("audioCodec"):
        parts.append(str(v["audioCodec"]).upper())
    if v.get("watched"):
        parts.append("✓ gesehen")
    tech = " · ".join(parts)
    return f"{name}  —  {tech}" if name else tech


def group_variants(items: list[dict]) -> list[dict]:
    """Fasst Items mit gleicher `metadataId` zu je einer Kachel zusammen."""
    groups: dict[int, dict] = {}
    out: list[dict] = []
    for item in items or []:
        metadata_id = item.get("metadataId") or 0
        if not metadata_id or item.get("variantSplit"):
            out.append(item)
            continue
        current = groups.get(metadata_id)
        if current is None:
            representative = dict(item)
            representative["_variants"] = [item]
            groups[metadata_id] = representative
            out.append(representative)
            continue
        current["_variants"].append(item)
        # Besseren Repräsentanten übernehmen: höher aufgelöst, sonst höhere
        # Bitrate. `_variants` bleibt dabei erhalten (Sammelliste aller Dateien).
        if (item.get("height") or 0, item.get("bitrateKbps") or 0) > (
            current.get("height") or 0,
            current.get("bitrateKbps") or 0,
        ):
            variants = current["_variants"]
            current.clear()
            current.update(item)
            current["_variants"] = variants
    return out
