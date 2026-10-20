"""Offline-Downloads: lädt die Originaldatei über /api/download/{id} herunter
und hält eine kleine JSON-Registry (Item-ID → lokaler Pfad + Metadaten), damit
die App nach einem Neustart weiß, was offline verfügbar ist — ohne dafür eine
eigene Datenbank zu brauchen."""

from __future__ import annotations

import json
import re
import threading
import weakref
from pathlib import Path
from typing import Callable

from gi.repository import GLib

from . import config
from .api import GoldfishAPIError, GoldfishClient

_SANITIZE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Meldung an `on_error`, wenn der Benutzer abbricht — Aufrufer (Album-Warteschlange)
# erkennen daran, dass kein Fehler vorliegt und nicht weitergeladen werden soll.
CANCELLED = "Abgebrochen"

ProgressCB = Callable[[int, float], None]  # (item_id, fraction 0..1)
DoneCB = Callable[[int, str], None]  # (item_id, local_path)
ErrorCB = Callable[[int, str], None]  # (item_id, message)
ChangeCB = Callable[[int], None]  # (item_id) — Download fertig, fehlgeschlagen oder gelöscht


def _sanitize_filename(name: str) -> str:
    name = _SANITIZE_RE.sub("", name).strip(" .")
    return name or "video"


class _Cancelled(Exception):
    """Intern: der Benutzer hat den Download abgebrochen."""


class DownloadManager:
    def __init__(self, client: GoldfishClient):
        self.client = client
        self._registry: dict[str, dict] = {}
        self._active: set[int] = set()
        # Pro laufendem Download: Abbruch-Merker (vom Worker-Faden geprüft),
        # Titel für die Downloads-Seite und letzter bekannter Fortschritt.
        self._cancel: dict[int, threading.Event] = {}
        self._active_info: dict[int, dict] = {}
        # Wer wissen will, ob ein Titel (nicht mehr) offline vorliegt — etwa
        # die Detailseite, deren Abspielen-Knopf dann auf "Offline abspielen"
        # wechselt. Die `on_done`-Rückrufe von `start_download` reichen dafür
        # nicht: sie erreichen nur die Seite, die den Download gestartet hat,
        # und keine, die später für denselben Titel geöffnet wurde.
        # Schwache Referenzen wie bei `AppContext.add_item_state_listener`:
        # eine geschlossene Seite soll nicht durch diese Liste weiterleben.
        # Deshalb muss der Aufrufer die gebundene Methode selbst festhalten
        # (`weakref.ref(obj.methode)` ist sofort tot).
        self._listeners: list[weakref.ref] = []
        self._load()

    # -- Änderungs-Meldungen ----------------------------------------------

    def add_listener(self, callback: ChangeCB) -> None:
        """`callback(item_id)` läuft im GTK-Hauptablauf, sobald ein Download
        fertig ist, fehlschlägt oder gelöscht wird."""
        self._listeners.append(weakref.ref(callback))

    def remove_listener(self, callback: ChangeCB) -> None:
        self._listeners = [r for r in self._listeners if r() is not None and r() != callback]

    def _notify(self, item_id: int) -> bool:
        alive = []
        for ref in self._listeners:
            callback = ref()
            if callback is None:
                continue  # Seite ist weg — Eintrag ausräumen
            alive.append(ref)
            try:
                callback(item_id)
            except Exception:  # noqa: BLE001 — eine Anzeige darf den Rest nie stören
                continue
        self._listeners = alive
        return False  # auch als GLib.idle_add-Rückruf nutzbar

    # -- Registry --------------------------------------------------------

    def _load(self) -> None:
        config.ensure_dirs()
        if not config.DOWNLOADS_REGISTRY_FILE.exists():
            return
        try:
            self._registry = json.loads(config.DOWNLOADS_REGISTRY_FILE.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            self._registry = {}

    def _save(self) -> None:
        config.ensure_dirs()
        config.DOWNLOADS_REGISTRY_FILE.write_text(json.dumps(self._registry, indent=2), "utf-8")

    def list_downloads(self) -> list[dict]:
        return sorted(self._registry.values(), key=lambda r: r.get("downloadedAt", ""), reverse=True)

    def is_downloaded(self, item_id: int) -> bool:
        rec = self._registry.get(str(item_id))
        return bool(rec and Path(rec["path"]).exists())

    def is_downloading(self, item_id: int) -> bool:
        return item_id in self._active

    def active_downloads(self) -> list[dict]:
        """Laufende/wartende Downloads: `{id, title, fraction}` (fraction -1 =
        noch kein Fortschritt, etwa solange der Server umwandelt)."""
        return [dict(info, id=i) for i, info in self._active_info.items() if i in self._active]

    def cancel_download(self, item_id: int) -> None:
        """Bricht einen laufenden Download ab. Der Worker-Faden stoppt beim
        nächsten Datenblock, schließt die Verbindung und löscht die `.part`-Datei;
        der Registry-Eintrag entsteht gar nicht erst. Danach meldet `_notify`."""
        event = self._cancel.get(int(item_id))
        if event is not None:
            event.set()

    def local_path(self, item_id: int) -> Path | None:
        rec = self._registry.get(str(item_id))
        if rec and Path(rec["path"]).exists():
            return Path(rec["path"])
        return None

    def get_record(self, item_id: int) -> dict | None:
        """Der rohe Registry-Eintrag (u.a. `sizeBytes`) — `None`, wenn nicht
        heruntergeladen oder die Datei inzwischen fehlt. Für die Detailseite,
        die daraus die Download-Kenndaten anzeigt."""
        rec = self._registry.get(str(item_id))
        if rec and Path(rec["path"]).exists():
            return rec
        return None

    def get_local_resume(self, item_id: int) -> float:
        """Fortsetz-Position eines offline heruntergeladenen Videos.

        Läuft rein lokal aus der Registry (kein `/api/items/{id}/resume` —
        der Server ist beim Offline-Abspielen nicht erreichbar), damit
        „Weiterschauen?" auch ohne Netz funktioniert."""
        rec = self._registry.get(str(item_id))
        return float(rec.get("resumeSec") or 0.0) if rec else 0.0

    def set_local_resume(self, item_id: int, position_sec: float) -> None:
        rec = self._registry.get(str(item_id))
        if rec is None:
            return
        rec["resumeSec"] = position_sec
        self._save()

    def delete_download(self, item_id: int) -> None:
        rec = self._registry.pop(str(item_id), None)
        if rec:
            try:
                Path(rec["path"]).unlink(missing_ok=True)
            except OSError:
                pass
            self._save()
            self._notify(int(item_id))

    # -- Download ----------------------------------------------------------

    def _finish(self, item_id: int) -> None:
        """Räumt den Laufzeit-Zustand eines Downloads weg (Ende, Fehler, Abbruch)."""
        self._active.discard(item_id)
        self._cancel.pop(item_id, None)
        self._active_info.pop(item_id, None)

    def start_download(
        self,
        item: dict,
        on_progress: ProgressCB | None = None,
        on_done: DoneCB | None = None,
        on_error: ErrorCB | None = None,
        profile: str = "",
    ) -> None:
        """`profile` deckelt Auflösung und Bitrate (etwa "720p") — nützlich für
        ein Notebook mit wenig Platz, wo ein 80-GB-Film nichts verloren hat.
        Leer oder "orig" lädt die Datei unverändert.

        Mit einem Profil wandelt der Server vorher um; das dauert einmalig,
        wird aber zwischengespeichert. Der Deckel wirkt nur, wenn die Datei ihn
        tatsächlich überschreitet."""
        item_id = int(item["id"])
        if item_id in self._active:
            return
        self._active.add(item_id)
        self._cancel[item_id] = threading.Event()
        self._active_info[item_id] = {"title": item.get("title") or f"item-{item_id}", "fraction": -1.0}
        thread = threading.Thread(
            target=self._download_worker,
            args=(item, on_progress, on_done, on_error, profile),
            daemon=True,
        )
        thread.start()

    def _download_worker(
        self,
        item: dict,
        on_progress: ProgressCB | None,
        on_done: DoneCB | None,
        on_error: ErrorCB | None,
        profile: str = "",
    ) -> None:
        item_id = int(item["id"])
        cancel = self._cancel.setdefault(item_id, threading.Event())
        try:
            # Mit Profil über die Kompatibilitätsprüfung des Servers, damit er
            # die kleinere Fassung erzeugt; ohne Profil unverändert.
            resp = self.client.download_response(item_id, compat=bool(profile), profile=profile)
        except GoldfishAPIError as exc:
            self._finish(item_id)
            if on_error:
                GLib.idle_add(on_error, item_id, CANCELLED if cancel.is_set() else str(exc))
            GLib.idle_add(self._notify, item_id)
            return

        if cancel.is_set():  # während der Server-Umwandlung abgebrochen
            resp.close()
            self._finish(item_id)
            if on_error:
                GLib.idle_add(on_error, item_id, CANCELLED)
            GLib.idle_add(self._notify, item_id)
            return

        title = item.get("title") or f"item-{item_id}"
        ext = (item.get("container") or "mp4").lower()
        filename = f"{_sanitize_filename(title)}-{item_id}.{ext}"
        config.ensure_dirs()
        final_path = config.DOWNLOADS_DIR / filename
        tmp_path = final_path.with_suffix(final_path.suffix + ".part")

        total = int(resp.headers.get("Content-Length") or item.get("sizeBytes") or 0)
        downloaded = 0
        try:
            with resp, open(tmp_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if cancel.is_set():
                        raise _Cancelled
                    if not chunk:
                        continue
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        fraction = min(1.0, downloaded / total)
                        info = self._active_info.get(item_id)
                        if info is not None:
                            info["fraction"] = fraction
                    if on_progress and total > 0:
                        GLib.idle_add(on_progress, item_id, fraction)
            tmp_path.rename(final_path)
        except _Cancelled:
            # `with resp` hat die Verbindung schon geschlossen; Teildatei weg.
            tmp_path.unlink(missing_ok=True)
            self._finish(item_id)
            if on_error:
                GLib.idle_add(on_error, item_id, CANCELLED)
            GLib.idle_add(self._notify, item_id)
            return
        except OSError as exc:
            tmp_path.unlink(missing_ok=True)
            self._finish(item_id)
            if on_error:
                GLib.idle_add(on_error, item_id, f"Datei konnte nicht geschrieben werden: {exc}")
            GLib.idle_add(self._notify, item_id)
            return
        except Exception as exc:  # noqa: BLE001 — sonst bliebe "Download läuft" für immer stehen
            tmp_path.unlink(missing_ok=True)
            self._finish(item_id)
            if on_error:
                GLib.idle_add(on_error, item_id, f"Unerwarteter Fehler: {exc}")
            GLib.idle_add(self._notify, item_id)
            return

        self._registry[str(item_id)] = {
            "id": item_id,
            "title": title,
            "path": str(final_path),
            "sizeBytes": downloaded,
            "durationSec": item.get("durationSec", 0),
            "downloadedAt": GLib.DateTime.new_now_utc().format_iso8601(),
        }
        self._save()
        self._finish(item_id)
        if on_done:
            GLib.idle_add(on_done, item_id, str(final_path))
        GLib.idle_add(self._notify, item_id)
