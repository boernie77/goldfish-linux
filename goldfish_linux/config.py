"""Pfade + persistente Einstellungen (Server-URL, Session-Cookie).

Folgt der XDG Base Directory Specification:
- ~/.config/goldfish-linux/    Einstellungen, Session
- ~/.local/share/goldfish-linux/downloads/   heruntergeladene Videodateien
- ~/.cache/goldfish-linux/posters/           Poster-/Thumbnail-Cache
"""

from __future__ import annotations

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
DOWNLOADS_REGISTRY_FILE = DATA_DIR / "downloads.json"


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
