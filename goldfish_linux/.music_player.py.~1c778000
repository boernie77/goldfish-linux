"""Musikwiedergabe mit Warteschlange.

Getrennt vom Videofenster und ohne eigenes Fenster: die Musik läuft weiter,
während man durch die Bibliotheken blättert. Die Bedienung sitzt in einer
Leiste am unteren Fensterrand (`widgets.mini_player`).

**Musik wird immer direkt abgespielt.** Anders als der Browser braucht es
dafür keine Umwandlung: GStreamer spielt MP3, M4A, OGG, Opus, WAV und auch
FLAC von sich aus. Deshalb geht es hier direkt auf `/api/stream/{id}` statt
über `/api/playback/{id}` — das erspart pro Titelwechsel einen serverseitigen
ffprobe-Lauf und lässt die Wiedergabe ohne Verzögerung beginnen. Der Browser
muss FLAC und WAV umwandeln, weil kein Browser sie zuverlässig abspielt; das
ist eine Einschränkung von dort, nicht von hier.
"""

from __future__ import annotations

import threading
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from .api import GoldfishAPIError, GoldfishClient  # noqa: E402

# Audioformate, die GStreamer mit den Paketabhängigkeiten dieser App direkt
# abspielt. Alles andere lässt sich vom Server umwandeln.
_DIRECT_CONTAINERS = frozenset({"mp3", "m4a", "m4b", "aac", "ogg", "opus", "oga", "wav", "flac", "wma"})


class MusicPlayer:
    """Hält die Warteschlange und das laufende Medium.

    Ein Wechsel meldet sich über `on_change`, damit die Leiste sich
    aktualisiert; `on_tick` läuft viermal pro Sekunde für Position und
    Fortschritt.
    """

    def __init__(self, client: GoldfishClient) -> None:
        self.client = client
        self.queue: list[dict] = []
        self.index = 0
        self.media: Gtk.MediaFile | None = None
        self.on_change: Callable[[], None] | None = None
        self.on_error: Callable[[str], None] | None = None
        self._handlers: list[int] = []
        self._volume = 1.0

    # -- Zustand ---------------------------------------------------------

    @property
    def current(self) -> dict | None:
        if 0 <= self.index < len(self.queue):
            return self.queue[self.index]
        return None

    @property
    def playing(self) -> bool:
        return bool(self.media and self.media.get_playing())

    @property
    def position(self) -> float:
        return (self.media.get_timestamp() / 1_000_000) if self.media else 0.0

    @property
    def duration(self) -> float:
        """Dauer aus dem Titel selbst, nicht aus dem Medium — der Server kennt
        sie aus dem Einlesen, und beim Start steht sie damit sofort da."""
        item = self.current
        if item and item.get("durationSec"):
            return float(item["durationSec"])
        return (self.media.get_duration() / 1_000_000) if self.media else 0.0

    # -- Steuerung -------------------------------------------------------

    def play_queue(self, tracks: list[dict], index: int = 0) -> None:
        self.queue = list(tracks)
        self.index = max(0, min(index, len(self.queue) - 1)) if self.queue else 0
        self._start_current()

    def append(self, tracks: list[dict]) -> None:
        """Titel hinten anhängen. Läuft nichts, beginnt die Wiedergabe."""
        was_empty = not self.queue
        self.queue.extend(tracks)
        if was_empty:
            self.index = 0
            self._start_current()
        elif self.on_change:
            self.on_change()

    def toggle_play(self) -> None:
        if self.media is None:
            if self.queue:
                self._start_current()
            return
        if self.media.get_playing():
            self.media.pause()
        else:
            self.media.play()
        if self.on_change:
            self.on_change()

    def next_track(self) -> None:
        if self.index + 1 < len(self.queue):
            self.index += 1
            self._start_current()
        else:
            self.stop()

    def previous_track(self) -> None:
        # Innerhalb der ersten Sekunden zurück an den Anfang des Titels, wie
        # man es von Musikprogrammen kennt; danach zum vorherigen.
        if self.position > 3 or self.index == 0:
            self.seek(0)
            return
        self.index -= 1
        self._start_current()

    def play_index(self, index: int) -> None:
        if 0 <= index < len(self.queue):
            self.index = index
            self._start_current()

    def remove_index(self, index: int) -> None:
        """Titel aus der Warteschlange nehmen. Trifft es den laufenden, geht es
        mit dem nächsten weiter; liegt er davor, verschiebt sich der Zeiger."""
        if not 0 <= index < len(self.queue):
            return
        if index == self.index:
            del self.queue[index]
            if self.index >= len(self.queue):
                self.stop()
                return
            self._start_current()
            return
        del self.queue[index]
        if index < self.index:
            self.index -= 1
        if self.on_change:
            self.on_change()

    def seek(self, seconds: float) -> None:
        if self.media and self.media.is_seekable():
            self.media.seek(int(max(0.0, seconds) * 1_000_000))

    def set_volume(self, value: float) -> None:
        self._volume = value
        if self.media:
            self.media.set_volume(value)

    def stop(self) -> None:
        self._release()
        self.queue = []
        self.index = 0
        if self.on_change:
            self.on_change()

    def shuffle(self) -> None:
        """Warteschlange mischen und beim laufenden Titel weitermachen."""
        import random

        current = self.current
        rest = [t for i, t in enumerate(self.queue) if i != self.index]
        random.shuffle(rest)
        self.queue = ([current] if current else []) + rest
        self.index = 0
        if self.on_change:
            self.on_change()

    # -- Innereien -------------------------------------------------------

    def _release(self) -> None:
        """Laufendes Medium anhalten und von seinen Signalen trennen — sonst
        meldet das abgelöste Medium beim Wechsel noch Ende oder Fehler und
        schiebt die Warteschlange ungewollt weiter."""
        if self.media is None:
            return
        for handler in self._handlers:
            try:
                self.media.disconnect(handler)
            except TypeError:
                pass
        self._handlers = []
        # `pause()` allein baut die GStreamer-Kette NICHT ab — jedes abgelöste
        # Medium behielt Decoder, Fäden und Puffer (nachgemessen: vierzehn
        # Fäden und rund 25 MB je Titel). Bei einem Album mit zwanzig Titeln
        # wäre das die halbe Maschine.
        self.media.set_playing(False)
        self.media.clear()
        self.media = None

    def _start_current(self) -> None:
        item = self.current
        if item is None:
            return
        self._release()

        container = (item.get("container") or "").lower()
        if container in _DIRECT_CONTAINERS:
            url = self.client.with_session_param(f"/api/stream/{item['id']}")
            self._play_url(url)
        else:
            # Seltener Fall: ein Format, das GStreamer nicht kennt. Dann den
            # Server fragen, der eine umgewandelte Fassung liefert.
            threading.Thread(target=self._resolve_and_play, args=(int(item["id"]),), daemon=True).start()

        self._report_started(item)
        if self.on_change:
            self.on_change()

    def _resolve_and_play(self, item_id: int) -> None:
        try:
            info = self.client.playback_info(item_id)
        except GoldfishAPIError:
            return
        url = info.get("url")
        if url:
            GLib.idle_add(self._play_url, self.client.with_session_param(url))

    def _play_url(self, url: str) -> bool:
        media = Gtk.MediaFile.new_for_file(Gio.File.new_for_uri(url))
        self.media = media
        self._handlers = [
            media.connect("notify::ended", self._on_ended),
            # Fehler nicht verschlucken: ohne diesen Anschluss bleibt ein
            # nicht abspielbarer Titel stumm stehen, und die Leiste zeigt
            # weiter "läuft" bei Position null.
            media.connect("notify::error", self._on_error),
        ]
        media.set_volume(self._volume)
        media.play()
        return False

    def _on_error(self, media: Gtk.MediaFile, _param) -> None:
        if media is not self.media:
            return
        error = media.get_error()
        if error is None:
            return
        item = self.current or {}
        if self.on_error:
            self.on_error(f"{item.get('title') or 'Titel'}: {error.message}")
        item_id = item.get("id")
        if item_id:
            self._report_error(int(item_id), error.message)

    def _report_error(self, item_id: int, message: str) -> None:
        def worker() -> None:
            try:
                self.client.playback_error(item_id, message)
            except GoldfishAPIError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _on_ended(self, media: Gtk.MediaFile, _param) -> None:
        if media is not self.media or not media.get_ended():
            return
        self.next_track()

    def _report_started(self, item: dict) -> None:
        item_id = int(item["id"])

        def worker() -> None:
            try:
                self.client.playback_start(item_id)
            except GoldfishAPIError:
                pass  # Protokoll ist Beigabe, nie ein Blocker

        threading.Thread(target=worker, daemon=True).start()
