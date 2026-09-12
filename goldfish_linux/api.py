"""HTTP-Client für die Goldfish-Server-API.

Nutzt `requests.Session` für Cookie-Handling (Login setzt einen HttpOnly-
Session-Cookie `goldfish_session`, siehe internal/api/auth.go im Server-Repo).
Für den nativen Video-Player (GStreamer/GTK4-Media, kein Cookie-Jar) wird
stattdessen der `?session=<token>`-Query-Fallback genutzt, den der Server
ursprünglich für Cast-Receiver (Chromecast/FireTV) bereitstellt — funktioniert
identisch für Stream- und Transcode-Playlist-URLs.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse, urlencode, urlunparse, parse_qsl

import requests
import urllib3.util.connection as _urllib3_connection

# Manche selbstgehosteten Server (Heimnetz hinter Tunnel/Reverse-Proxy)
# brauchen für den ALLERERSTEN Request nach einer Weile Inaktivität spürbar
# länger (beobachtet: ~6s statt <0.3s bei Folge-Requests) — vermutlich ein
# Aufwach-/Verbindungsaufbau-Effekt auf dem Weg zum Server. 20s Timeout +
# ein automatischer Retry deckt das ab, ohne bei einem echt nicht
# erreichbaren Server ewig zu hängen.
DEFAULT_TIMEOUT = 20
RETRY_ON_TIMEOUT = 1


def _force_ipv4_only() -> None:
    """Verbindungen nur noch über IPv4 aufbauen.

    Hintergrund (User-Report 2026-09-12, "Connection reset by peer" beim
    Login): viele selbstgehostete Server laufen unter einem DynDNS-Namen,
    dessen AAAA-Eintrag (IPv6) das dynamische, sich gelegentlich ändernde
    Präfix des Heimrouters trägt. Zeigt der Eintrag gerade auf ein veraltetes
    Präfix, kann die TCP-Verbindung zu einem inzwischen ANDEREN Host
    zustande kommen (Drei-Wege-Handshake erfolgreich!) — die eigentliche
    HTTP-Anfrage wird dort aber sofort per RST abgelehnt. `requests`/urllib3
    implementiert (anders als Browser via "Happy Eyeballs", RFC 8305) keinen
    automatischen IPv4-Fallback in diesem Fall, weil der Verbindungsaufbau
    selbst ja nicht fehlschlug. Verifiziert: die betroffene Domain hatte
    einen AAAA-Eintrag, der von diesem Rechner aus gar nicht erreichbar war.
    Da Goldfish-Server praktisch immer über eine IPv4-Portweiterleitung
    erreichbar sind, ist ein hartes IPv4-only hier der zuverlässigste Fix.
    """
    _urllib3_connection.allowed_gai_family = lambda: socket.AF_INET


_force_ipv4_only()


class GoldfishAPIError(Exception):
    """Fehler bei einem API-Aufruf (Netzwerk, HTTP-Fehlerstatus, ungültiges JSON)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class AuthStatus:
    logged_in: bool
    username: str
    is_admin: bool
    can_download: bool
    setup_needed: bool


class GoldfishClient:
    """Ein Client pro Server-Verbindung. Nicht thread-safe für Login/Logout,
    aber `requests.Session` selbst ist für parallele GET-Requests (z. B.
    Poster-Loader-Threads) unproblematisch, solange kein gleichzeitiger
    Login/Logout läuft."""

    def __init__(self, server_url: str = "", session_token: str = ""):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "Goldfish-Linux/0.1"
        self.base_url = ""
        self.session_token = ""
        if server_url:
            self.set_server(server_url, session_token)

    def set_server(self, server_url: str, session_token: str = "") -> None:
        self.base_url = server_url.rstrip("/")
        if session_token:
            self.set_session_token(session_token)

    def set_session_token(self, token: str) -> None:
        """Setzt den Session-Cookie manuell (z. B. beim Wiederherstellen einer
        gespeicherten Session aus config.Settings, ohne erneutes Login)."""
        self.session_token = token
        host = urlparse(self.base_url).hostname or ""
        self.session.cookies.set("goldfish_session", token, domain=host)

    # -- niedrige Ebene ----------------------------------------------------

    def _url(self, path: str) -> str:
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = self._url(path)
        timeout = kwargs.pop("timeout", DEFAULT_TIMEOUT)
        resp = None
        last_timeout_exc: requests.Timeout | None = None
        for attempt in range(RETRY_ON_TIMEOUT + 1):
            try:
                resp = self.session.request(method, url, timeout=timeout, **kwargs)
                break
            except requests.Timeout as exc:
                last_timeout_exc = exc
                if attempt < RETRY_ON_TIMEOUT:
                    time.sleep(0.5)
                continue
            except requests.RequestException as exc:
                raise GoldfishAPIError(f"Verbindung fehlgeschlagen: {exc}") from exc
        if resp is None:
            raise GoldfishAPIError(
                f"Verbindung fehlgeschlagen (auch nach erneutem Versuch keine Antwort "
                f"innerhalb von {timeout}s): {last_timeout_exc}"
            ) from last_timeout_exc
        if resp.status_code >= 400:
            message = resp.reason
            try:
                body = resp.json()
                message = body.get("error") or body.get("message") or message
            except ValueError:
                pass
            raise GoldfishAPIError(message, status=resp.status_code)
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.content

    def get(self, path: str, params: dict | None = None) -> Any:
        return self._request("GET", path, params=params)

    def put(self, path: str, json_body: dict | None = None) -> Any:
        return self._request("PUT", path, json=json_body or {})

    def post(self, path: str, json_body: dict | None = None) -> Any:
        return self._request("POST", path, json=json_body or {})

    # -- Auth ----------------------------------------------------------

    def login(self, server_url: str, username: str, password: str) -> AuthStatus:
        self.set_server(server_url)
        self.post("/api/auth/login", {"username": username, "password": password})
        # Server setzt den Cookie via Set-Cookie-Header automatisch im
        # requests.Session-Jar — den Token für die spätere ?session=-Nutzung
        # (Player) trotzdem herausziehen.
        token = self.session.cookies.get("goldfish_session")
        if token:
            self.session_token = token
        return self.status()

    def status(self) -> AuthStatus:
        data = self.get("/api/auth/status") or {}
        return AuthStatus(
            logged_in=bool(data.get("loggedIn")),
            username=data.get("username", ""),
            is_admin=bool(data.get("isAdmin")),
            can_download=bool(data.get("canDownload")),
            setup_needed=bool(data.get("setup")),
        )

    def logout(self) -> None:
        try:
            self.post("/api/auth/logout")
        except GoldfishAPIError:
            pass

    # -- Bibliotheken / Navigation ---------------------------------------

    def libraries(self) -> list[dict]:
        return self.get("/api/libraries") or []

    def folders(self, library_id: int, parent: str = "") -> list[dict]:
        return self.get(f"/api/libraries/{library_id}/folders", {"parent": parent}) or []

    def items(
        self,
        library_id: int | None = None,
        folder: str = "",
        search: str = "",
        sort: str = "title",
        sort_dir: str = "",
        watched: str = "",
        favorite: str = "",
    ) -> list[dict]:
        params: dict[str, str] = {"sort": sort}
        if library_id:
            params["libraryId"] = str(library_id)
        if folder:
            params["folder"] = folder
        if search:
            params["search"] = search
        if sort_dir:
            params["dir"] = sort_dir
        if watched:
            params["watched"] = watched
        if favorite:
            params["favorite"] = favorite
        return self.get("/api/items", params) or []

    def item(self, item_id: int) -> dict:
        return self.get(f"/api/items/{item_id}") or {}

    # -- Wiedergabe -----------------------------------------------------

    def playback_info(self, item_id: int, mode: str = "auto", profile: str = "orig") -> dict:
        params = {"mode": mode, "profile": profile}
        return self.get(f"/api/playback/{item_id}", params) or {}

    def playback_start(self, item_id: int) -> None:
        try:
            self.post(f"/api/playback/{item_id}/start")
        except GoldfishAPIError:
            pass  # Protokoll ist Komfort-Feature, kein Blocker fürs Abspielen

    def playback_stop(self, item_id: int, reason: str, position_sec: float, duration_sec: float) -> None:
        try:
            self.post(
                f"/api/playback/{item_id}/stop",
                {"reason": reason, "positionSec": position_sec, "durationSec": duration_sec},
            )
        except GoldfishAPIError:
            pass

    def playback_error(self, item_id: int, message: str) -> None:
        try:
            self.post(f"/api/playback/{item_id}/error", {"message": message[:300]})
        except GoldfishAPIError:
            pass

    def set_watched(self, item_id: int, watched: bool) -> None:
        self.put(f"/api/items/{item_id}/watched", {"watched": watched})

    def set_favorite(self, item_id: int, favorite: bool) -> None:
        self.put(f"/api/items/{item_id}/favorite", {"favorite": favorite})

    # -- URLs -------------------------------------------------------------

    def with_session_param(self, url_or_path: str) -> str:
        """Hängt `?session=<token>` an eine (ggf. bereits relative) URL an —
        Fallback-Auth für den nativen Player (GStreamer/GTK.MediaFile trägt
        keine Cookies). Bereits vorhandene Query-Parameter bleiben erhalten,
        `session` wird ergänzt/überschrieben."""
        full = url_or_path
        if full.startswith("/"):
            full = self._url(full)
        parts = urlparse(full)
        query = dict(parse_qsl(parts.query))
        query["session"] = self.session_token
        new_query = urlencode(query)
        return urlunparse(parts._replace(query=new_query))

    def poster_path_for_item(self, item: dict) -> str | None:
        """Poster-Pfad analog zur Web-UI (cards.js): TMDB/Custom-Metadata
        zuerst, sonst das eigene Item-Thumbnail (Private-Libs, unmatched)."""
        metadata_id = item.get("metadataId")
        if metadata_id:
            return f"/api/poster/metadata/{metadata_id}"
        if item.get("hasThumb"):
            return f"/api/thumb/{item['id']}"
        return None

    def absolute(self, path: str) -> str:
        return self._url(path)

    def download_response(self, item_id: int) -> requests.Response:
        """Startet einen Streaming-Download der Originaldatei. Aufrufer muss
        `resp.close()` sicherstellen (via `with` oder try/finally)."""
        url = self._url(f"/api/download/{item_id}")
        try:
            resp = self.session.get(url, stream=True, timeout=30)
        except requests.RequestException as exc:
            raise GoldfishAPIError(f"Download fehlgeschlagen: {exc}") from exc
        if resp.status_code >= 400:
            resp.close()
            raise GoldfishAPIError(f"Download fehlgeschlagen: HTTP {resp.status_code}", status=resp.status_code)
        return resp

    def fetch_bytes(self, path: str) -> bytes | None:
        """Für Poster/Thumbnails: kleine Bilder synchron laden (wird vom
        Aufrufer aus einem Hintergrund-Thread heraus benutzt)."""
        url = self._url(path)
        try:
            resp = self.session.get(url, timeout=10)
        except requests.RequestException:
            return None
        if resp.status_code != 200:
            return None
        return resp.content
