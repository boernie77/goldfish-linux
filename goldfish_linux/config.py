"""Pfade + persistente Einstellungen (Server-URL, Session-Cookie).

Folgt der XDG Base Directory Specification:
- ~/.config/goldfish-linux/    Einstellungen, Session
- ~/.local/share/goldfish-linux/downloads/   heruntergeladene Videodateien
- ~/.cache/goldfish-linux/posters/           Poster-/Thumbnail-Cache
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def _xdg(env_var: str, default: str) -> Path:
    base = os.environ.get(env_var)
    if base:
        return Path(base)
    return Path.home() / default


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "goldfish-linux"
DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / "goldfish-linux"
CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache") / "goldfish-linux"
DOWNLOADS_DIR = DATA_DIR / "downloads"
POSTER_CACHE_DIR = CACHE_DIR / "posters"

SETTINGS_FILE = CONFIG_DIR / "settings.json"
VIEW_PREFS_FILE = CONFIG_DIR / "view_prefs.json"
LOCAL_LIBRARIES_FILE = CONFIG_DIR / "local_libraries.json"
DOWNLOADS_REGISTRY_FILE = DATA_DIR / "downloads.json"


def poster_cache_file(key: str):
    """Ablageort eines Bildes im Zwischenspeicher, benannt nach dem Hash
    seiner Quelle.

    Gemeinsam genutzt von `widgets/poster.py` (das die Bilder anzeigt) und
    `local_library.py` (das Vorschaubilder eigener Dateien im Vorgriff
    erzeugt) — beide müssen denselben Namen berechnen, sonst legt das eine ab,
    was das andere nie findet."""
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return POSTER_CACHE_DIR / f"{digest}.img"


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, DATA_DIR, CACHE_DIR, DOWNLOADS_DIR, POSTER_CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)


class Settings:
    """Einfache JSON-Datei mit Server-URL + Session-Token.

    Das Passwort selbst wird NIE gespeichert — nur der Session-Cookie-Wert,
    den der Server beim Login vergibt (gleiche Session-TTL wie im Browser).
    Ist die Session beim nächsten Start abgelaufen, zeigt die App wieder den
    Login-Dialog.
    """

    def __init__(self) -> None:
        self.server_url: str = ""
        self.session_token: str = ""
        self.username: str = ""
        self._load()

    def _load(self) -> None:
        if not SETTINGS_FILE.exists():
            return
        try:
            data = json.loads(SETTINGS_FILE.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.server_url = data.get("serverUrl", "")
        self.session_token = data.get("sessionToken", "")
        self.username = data.get("username", "")

    def save(self) -> None:
        ensure_dirs()
        data = {
            "serverUrl": self.server_url,
            "sessionToken": self.session_token,
            "username": self.username,
        }
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), "utf-8")

    def clear_session(self) -> None:
        self.session_token = ""
        self.save()


class ViewPrefs:
    """Gemerkte Sortierung pro Bibliothek und Ordner.

    Entspricht den `sort:lib:<id>:<folder>`-Einträgen des Browsers. Filter
    werden bewusst NICHT gemerkt — auch der Browser setzt sie beim Wechsel der
    Bibliothek zurück, sonst sucht man später vergeblich nach Titeln, die ein
    vergessener Filter ausblendet.

    **Fallstrick, den der Browser teuer gelernt hat** (siehe dortigen Kommentar
    zu `FLAT_LIBRARY_SORTS`): eine flache Sortierung darf nur dort gemerkt
    werden, wo ohnehin keine Ordnerkacheln stehen — also in einem normalen
    Unterordner. Würde man sie in der Bibliothekswurzel oder einem
    Drilldown-Ordner merken, wären die Ordnerkacheln beim nächsten Öffnen
    dauerhaft verschwunden, ohne erkennbaren Grund.
    """

    def __init__(self) -> None:
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        if not VIEW_PREFS_FILE.exists():
            return
        try:
            self._data = json.loads(VIEW_PREFS_FILE.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            self._data = {}

    def _save(self) -> None:
        ensure_dirs()
        try:
            VIEW_PREFS_FILE.write_text(json.dumps(self._data, indent=2), "utf-8")
        except OSError:
            pass  # Merken ist Komfort, kein Muss

    @staticmethod
    def _key(library_id: int, folder: str) -> str:
        return f"sort:lib:{library_id}:{folder}"

    def get_sort(self, library_id: int, folder: str) -> tuple[str, bool | None] | None:
        entry = self._data.get(self._key(library_id, folder))
        if not isinstance(entry, dict) or "sort" not in entry:
            return None
        return entry["sort"], entry.get("ascending")

    def set_sort(self, library_id: int, folder: str, sort: str, ascending: bool | None) -> None:
        self._data[self._key(library_id, folder)] = {"sort": sort, "ascending": ascending}
        self._save()

    @staticmethod
    def _season_key(library_id: int, folder: str) -> str:
        return f"seasonView:{library_id}:{folder}"

    def season_view(self, library_id: int, folder: str) -> bool:
        """Ob dieser Serienordner die Staffelansicht zeigt.

        Standard ist an, wie im Browser. Ein ausdrückliches Aus entsteht, wenn
        der Server für den Ordner keine Staffelstruktur liefert — dann wäre
        die Ansicht eine Sackgasse."""
        return bool(self._data.get(self._season_key(library_id, folder), True))

    def set_season_view(self, library_id: int, folder: str, enabled: bool) -> None:
        self._data[self._season_key(library_id, folder)] = enabled
        self._save()

    def clear_season_view(self, library_id: int, folder: str) -> None:
        """Merker verwerfen — nötig, sobald der Ordner zugeordnet wurde und
        Staffeln vorhanden sein könnten. Der Browser hatte hier lange einen
        Fehler: ein einmal gesetztes Aus blieb für immer stehen, und die
        Serie zeigte selbst nach einer korrekten Zuordnung nie Staffeln."""
        if self._data.pop(self._season_key(library_id, folder), None) is not None:
            self._save()

    def alpha_sidebar(self) -> bool:
        """Ob die Buchstabenleiste erscheint. Standard: ja."""
        return bool(self._data.get("alphaSidebar", True))

    def set_alpha_sidebar(self, enabled: bool) -> None:
        self._data["alphaSidebar"] = enabled
        self._save()

    def autoplay_next(self) -> bool:
        """Ob am Ende einer Serienfolge die nächste Folge angeboten wird.

        Standard: AUS. Die Option ist bewusst rein lokal (Gerät) und nicht
        serverseitig — wie `colorScheme` betrifft sie das Verhalten DIESES
        Geräts, und der Server kennt keine solche Einstellung.
        """
        return bool(self._data.get("autoplayNext", False))

    def set_autoplay_next(self, enabled: bool) -> None:
        self._data["autoplayNext"] = bool(enabled)
        self._save()

    def local_in_sidebar(self, key: str) -> bool:
        """Ob eine eigene Bibliothek (Datenträger oder Sammlung) in der
        Seitenleiste erscheint. Standard: ja.

        Bewusst hier und nicht auf dem Server: eigene Datenträger kennt der
        Server nicht, ihre Sichtbarkeit kann also nur lokal gemerkt werden —
        anders als bei den Server-Bibliotheken, wo `user_nav_prefs` gilt."""
        return self._data.get(f"localNav:{key}", True) is not False

    def set_local_in_sidebar(self, key: str, visible: bool) -> None:
        self._data[f"localNav:{key}"] = visible
        self._save()

    def clear_sort(self, library_id: int, folder: str) -> None:
        if self._data.pop(self._key(library_id, folder), None) is not None:
            self._save()

    def music_view_mode(self, library_id: int) -> str:
        """"grid" | "list" | "all" (Alben als Kacheln, Alben als Liste, oder
        alle Titel der Bibliothek flach) — pro Bibliothek gemerkt, analog zum
        Browser/den anderen Clients. Standard: "grid"."""
        value = self._data.get(f"musicView:{library_id}", "grid")
        return value if value in ("grid", "list", "all") else "grid"

    def set_music_view_mode(self, library_id: int, mode: str) -> None:
        self._data[f"musicView:{library_id}"] = mode if mode in ("grid", "list", "all") else "grid"
        self._save()

    def music_column_layout(self, context: str) -> dict:
        """Spaltenreihenfolge, -breiten und -sichtbarkeit einer Musikliste,
        gemerkt pro Kontext ("albums"/"allTracks"/"albumTracks") — dasselbe
        Muster wie `MUSIC_LIST_CONTEXTS` im Browser, wo `{order, widths}` je
        Kontext im localStorage liegen.

        Ein fehlender Eintrag heißt "noch nie angefasst": dann entscheidet die
        Spaltenbeschreibung, was erscheint. Ein leeres `visible` ist etwas
        anderes — der Benutzer hat dann alles abgewählt."""
        raw = self._data.get(f"musicColumns:{context}")
        if not isinstance(raw, dict):
            return {}
        layout: dict = {}
        if isinstance(raw.get("order"), list):
            layout["order"] = [str(k) for k in raw["order"]]
        if isinstance(raw.get("widths"), dict):
            layout["widths"] = {str(k): int(v) for k, v in raw["widths"].items() if isinstance(v, (int, float))}
        if isinstance(raw.get("visible"), list):
            layout["visible"] = [str(k) for k in raw["visible"]]
        return layout

    def set_music_column_layout(self, context: str, *, order: list[str], widths: dict[str, int], visible: list[str]) -> None:
        self._data[f"musicColumns:{context}"] = {
            "order": list(order),
            "widths": {k: int(v) for k, v in widths.items()},
            "visible": list(visible),
        }
        self._save()

    def playlist_view_mode(self, playlist_id: int) -> str:
        """"grid" | "list" — wie eine Musik-Playlist gezeigt wird (Kacheln
        oder Titelliste mit Spalten). Standard: "grid"."""
        value = self._data.get(f"playlistView:{playlist_id}", "grid")
        return value if value in ("grid", "list") else "grid"

    def set_playlist_view_mode(self, playlist_id: int, mode: str) -> None:
        self._data[f"playlistView:{playlist_id}"] = mode if mode in ("grid", "list") else "grid"
        self._save()

    def music_filter(self, library_id: int) -> dict:
        """Gemerkte Musik-Filter (nur Favoriten, Genres) je Bibliothek."""
        raw = self._data.get(f"musicFilter:{library_id}")
        if not isinstance(raw, dict):
            return {}
        return {
            "favorites": bool(raw.get("favorites")),
            "genres": [str(g) for g in raw.get("genres") or []],
        }

    def set_music_filter(self, library_id: int, *, favorites: bool, genres: list[str]) -> None:
        self._data[f"musicFilter:{library_id}"] = {"favorites": favorites, "genres": sorted(genres)}
        self._save()

    def color_scheme(self) -> str:
        """"system" | "light" | "dark". Standard: "system".

        Bewusst hier und nicht serverseitig: das betrifft die Darstellung auf
        DIESEM Gerät, nicht den Goldfish-Account. Manche Desktops (z. B.
        Cinnamon auf Linux Mint) melden ihre Dunkelmodus-Einstellung nicht
        über das Portal, das libadwaita sonst automatisch abfragt — ohne
        diesen Schalter bliebe die App dort für immer hell, egal was das
        System-Theme sagt."""
        value = self._data.get("colorScheme", "system")
        return value if value in ("system", "light", "dark") else "system"

    def set_color_scheme(self, value: str) -> None:
        self._data["colorScheme"] = value if value in ("system", "light", "dark") else "system"
        self._save()
