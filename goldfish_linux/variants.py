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
