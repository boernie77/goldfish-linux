"""Ordner-Sammlungen und Ermittler-Katalog (Server 1.4.57–1.4.68).

Reine Hilfsfunktionen ohne GTK, damit sie sich ohne Oberfläche prüfen lassen
(`tests/test_catalog.py`). Die Regeln sind dieselben wie im Browser:

* **Kommissar-Zeile** (`cards.js`, `episodeGroup`): eine Folge, deren Pfad aus
  mindestens drei Segmenten besteht und deren zweites Segment KEIN
  Staffel-Ordner ist, trägt dieses Segment als eigene Zeile — beim Tatort der
  Kommissar (`Tatort/Batic und Leitmayr/…`). Normale Serien mit
  `Staffel 1/`-Ordnern bleiben unberührt.
* **Fehlende Folgen** (`views.js`, `applyCatalogGaps`): die Platzhalter werden
  bei „Veröffentlicht, älteste zuerst" nach `metadata.releaseDate` in die
  Zeitleiste eingereiht, sonst hinten angehängt.
"""

from __future__ import annotations

import re

# Dieselbe Regel wie im Browser — bewusst wortgleich, damit alle Clients
# dieselben Zwischenordner als „Kommissar" erkennen.
_SEASON_DIR_RE = re.compile(r"^(staffel|season|serie|s)\s*\d+$|^specials?$|^extras?$", re.IGNORECASE)

# Kennzeichen eines Platzhalters im Raster (kein echtes Item, keine ID).
MISSING_KEY = "_catalogMissing"
TEAM_KEY = "_catalogTeam"


def episode_group(item: dict) -> str:
    """Der Zwischenordner einer Folge (Tatort: der Kommissar), sonst ""."""
    metadata = item.get("metadata") or {}
    if metadata.get("tmdbType") != "episode":
        return ""
    rel = (item.get("relPath") or "").split("/")
    if len(rel) < 3:
        return ""
    segment = rel[1].strip()
    if not segment or _SEASON_DIR_RE.match(segment):
        return ""
    return rel[1]


def episode_group_folder(item: dict) -> str:
    """Der Ordnerpfad zur Kommissar-Zeile (`<seg1>/<seg2>`), sonst ""."""
    if not episode_group(item):
        return ""
    rel = (item.get("relPath") or "").split("/")
    return f"{rel[0]}/{rel[1]}"


def in_forced_folder(folder: str, forced_root: str | None) -> bool:
    """Liegt `folder` in der erzwungenen Ordner-Ansicht `forced_root`?

    Nur dort gilt der Katalog — genau wie im Browser (`catalogContext`)."""
    if not forced_root or not folder:
        return False
    return folder == forced_root or folder.startswith(forced_root + "/")


def is_team_folder(folder: str) -> bool:
    """Ein Unterordner (`Tatort/Batic und Leitmayr`) statt der Serienwurzel."""
    return "/" in (folder or "").strip("/")


def format_catalog_date(date: str | None) -> str:
    """`1990-02-04` → `04.02.1990` (Erstausstrahlung aus dem Katalog)."""
    if not date or len(date) < 10:
        return date or ""
    return f"{date[8:10]}.{date[5:7]}.{date[:4]}"


def missing_placeholders(missing: list[dict]) -> list[dict]:
    """Katalog-Einträge als Platzhalter-Dicts fürs Raster."""
    return [dict(entry, **{MISSING_KEY: True}) for entry in (missing or [])]


def team_placeholders(groups: list[dict]) -> list[dict]:
    """„Ermittler ohne eigenen Ordner": nur Gruppen ohne `folder`."""
    return [dict(g, **{TEAM_KEY: True}) for g in (groups or []) if not g.get("folder")]


def _item_date(item: dict) -> str:
    metadata = item.get("metadata") or {}
    return str(metadata.get("releaseDate") or "")[:10]


def merge_missing(items: list[dict], missing: list[dict], chronological: bool) -> list[dict]:
    """Platzhalter in die Liste einreihen.

    Chronologisch: jeder Platzhalter kommt vor das erste Item mit einem
    späteren Datum (Items ohne Datum zählen nicht als Anker). Sonst — oder
    wenn kein späteres Item existiert — ans Ende."""
    if not missing:
        return list(items)
    if not chronological:
        return list(items) + list(missing)
    result = list(items)
    tail: list[dict] = []
    for entry in missing:
        date = entry.get("date") or ""
        pos = None
        if date:
            for i, it in enumerate(result):
                if it.get(MISSING_KEY):
                    continue
                d = _item_date(it)
                if d and d > date:
                    pos = i
                    break
        if pos is None:
            tail.append(entry)
        else:
            result.insert(pos, entry)
    return result + tail


def catalog_note(data: dict | None) -> str:
    """Zusatz für die Zählzeile: „owned/total Folgen vorhanden"."""
    if not data or not data.get("available") or not data.get("total"):
        return ""
    return f"{int(data.get('owned') or 0)}/{int(data['total'])} Folgen vorhanden"


def file_count_label(count: int) -> str:
    """Zähler einer Ordner-Sammlung: „N Dateien" (wie der Browser)."""
    return f"{count} Datei" if count == 1 else f"{count} Dateien"
