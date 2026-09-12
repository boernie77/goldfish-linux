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


@dataclass
class PlaybackProfile:
    """Eine Qualitätsstufe. `max_height == 0` heißt „Original, kein Deckel"."""

    id: str
    label: str
    max_height: int
    video_kbps: int
    audio_kbps: int

    @classmethod
    def from_json(cls, raw: dict) -> PlaybackProfile:
        # Groß geschriebene Schlüssel, weil das Go-Struct keine JSON-Tags hat;
        # die camelCase-Varianten trotzdem mitgelesen, falls der Server sie
        # irgendwann nachrüstet.
        def pick(*keys: str, default: Any = 0) -> Any:
            for key in keys:
                if key in raw:
                    return raw[key]
            return default

        return cls(
            id=str(pick("ID", "id", default="")),
            label=str(pick("Label", "label", default="")),
            max_height=int(pick("MaxHeight", "maxHeight") or 0),
            video_kbps=int(pick("VideoKbps", "videoKbps") or 0),
            audio_kbps=int(pick("AudioKbps", "audioKbps") or 0),
        )


@dataclass
class TrickplayCue:
    """Ein Vorschaubild im Sprite-Sheet: Zeitfenster plus Ausschnitt."""

    start: float
    end: float
    x: int
    y: int
    width: int
    height: int


def _parse_vtt_timestamp(raw: str) -> float | None:
    """`HH:MM:SS.mmm` oder `MM:SS.mmm` in Sekunden."""
    parts = raw.strip().split(":")
    if not 2 <= len(parts) <= 3:
        return None
    try:
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + float(part)
    except ValueError:
        return None
    return seconds


def parse_trickplay_vtt(text: str) -> list[TrickplayCue]:
    """Parst das Sprite-Manifest des Servers (`internal/trickplay/worker.go`,
    `writeVTT`): auf eine Zeitzeile `HH:MM:SS.mmm --> HH:MM:SS.mmm` folgt eine
    Zeile `sprite.jpg#xywh=x,y,w,h`.

    Bewusst über die tatsächlichen Werte geparst statt aus Intervall und
    Rasterbreite nachgerechnet — so übersteht der Client eine künftige
    Formatänderung auf Serverseite, ohne angefasst zu werden."""
    cues: list[TrickplayCue] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "-->" not in line:
            i += 1
            continue
        halves = line.split("-->")
        start = _parse_vtt_timestamp(halves[0]) if len(halves) == 2 else None
        end = _parse_vtt_timestamp(halves[1]) if len(halves) == 2 else None
        # Nächste nicht-leere Zeile trägt den Ausschnitt.
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if start is not None and end is not None and j < len(lines) and "xywh=" in lines[j]:
            try:
                nums = lines[j].split("xywh=", 1)[1].strip().split(",")
                if len(nums) == 4:
                    x, y, w, h = (int(float(n)) for n in nums)
                    cues.append(TrickplayCue(start=start, end=end, x=x, y=y, width=w, height=h))
            except (ValueError, IndexError):
                pass  # einzelne kaputte Zeile überspringen, Rest bleibt nutzbar
        i = j + 1
    return cues


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

    def get(self, path: str, params: dict | None = None, timeout: float | None = None) -> Any:
        kwargs: dict = {"params": params}
        if timeout is not None:
            kwargs["timeout"] = timeout
        return self._request("GET", path, **kwargs)

    def put(self, path: str, json_body: dict | None = None) -> Any:
        return self._request("PUT", path, json=json_body or {})

    def post(self, path: str, json_body: dict | None = None) -> Any:
        return self._request("POST", path, json=json_body or {})

    def delete(self, path: str, json_body: dict | None = None) -> Any:
        return self._request("DELETE", path, json=json_body or {})

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

    def status(self, timeout: float | None = None) -> AuthStatus:
        data = self.get("/api/auth/status", timeout=timeout) or {}
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
        buckets: list[str] | None = None,
        genres: list[str] | None = None,
        person_id: int | None = None,
        match: str = "",
    ) -> list[dict]:
        """Die Hauptliste. Alle Filter sind optional und werden serverseitig
        mit UND verknüpft; `buckets` (Auflösung) und `genres` sind innerhalb
        ihrer Gruppe ODER-verknüpft, weil der Server sie als wiederholten
        `bucket=`/`genre=`-Parameter erwartet.

        `person_id` ist eine TMDB-Personen-ID und macht die Liste
        bibliotheksübergreifend — alles, worin diese Person mitspielt."""
        params: dict[str, Any] = {"sort": sort}
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
        if buckets:
            params["bucket"] = buckets
        if genres:
            params["genre"] = genres
        if person_id:
            params["personId"] = str(person_id)
        if match:
            params["match"] = match
        return self.get("/api/items", params) or []

    def item(self, item_id: int) -> dict:
        return self.get(f"/api/items/{item_id}") or {}

    # -- Wiedergabe -----------------------------------------------------

    def playback_info(self, item_id: int, mode: str = "auto", profile: str = "orig") -> dict:
        """Streams, Untertitel und die Wiedergabe-URL für ein Item.

        Auch die Datenquelle für die Ton- und Untertitelauswahl: `item.streams`
        allein kennt die erzeugten KI- und OCR-Untertitel nicht, dieser
        Endpoint schon. `profile` wirkt im Auto-Modus als Qualitätsdeckel und
        erzwingt eine Umwandlung, wenn das Item ihn überschreitet."""
        params = {"mode": mode, "profile": profile}
        return self.get(f"/api/playback/{item_id}", params) or {}

    def playback_profiles(self, item_id: int) -> list[PlaybackProfile]:
        """Die wählbaren Qualitätsstufen für dieses Item.

        ACHTUNG, Server-Eigenheit: `playback.Profile` trägt im Go-Code KEINE
        JSON-Tags, die Felder kommen deshalb groß geschrieben an (`ID`,
        `Label`, `MaxHeight`, `VideoKbps`, `AudioKbps`) — anders als jedes
        andere Feld der API, das camelCase ist. Hier einmal zentral in eine
        Dataclass normalisiert, damit die Oberfläche nicht darüber stolpert."""
        info = self.playback_info(item_id)
        return [PlaybackProfile.from_json(p) for p in (info.get("profiles") or [])]

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

    # -- Items: Varianten, Zufall, Staffeln -------------------------------

    def variants(self, item_id: int) -> list[dict]:
        """Alle Geschwister-Dateien mit derselben `metadataId` (inkl. `item_id`
        selbst) — Datenquelle für die Varianten-Auswahl im Detail-Dialog.

        Bewusst dieser Endpoint und NICHT die gerade geladene Grid-Liste: nur
        er ist bibliotheksübergreifend vollständig (siehe CLAUDE.md
        „Merge-Duplikate", dritter Bugfix — der Browser hatte genau deshalb
        einen zu kurzen Varianten-Dropdown)."""
        return self.get(f"/api/items/{item_id}/variants") or []

    def random_item(
        self,
        library_id: int | None = None,
        library_ids: list[int] | None = None,
        folder_selections: list[tuple[int, str]] | None = None,
        folder: str = "",
        search: str = "",
        playlist_id: int | None = None,
        album_id: int | None = None,
        person_id: int | None = None,
    ) -> dict:
        """Ein zufälliges Item. Die Pool-Auswahl folgt der Prioritätenkette aus
        CLAUDE.md („Shuffle-Play"): Playlist, Person, Album, manuelle
        Ordner-Auswahl, sonst Bibliothek.

        `folder_selections` sind `(library_id, rel_path)`-Paare und werden als
        wiederholter `folderSel=<libId>:<relPath>`-Parameter geschickt —
        damit lässt sich der Zufall über mehrere Ordner UND Bibliotheken
        hinweg einschränken. `rel_path=""` meint die ganze Bibliothek."""
        params: dict[str, Any] = {}
        if playlist_id:
            params["playlistId"] = str(playlist_id)
        elif person_id:
            params["personId"] = str(person_id)
        elif album_id:
            params["albumId"] = str(album_id)
        elif folder_selections:
            params["folderSel"] = [f"{lib}:{rel}" for lib, rel in folder_selections]
        elif library_ids:
            params["libraryId"] = [str(i) for i in library_ids]
        elif library_id:
            params["libraryId"] = str(library_id)
        if folder:
            params["folder"] = folder
        if search:
            params["search"] = search
        return self.get("/api/items/random", params) or {}

    def seasons(self, library_id: int, folder: str, refresh: bool = False) -> dict:
        """Staffel-Struktur eines Serien-Ordners: `{show, seasons: [...]}`.

        Ein leeres `seasons`-Array ist ein regulärer Fall, kein Fehler — der
        Ordner hat dann keine TMDB-Staffel-Struktur (z. B. Tatort mit
        Kommissar-Unterordnern). Aufrufer sollen dann auf die normale
        Ordner-Ansicht zurückfallen, wie der Browser es tut."""
        params: dict[str, str] = {"folder": folder}
        if refresh:
            params["refresh"] = "true"
        return self.get(f"/api/libraries/{library_id}/seasons", params) or {}

    def genres(self, library_id: int) -> list[str]:
        """Trefferliste für den Genre-Filter, serverseitig pro Bibliothek
        gescoped: Filme/Serien aus `metadata.genres`, Musik aus `items.genre`.
        Privat-Bibliotheken liefern immer eine leere Liste."""
        data = self.get(f"/api/libraries/{library_id}/genres") or {}
        return data.get("genres") or []

    def home(self) -> dict:
        """Startseiten-Streifen: `{sections: [{library, continue, nextUp,
        recent}], showContinue, showNextUp}` — bereits serverseitig nach der
        effektiven Benutzer-Reihenfolge sortiert und ACL-gefiltert."""
        return self.get("/api/home") or {}

    # -- Metadaten: Besetzung, Trailer, Personen --------------------------

    def cast(self, metadata_id: int) -> list[dict]:
        """Besetzung zu einer Metadata-ID. Wichtig: der Endpoint arbeitet auf
        `metadata_id`, NICHT auf `item_id` (dieselbe Konvention, die auch die
        Android-App kennt). Bei Episoden liefert der Server automatisch
        Show-Hauptcast plus Episoden-Gäste."""
        return self.get(f"/api/metadata/{metadata_id}/cast") or []

    def trailer(self, metadata_id: int) -> dict | None:
        """YouTube-Trailer-Info für einen Film. 404 ist der Normalfall (kein
        Trailer gefunden, TMDB aus, oder keine Film-Metadata) und kommt hier
        als `None` zurück — Aufrufer blenden den Button dann einfach aus."""
        try:
            return self.get(f"/api/metadata/{metadata_id}/trailer") or None
        except GoldfishAPIError as exc:
            if exc.status == 404:
                return None
            raise

    def trailer_stream_path(self, metadata_id: int) -> str | None:
        """Lässt den Server den Trailer per yt-dlp herunterladen und zu EINER
        MP4 muxen; liefert den relativen Pfad zur fertigen Datei.

        Für den GTK-Player die richtige Wahl gegenüber dem iframe-Embed des
        Browsers: `Gtk.Video` braucht eine einzelne Datei-URL. Der Aufruf
        blockiert, solange der Download läuft (Trailer sind kurz), und kann
        mit 502 fehlschlagen — dann `None`, und der Aufrufer fällt auf
        „extern öffnen" zurück."""
        data = self.get(f"/api/metadata/{metadata_id}/trailer-stream") or {}
        return data.get("url") or None

    def person(self, tmdb_id: int) -> dict:
        """Bio-Daten und vollständige Filmografie einer Person, live von TMDB
        (serverseitig gecacht). Fällt serverseitig auf den lokalen
        `people`-Eintrag zurück, wenn TMDB aus ist."""
        return self.get(f"/api/person/{tmdb_id}") or {}

    def person_profile_path(self, tmdb_id: int) -> str:
        return f"/api/person/{tmdb_id}/profile"

    # -- Fortsetz-Position -------------------------------------------------

    def get_resume(self, item_id: int) -> float:
        """Gespeicherte Wiedergabeposition in Sekunden, 0 wenn keine.

        Eigener Endpoint, weil `resumePosSec` bewusst NICHT in der
        `/api/items/{id}`-Antwort steckt (der `GetItemFor`-Query listet die
        Spalte nicht auf) — dieselbe Konvention, auf die sich auch die
        Android-App verlässt."""
        data = self.get(f"/api/items/{item_id}/resume") or {}
        try:
            return float(data.get("positionSec") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def set_resume(self, item_id: int, position_sec: float) -> None:
        self.put(f"/api/items/{item_id}/resume", {"positionSec": position_sec})

    # -- Musik -------------------------------------------------------------

    def albums(self, library_id: int, genres: list[str] | None = None) -> list[dict]:
        """Album-Kacheln einer Musik-Bibliothek. Eigener Endpoint statt des
        generischen `/api/items` — die kanonische Album-Gruppierung entsteht
        serverseitig aus `GroupMusicAlbums`, nicht aus der Ordnerstruktur."""
        params: dict[str, Any] = {}
        if genres:
            params["genre"] = genres
        return self.get(f"/api/libraries/{library_id}/albums", params) or []

    def album(self, album_id: int) -> dict:
        """Album-Detail mit Titelliste, serverseitig nach `track_no` sortiert."""
        return self.get(f"/api/albums/{album_id}") or {}

    def set_album_favorite(self, album_id: int, favorite: bool) -> None:
        self.put(f"/api/albums/{album_id}/favorite", {"favorite": favorite})

    def album_cover_path(self, album_id: int) -> str:
        """Cover-Pfad. Fehlt ein Cover, antwortet der Server mit einem Redirect
        auf `/placeholder.svg` — `requests` folgt dem automatisch."""
        return f"/api/poster/album/{album_id}"

    # -- Sammlungen --------------------------------------------------------

    def collections(self) -> list[dict]:
        return self.get("/api/collections") or []

    def collection_parts(self, collection_id: int) -> list[dict]:
        """Alle Teile einer Sammlung, vorhandene wie fehlende. Ein nicht
        vorhandener Teil trägt `owned: false` — auch dann, wenn die Datei
        existiert, der Benutzer aber keinen Zugriff darauf hat (der Server
        verschweigt den Unterschied bewusst)."""
        return self.get(f"/api/collections/{collection_id}/items") or []

    def hide_collection_part(self, collection_id: int, tmdb_movie_id: int) -> None:
        self.post(f"/api/collections/{collection_id}/parts/{tmdb_movie_id}/hide")

    def unhide_collection_part(self, collection_id: int, tmdb_movie_id: int) -> None:
        self.delete(f"/api/collections/{collection_id}/parts/{tmdb_movie_id}/hide")

    def collection_poster_path(self, collection_id: int) -> str:
        return f"/api/poster/collection/{collection_id}"

    # -- Playlists ---------------------------------------------------------

    def playlists(self, kind: str = "video") -> list[dict]:
        """Playlists des angemeldeten Benutzers. `kind` trennt Video und Musik
        strikt (Server: `playlists.kind`) — ein Musiktitel kann nicht in eine
        Video-Playlist wandern. `kind=""` liefert beide."""
        params = {"kind": kind} if kind else None
        return self.get("/api/playlists", params) or []

    def create_playlist(self, name: str, kind: str = "video") -> dict:
        return self.post("/api/playlists", {"name": name, "kind": kind}) or {}

    def rename_playlist(self, playlist_id: int, name: str) -> None:
        self.put(f"/api/playlists/{playlist_id}", {"name": name})

    def delete_playlist(self, playlist_id: int) -> None:
        self.delete(f"/api/playlists/{playlist_id}")

    def playlist_items(self, playlist_id: int) -> list[dict]:
        return self.get(f"/api/playlists/{playlist_id}/items") or []

    def add_to_playlist(self, playlist_id: int, item_id: int) -> bool:
        """`True` wenn tatsächlich hinzugefügt, `False` wenn schon drin — der
        Server unterscheidet das über `RowsAffected` des `INSERT OR IGNORE`,
        damit die Oberfläche „ist bereits in X" melden kann."""
        data = self.post(f"/api/playlists/{playlist_id}/items", {"itemId": item_id}) or {}
        return bool(data.get("added"))

    def remove_from_playlist(self, playlist_id: int, item_id: int) -> None:
        self.delete(f"/api/playlists/{playlist_id}/items/{item_id}")

    def reorder_playlist(self, playlist_id: int, item_ids: list[int]) -> None:
        self.put(f"/api/playlists/{playlist_id}/items", {"itemIds": item_ids})

    def playlists_for_item(self, item_id: int) -> list[dict]:
        """Für die Häkchen im „Zu Playlist hinzufügen"-Dialog."""
        return self.get(f"/api/items/{item_id}/playlists") or []

    # -- Startseite & Reiterleiste (pro Benutzer) --------------------------

    def home_preferences(self) -> dict:
        """`{libraries: [...], showContinue, showNextUp}` — welche Bibliotheken
        auf der Startseite erscheinen, in welcher Reihenfolge, plus die beiden
        globalen Streifen-Schalter."""
        return self.get("/api/home/preferences") or {}

    def set_home_preference(self, library_id: int, on_home: bool) -> None:
        self.put(f"/api/home/preferences/{library_id}", {"onHome": on_home})

    def set_home_order(self, library_ids: list[int]) -> None:
        self.put("/api/home/order", {"ids": library_ids})

    def set_home_strips(self, show_continue: bool | None = None, show_next_up: bool | None = None) -> None:
        body: dict[str, bool] = {}
        if show_continue is not None:
            body["showContinue"] = show_continue
        if show_next_up is not None:
            body["showNextUp"] = show_next_up
        self.put("/api/home/strips", body)

    def nav_preferences(self) -> dict:
        """Eigene Tabelle, bewusst getrennt von den Startseiten-Einstellungen:
        Reiterleiste und Startseite sollen unabhängig steuerbar sein (siehe
        CLAUDE.md „Pro-User-Overrides, DREI unabhängige Achsen")."""
        return self.get("/api/nav/preferences") or {}

    def set_nav_preference(self, library_id: int, on_nav: bool) -> None:
        self.put(f"/api/nav/preferences/{library_id}", {"onNav": on_nav})

    def set_nav_order(self, library_ids: list[int]) -> None:
        self.put("/api/nav/order", {"ids": library_ids})

    # -- Vorschaubilder beim Spulen (Trickplay) ----------------------------

    def trickplay_cues(self, item_id: int) -> list[TrickplayCue]:
        """Parst das Sprite-Manifest. Gibt eine leere Liste zurück, wenn es
        keine Vorschaubilder gibt (404 bei `trickplayStatus != "done"`) — der
        Player lässt die Vorschau dann einfach weg."""
        raw = self.fetch_bytes(f"/api/trickplay/{item_id}/thumbs.vtt")
        if not raw:
            return []
        try:
            return parse_trickplay_vtt(raw.decode("utf-8", errors="replace"))
        except ValueError:
            return []

    def trickplay_sprite_bytes(self, item_id: int) -> bytes | None:
        """Das komplette Sprite-Sheet als JPEG. Bewusst über diese Session
        geladen (nicht als URL an ein Bild-Widget gegeben), damit die
        Cookie-Anmeldung greift."""
        return self.fetch_bytes(f"/api/trickplay/{item_id}/sprite.jpg")

    # -- Untertitel --------------------------------------------------------

    def subtitle_vtt(self, item_id: int, stream_index: int, timeout: float = 120) -> str | None:
        """Eingebetteten Text-Untertitel als WebVTT holen.

        `stream_index` ist der ABSOLUTE ffmpeg-Stream-Index aus
        `playback_info()["streams"]`, nicht der n-te Untertitel — der Server
        gibt ihn direkt an `ffmpeg -map 0:<idx>` weiter.

        Beim ERSTEN Abruf extrahiert der Server die Spur per ffmpeg und
        blockiert so lange; erst danach liegt sie in seinem Cache. Deshalb das
        großzügige Standard-Timeout — mit den 10 s von `fetch_bytes` läuft ein
        kalter Abruf zuverlässig ins Leere (in der Erstprüfung genau so
        passiert). Bild-Untertitel (PGS/VOBSUB) lehnt der Server mit 415 ab,
        das kommt hier ebenfalls als `None` zurück."""
        raw = self.fetch_bytes(f"/api/subtitle/{item_id}/{stream_index}.vtt", timeout=timeout)
        return raw.decode("utf-8", errors="replace") if raw else None

    def generated_subtitle_vtt(self, item_id: int, language: str) -> str | None:
        """Von Whisper erzeugten Untertitel holen (`🎤 … (KI)`)."""
        raw = self.fetch_bytes(f"/api/generated-subtitle/{item_id}/{language}.vtt")
        return raw.decode("utf-8", errors="replace") if raw else None

    def ocr_subtitle_vtt(self, item_id: int, language: str) -> str | None:
        """Per OCR aus Bild-Untertiteln erzeugten Untertitel holen
        (`📝 … (OCR)`)."""
        raw = self.fetch_bytes(f"/api/ocr-subtitle/{item_id}/{language}.vtt")
        return raw.decode("utf-8", errors="replace") if raw else None

    # -- Eigenes Konto -----------------------------------------------------

    def change_password(self, old_password: str, new_password: str) -> None:
        """Der Server prüft das alte Passwort (403 bei falsch) und verlangt
        mindestens 6 Zeichen für das neue (400 sonst). Beide Fehlertexte
        kommen über `GoldfishAPIError` direkt anzeigefertig an."""
        self.put("/api/auth/password", {"oldPassword": old_password, "newPassword": new_password})

    # -- Gesehen-Sync zwischen zwei Konten ---------------------------------

    def other_users(self) -> list[dict]:
        """Nur ID und Benutzername, für die Partner-Auswahl."""
        return self.get("/api/users/names") or []

    def watch_links(self) -> list[dict]:
        """Eigene Verknüpfungen, aktive und offene."""
        return self.get("/api/watch-links") or []

    def request_watch_link(self, username: str) -> None:
        self.post("/api/watch-links", {"username": username})

    def confirm_watch_link(self, partner_id: int) -> None:
        self.post(f"/api/watch-links/{partner_id}/confirm")

    def unlink_watch_link(self, partner_id: int) -> None:
        """Dient auch zum Ablehnen einer offenen Anfrage — der Server löscht in
        beiden Fällen einfach die Zeile."""
        self.delete(f"/api/watch-links/{partner_id}")

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

    def download_url(self, item_id: int, compat: bool = False, profile: str = "") -> str:
        """Download-URL. Ohne Argumente die unveränderte Originaldatei.

        `compat=True` lässt den Server vorher prüfen, ob die Datei überhaupt
        direkt abspielbar ist, und legt sonst einmalig eine passende Kopie an.
        `profile` (z. B. "720p") deckelt zusätzlich Auflösung und Bitrate —
        aber nur, wenn das Item sie tatsächlich überschreitet; "orig" oder
        leer heißt bewusst kein Deckel."""
        params: dict[str, str] = {}
        if compat:
            params["compat"] = "1"
        if profile and profile != "orig":
            params["profile"] = profile
        path = f"/api/download/{item_id}"
        if not params:
            return self._url(path)
        return self._url(path) + "?" + urlencode(params)

    def compat_download_status(self, item_id: int, profile: str = "") -> dict:
        """Fortschritt der serverseitigen Formatanpassung:
        `{state, percent, message}` mit `state` aus `ready|preparing|error|idle`.
        Stößt sie an, falls nötig — der Aufrufer ruft also einfach wiederholt
        auf, bis `ready`.

        `profile` MUSS mit dem übereinstimmen, das `download_url` für denselben
        Download benutzt, sonst wird ein anderer Server-Cache-Pfad geprüft als
        der Download danach anfordert."""
        params: dict[str, str] = {}
        if profile and profile != "orig":
            params["profile"] = profile
        return self.get(f"/api/download/{item_id}/compat-status", params) or {}

    def download_response(self, item_id: int, compat: bool = False, profile: str = "") -> requests.Response:
        """Startet einen Streaming-Download. Aufrufer muss `resp.close()`
        sicherstellen (via `with` oder try/finally). Argumente wie bei
        `download_url`."""
        url = self.download_url(item_id, compat=compat, profile=profile)
        try:
            resp = self.session.get(url, stream=True, timeout=30)
        except requests.RequestException as exc:
            raise GoldfishAPIError(f"Download fehlgeschlagen: {exc}") from exc
        if resp.status_code >= 400:
            resp.close()
            raise GoldfishAPIError(f"Download fehlgeschlagen: HTTP {resp.status_code}", status=resp.status_code)
        return resp

    def fetch_external_bytes(self, url: str, timeout: float = 10) -> bytes | None:
        """Ein Bild von einer fremden Adresse holen — für die Standbilder und
        Poster, die TMDB direkt ausliefert (`image.tmdb.org`). Der Browser
        macht das genauso; einen Weg über den eigenen Server gibt es für
        beliebige TMDB-Pfade nicht.

        Bewusst OHNE die eigene Sitzung: `requests.get` statt `self.session`,
        damit der Anmelde-Cookie nicht an Dritte gehen kann. Er ist zwar an
        die eigene Domain gebunden und würde ohnehin nicht mitgesendet, aber
        eine eigene Anfrage macht das unmissverständlich."""
        try:
            resp = requests.get(url, timeout=timeout)
        except requests.RequestException:
            return None
        if resp.status_code != 200:
            return None
        return resp.content

    def tmdb_image_url(self, path: str | None, size: str = "w342") -> str | None:
        """Vollständige TMDB-Bildadresse aus einem Pfad wie `/abc123.jpg`.
        Größen wie im Browser: w342 für Kacheln, w500 für große Poster,
        w185 für Portraits."""
        if not path:
            return None
        return f"https://image.tmdb.org/t/p/{size}{path}"

    def fetch_bytes(self, path: str, timeout: float = 10) -> bytes | None:
        """Für Poster/Thumbnails: kleine Bilder synchron laden (wird vom
        Aufrufer aus einem Hintergrund-Thread heraus benutzt).

        `timeout` hochsetzen für Endpunkte, die serverseitig erst etwas
        erzeugen müssen — siehe `subtitle_vtt`."""
        url = self._url(path)
        try:
            resp = self.session.get(url, timeout=timeout)
        except requests.RequestException:
            return None
        if resp.status_code != 200:
            return None
        return resp.content
