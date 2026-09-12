"""Lokale Bibliotheken: Ordner und externe Datenträger ohne Server.

Gedacht für USB-Platten, SD-Karten und Ordner auf der eigenen Maschine. Läuft
vollständig ohne Goldfish-Server: einlesen, anzeigen, abspielen.

**Zwei Dinge macht diese App bewusst anders als die Mac-App:**

1. **Eingelesen wird mit GStreamer, nicht mit ffprobe.** Auf dem Zielsystem
   ist ffprobe oft gar nicht installiert (hier nachgeprüft: fehlt), GStreamer
   dagegen zwingend, weil die Wiedergabe darauf aufbaut.
   `GstPbutils.Discoverer` liefert Dauer, Auflösung und Spuren — gemessen 0,12
   Sekunden pro Datei. Das erspart eine zusätzliche Paketabhängigkeit.
2. **Es gibt keine Formatanpassung.** Die Mac-App muss Dateien umwandeln, die
   macOS nicht abspielen kann (MKV mit DTS zum Beispiel), und pflegt dafür
   einen eigenen Zwischenspeicher. Auf diesem System spielt GStreamer MKV,
   MP4, AVI, WMV, HEVC, H.264, VP9, AV1, AC3, DTS und E-AC3 direkt ab
   (nachgeprüft). Eine Umwandlung wäre also reine Arbeit ohne Nutzen.

Vorschaubilder kommen aus dem Zwischenspeicher des Dateimanagers, sofern er
eines angelegt hat. Selbst welche zu erzeugen bräuchte ffmpeg — siehe oben.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstPbutils", "1.0")
from gi.repository import Gio, GLib, Gst, GstPbutils  # noqa: E402

from . import config  # noqa: E402

VIDEO_EXTENSIONS = frozenset(
    {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".m4v", ".webm", ".mpg", ".mpeg", ".ts", ".m2ts", ".flv", ".3gp"}
)

# Zeitgrenze je Datei beim Einlesen. Eine beschädigte oder sehr langsam
# angebundene Datei soll den Durchlauf nicht anhalten.
_DISCOVER_TIMEOUT_S = 10
# Für ein Vorschaubild braucht es keine zehn Sekunden — gemessen 0,03 bis 0,11.
_THUMB_TIMEOUT_S = 5

_gst_ready = False


def _ensure_gst() -> None:
    global _gst_ready
    if not _gst_ready:
        Gst.init(None)
        _gst_ready = True


@dataclass
class LocalVideo:
    path: str
    name: str
    size_bytes: int = 0
    duration_sec: float = 0.0
    width: int = 0
    height: int = 0
    modified: float = 0.0

    def to_json(self) -> dict:
        return {
            "path": self.path,
            "name": self.name,
            "sizeBytes": self.size_bytes,
            "durationSec": self.duration_sec,
            "width": self.width,
            "height": self.height,
            "modified": self.modified,
        }

    @classmethod
    def from_json(cls, raw: dict) -> LocalVideo:
        return cls(
            path=raw.get("path", ""),
            name=raw.get("name", ""),
            size_bytes=int(raw.get("sizeBytes") or 0),
            duration_sec=float(raw.get("durationSec") or 0),
            width=int(raw.get("width") or 0),
            height=int(raw.get("height") or 0),
            modified=float(raw.get("modified") or 0),
        )

    def as_item(self) -> dict:
        """Als Item im Format des Servers, damit die vorhandenen Kacheln und
        das Wiedergabefenster ohne Sonderfall damit umgehen können."""
        return {
            "id": -abs(hash(self.path)) % (10**12),  # negativ: kein Server-Item
            "libraryId": 0,
            "title": self.name,
            "path": self.path,
            "relPath": self.name,
            "container": Path(self.path).suffix.lstrip(".").lower(),
            "width": self.width,
            "height": self.height,
            "durationSec": self.duration_sec,
            "sizeBytes": self.size_bytes,
            "hasThumb": False,
            "local": True,
        }


@dataclass
class LocalLibrary:
    name: str
    root: str
    videos: list[LocalVideo] = field(default_factory=list)
    scanned_at: float = 0.0
    # Nur für die zur Laufzeit erzeugte Sammel-Bibliothek gesetzt: die
    # Wurzeln, aus denen sie besteht. Sie wird nicht gespeichert.
    merged_from: list[str] = field(default_factory=list)

    @property
    def is_merged(self) -> bool:
        return bool(self.merged_from)

    @property
    def nav_key(self) -> str:
        """Stabiler Schlüssel für gemerkte Einstellungen (Sichtbarkeit in der
        Seitenleiste). Eine Sammel-Bibliothek hat keine eigene Wurzel, deshalb
        ein eigener Name — sie bleibt dieselbe, auch wenn sich ihre Teile
        ändern."""
        return "merged" if self.merged_from else self.root

    @property
    def available(self) -> bool:
        """Ob der Ordner gerade erreichbar ist. Bei externen Platten ist das
        der Normalfall "mal ja, mal nein" — die eingelesenen Einträge bleiben
        trotzdem erhalten, damit man sieht, was auf der Platte liegt.

        Bei einer Sammel-Bibliothek genügt eine erreichbare Wurzel: was von
        angeschlossenen Platten kommt, lässt sich abspielen, der Rest bleibt
        sichtbar."""
        if self.merged_from:
            return any(Path(root).is_dir() for root in self.merged_from)
        return Path(self.root).is_dir()

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "root": self.root,
            "scannedAt": self.scanned_at,
            "videos": [v.to_json() for v in self.videos],
        }

    @classmethod
    def from_json(cls, raw: dict) -> LocalLibrary:
        return cls(
            name=raw.get("name", ""),
            root=raw.get("root", ""),
            scanned_at=float(raw.get("scannedAt") or 0),
            videos=[LocalVideo.from_json(v) for v in (raw.get("videos") or [])],
        )


class LocalLibraryManager:
    """Verwaltet die lokalen Bibliotheken und ihren Bestand.

    Der Bestand wird gespeichert, damit die Einträge einer abgezogenen Platte
    sichtbar bleiben — ein erneutes Einlesen dauert sonst bei jedem Start neu.
    """

    def __init__(self) -> None:
        self.libraries: list[LocalLibrary] = []
        # Wurzeln, die zu EINER Sammel-Bibliothek zusammengefasst sind. Sie
        # verschwinden dadurch nicht, erscheinen in der Übersicht aber nur
        # noch gemeinsam — so wie es die Mac-App löst.
        self.merged_roots: list[str] = []
        self.merged_name: str = "Zusammengelegt"
        self._load()

    # -- Speichern und Laden ---------------------------------------------

    def _load(self) -> None:
        path = config.LOCAL_LIBRARIES_FILE
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.libraries = [LocalLibrary.from_json(entry) for entry in (raw.get("libraries") or [])]
        known = {lib.root for lib in self.libraries}
        # Wurzeln, die es nicht mehr gibt, beim Laden aussortieren.
        self.merged_roots = [r for r in (raw.get("mergedRoots") or []) if r in known]
        self.merged_name = raw.get("mergedName") or "Zusammengelegt"

    def save(self) -> None:
        config.ensure_dirs()
        try:
            config.LOCAL_LIBRARIES_FILE.write_text(
                json.dumps(
                    {
                        "libraries": [lib.to_json() for lib in self.libraries],
                        "mergedRoots": self.merged_roots,
                        "mergedName": self.merged_name,
                    },
                    indent=2,
                ),
                "utf-8",
            )
        except OSError:
            pass  # Speichern ist Komfort; der Bestand lässt sich neu einlesen

    # -- Verwalten -------------------------------------------------------

    def add(self, root: str, name: str = "") -> LocalLibrary | None:
        root = str(Path(root).expanduser().resolve())
        if any(lib.root == root for lib in self.libraries):
            return None
        library = LocalLibrary(name=name or Path(root).name or root, root=root)
        self.libraries.append(library)
        self.save()
        return library

    def remove(self, library: LocalLibrary) -> None:
        """Entfernt die Bibliothek aus der App. Die Dateien selbst bleiben
        unangetastet — hier wird nichts gelöscht."""
        if library in self.libraries:
            self.libraries.remove(library)
            if library.root in self.merged_roots:
                self.merged_roots.remove(library.root)
            self.save()

    def rename(self, library: LocalLibrary, name: str) -> None:
        library.name = name
        self.save()

    def set_merged(self, roots: list[str], name: str = "") -> None:
        """Legt fest, welche Wurzeln zu einer Sammel-Bibliothek gehören.

        Unter zwei Wurzeln ist das sinnlos — dann wird die Gruppe aufgelöst,
        damit nicht eine "Sammlung" aus einem einzigen Datenträger entsteht."""
        known = {lib.root for lib in self.libraries}
        roots = [r for r in roots if r in known]
        self.merged_roots = roots if len(roots) >= 2 else []
        if name:
            self.merged_name = name
        self.save()

    def merged_library(self) -> LocalLibrary | None:
        """Die Sammel-Bibliothek, zur Laufzeit aus ihren Teilen gebaut.

        Doppelte Pfade können nicht auftreten (jede Wurzel ist nur einmal
        eingerichtet), gleiche Dateien auf zwei Platten dagegen schon — die
        bleiben absichtlich beide sichtbar. Wer sie loswerden will, findet sie
        über die Dublettensuche, die hier über alle Teile zugleich läuft."""
        if len(self.merged_roots) < 2:
            return None
        parts = [lib for lib in self.libraries if lib.root in self.merged_roots]
        videos: list[LocalVideo] = []
        for part in parts:
            videos.extend(part.videos)
        videos.sort(key=lambda v: v.name.lower())
        return LocalLibrary(
            name=self.merged_name,
            root="",
            videos=videos,
            merged_from=[p.root for p in parts],
        )

    def visible_libraries(self) -> list[LocalLibrary]:
        """Was die Übersicht zeigt: die Sammel-Bibliothek als EIN Eintrag,
        dazu alle übrigen einzeln."""
        merged = self.merged_library()
        singles = [lib for lib in self.libraries if lib.root not in self.merged_roots]
        return ([merged] if merged else []) + singles

    def find_duplicates(self, library: LocalLibrary) -> list[list[LocalVideo]]:
        """Gruppen von Dateien, die sich in Größe UND Laufzeit gleichen.

        Dieselbe vorsichtige Regel wie im Server für private Bibliotheken:
        gleicher Umfang und gleiche Länge ist ein starkes Zeichen für dieselbe
        Datei, erkennt aber bewusst keine neu kodierten Fassungen. Das
        vermeidet Fehlalarme, bei denen jemand die falsche Datei löscht."""
        buckets: dict[tuple[int, int], list[LocalVideo]] = {}
        for video in library.videos:
            if video.size_bytes <= 0:
                continue
            key = (video.size_bytes, int(video.duration_sec))
            buckets.setdefault(key, []).append(video)
        return [group for group in buckets.values() if len(group) > 1]

    # -- Einlesen --------------------------------------------------------

    def scan_async(
        self,
        library: LocalLibrary,
        on_progress: Callable[[int, int, str], None] | None = None,
        on_done: Callable[[int], None] | None = None,
    ) -> None:
        threading.Thread(target=self._scan_worker, args=(library, on_progress, on_done), daemon=True).start()

    def _scan_worker(
        self,
        library: LocalLibrary,
        on_progress: Callable[[int, int, str], None] | None,
        on_done: Callable[[int], None] | None,
    ) -> None:
        _ensure_gst()
        root = Path(library.root)
        if not root.is_dir():
            if on_done:
                GLib.idle_add(on_done, -1)
            return

        files = sorted(
            p
            for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS and not p.name.startswith("._")
        )
        # `._*` sind die Beihefte, die macOS beim Kopieren auf FAT-Sticks
        # anlegt (AppleDouble): 4 KB groß, ohne Bild und ohne Ton. Auf dem
        # USB-Stick des Benutzers standen dutzende davon als eigene Kacheln.
        # Was schon eingelesen ist und sich nicht geändert hat, wird
        # übernommen — ein zweiter Durchlauf über eine große Platte soll nicht
        # alles neu ermitteln.
        known = {v.path: v for v in library.videos}
        discoverer = GstPbutils.Discoverer.new(_DISCOVER_TIMEOUT_S * Gst.SECOND)

        result: list[LocalVideo] = []
        for index, file_path in enumerate(files, start=1):
            if on_progress:
                GLib.idle_add(on_progress, index, len(files), file_path.name)
            try:
                stat = file_path.stat()
            except OSError:
                continue
            previous = known.get(str(file_path))
            if previous is not None and previous.modified == stat.st_mtime and previous.duration_sec > 0:
                result.append(previous)
                continue
            result.append(self._inspect(discoverer, file_path, stat))

        library.videos = result
        library.scanned_at = GLib.get_real_time() / 1_000_000
        self.save()
        if on_done:
            GLib.idle_add(on_done, len(result))

    @staticmethod
    def _inspect(discoverer: GstPbutils.Discoverer, file_path: Path, stat) -> LocalVideo:
        video = LocalVideo(
            path=str(file_path),
            name=file_path.stem,
            size_bytes=stat.st_size,
            modified=stat.st_mtime,
        )
        try:
            info = discoverer.discover_uri(file_path.as_uri())
        except GLib.Error:
            # Unlesbare Datei: Name und Größe stehen trotzdem, sie erscheint
            # nur ohne Laufzeit und Auflösung.
            return video
        video.duration_sec = info.get_duration() / Gst.SECOND
        streams = info.get_video_streams()
        if streams:
            video.width = streams[0].get_width()
            video.height = streams[0].get_height()
        return video


def thumbnail_bytes(path: str, width: int = 320, at_fraction: float = 0.15) -> bytes | None:
    """Ein Vorschaubild aus der Datei selbst — als JPEG-Bytes.

    **Die Antwort auf "geht das nicht doch ohne ffmpeg?": ja, mit GStreamer.**
    Dieselbe Bibliothek, die die Videos abspielt, kann auch ein Einzelbild
    liefern: die Pipeline wird nur in den Pause-Zustand gebracht (dabei
    entsteht bereits das erste Bild), auf 15 % der Laufzeit gesprungen und das
    dort anliegende Bild als JPEG abgeholt. Gemessen 0,03 bis 0,11 Sekunden je
    Datei — schnell genug, um es beim Anzeigen im Hintergrund zu machen.

    Ergebnis wird vom Aufrufer zwischengespeichert (siehe `widgets/poster.py`),
    hier passiert nichts weiter als das Erzeugen. Läuft im Ladefaden, nicht im
    Hauptablauf."""
    _ensure_gst()
    try:
        uri = GLib.filename_to_uri(path, None)
    except GLib.Error:
        return None
    # `pixel-aspect-ratio=1/1` nagelt die Höhe an das Seitenverhältnis, ohne
    # sie vorzugeben — ein festes Maß würde bei Breitwand verzerren oder die
    # Aushandlung scheitern lassen.
    description = (
        f'uridecodebin uri="{uri}" ! videoconvert ! videoscale ! '
        f"video/x-raw,width={width},pixel-aspect-ratio=1/1 ! jpegenc quality=80 ! "
        "appsink name=sink max-buffers=1 drop=false sync=false"
    )
    try:
        pipeline = Gst.parse_launch(description)
    except GLib.Error:
        return None
    sink = pipeline.get_by_name("sink")
    try:
        pipeline.set_state(Gst.State.PAUSED)
        changed, _state, _pending = pipeline.get_state(_THUMB_TIMEOUT_S * Gst.SECOND)
        if changed != Gst.StateChangeReturn.SUCCESS:
            return None
        found, duration = pipeline.query_duration(Gst.Format.TIME)
        if found and duration > 0:
            # Keyframe-genau reicht und ist deutlich schneller als exakt.
            pipeline.seek_simple(
                Gst.Format.TIME,
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                int(duration * at_fraction),
            )
            pipeline.get_state(_THUMB_TIMEOUT_S * Gst.SECOND)
        sample = sink.emit("pull-preroll")
        if sample is None:
            return None
        buffer = sample.get_buffer()
        ok, info = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return None
        try:
            return bytes(info.data)
        finally:
            buffer.unmap(info)
    except GLib.Error:
        return None
    finally:
        pipeline.set_state(Gst.State.NULL)


def system_thumbnail(path: str) -> str | None:
    """Vorschaubild aus dem Zwischenspeicher des Dateimanagers, falls vorhanden.

    Selbst eines zu erzeugen bräuchte ffmpeg, das hier nicht vorausgesetzt
    werden kann. Hat der Dateimanager die Datei schon einmal angezeigt, liegt
    das Bild bereits vor und wird mitgenutzt."""
    try:
        info = Gio.File.new_for_path(path).query_info(
            "thumbnail::path", Gio.FileQueryInfoFlags.NONE, None
        )
    except GLib.Error:
        return None
    thumb = info.get_attribute_byte_string("thumbnail::path")
    return thumb if thumb and Path(thumb).exists() else None
