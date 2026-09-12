"""Asynchrones Laden von Postern/Thumbnails in ein Gtk.Picture.

Lädt im Hintergrund-Thread per requests (über GoldfishClient), cached die
Bytes lokal (Dateiname = Hash des Server-Pfads) und setzt das Ergebnis via
GLib.idle_add auf das Picture-Widget — GTK-Widgets dürfen nur vom Main-Thread
aus angefasst werden.

**Wiederverwendung von Widgets:** seit dem Kachelraster (Gtk.GridView mit
Gtk.SignalListItemFactory) wird dasselbe Gtk.Picture beim Scrollen für
verschiedene Items neu belegt. Ein Poster-Request, der erst ankommt, wenn das
Widget längst ein anderes Item zeigt, würde sonst das falsche Bild einsetzen —
beim schnellen Scrollen durch tausende Kacheln der klassische "falsches
Poster"-Fehler. Deshalb trägt jedes Picture einen Generationszähler
(`_goldfish_poster_gen`): `load_poster_async` erhöht ihn bei jedem Aufruf, und
ein eintreffendes Ergebnis wird nur angewandt, wenn die Generation seitdem
unverändert ist.
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from .. import config  # noqa: E402
from ..api import GoldfishClient  # noqa: E402

# Poster kommen vom Server in Originalgröße (TMDB w500 und größer). Beim
# Dekodieren direkt auf Kachelbreite herunterskalieren: spart bei einem Raster
# mit hunderten Kacheln deutlich Speicher, und GdkPixbuf skaliert beim Laden
# günstiger als das Widget es beim Zeichnen täte.
_DECODE_WIDTH = 320

# Begrenzter Pool statt eines Threads pro Kachel: beim schnellen Durchscrollen
# einer großen Bibliothek (die Filme-Bibliothek bringt 2808 Kacheln mit) würde
# sonst für jede vorbeiziehende Kachel ein eigener Thread starten und gegen den
# Server feuern. Sechs gleichzeitige Anfragen füllen die Leitung gut aus und
# halten die Last für den Server berechenbar; die restlichen warten in der
# Warteschlange und sind, wenn sie dran sind, dank Generationszähler meist
# schon gegenstandslos.
_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="poster")


def _cache_path(server_path: str):
    digest = hashlib.sha1(server_path.encode("utf-8")).hexdigest()
    return config.POSTER_CACHE_DIR / f"{digest}.img"


def _next_generation(picture: Gtk.Picture) -> int:
    gen = getattr(picture, "_goldfish_poster_gen", 0) + 1
    picture._goldfish_poster_gen = gen
    return gen


def load_poster_async(picture: Gtk.Picture, client: GoldfishClient, server_path: str | None) -> None:
    """Lädt das Poster für `server_path` in `picture`.

    Der Aufrufer setzt vorher selbst einen Platzhalter — hier wird nur
    ersetzt, sobald echte Daten da sind. Bei Wiederverwendung des Widgets
    einfach erneut aufrufen, auch mit `None`, um ein noch unterwegs
    befindliches Ergebnis zu verwerfen."""
    gen = _next_generation(picture)
    if not server_path:
        return
    config.ensure_dirs()
    cache_file = _cache_path(server_path)
    if cache_file.exists():
        # Aus dem Cache synchron: bei einem Bild von der lokalen Platte ist
        # der Thread-Umweg teurer als das Dekodieren selbst, und die Kachel
        # steht ohne Nachflackern sofort fertig da.
        _apply_from_file(picture, cache_file, gen)
        return
    _POOL.submit(_fetch_and_apply, picture, client, server_path, cache_file, gen)


def _fetch_and_apply(
    picture: Gtk.Picture,
    client: GoldfishClient,
    server_path: str,
    cache_file,
    gen: int,
) -> None:
    # Vor dem Netzwerkzugriff prüfen: wer lange in der Warteschlange stand,
    # zeigt womöglich längst ein anderes Item und kann sich die Anfrage sparen.
    if getattr(picture, "_goldfish_poster_gen", 0) != gen:
        return
    data = client.fetch_bytes(server_path)
    if not data:
        return
    try:
        cache_file.write_bytes(data)
    except OSError:
        pass  # Cache beschleunigt nur — z. B. Platte voll ist kein Fehler
    GLib.idle_add(_apply_from_bytes, picture, data, gen)


def _texture_from_bytes(data: bytes) -> Gdk.Texture | None:
    """Dekodiert und skaliert auf `_DECODE_WIDTH` herunter.

    Über `new_from_stream_at_scale` und nicht über `PixbufLoader.set_size`:
    letzteres verlangt BEIDE Maße >= 0 und wirft bei einer -1 für die Höhe ein
    `gdk_pixbuf_loader_set_size: assertion 'width >= 0 && height >= 0' failed`
    (beim ersten Rastertest genau so passiert, drei Meldungen pro Poster).
    `new_from_stream_at_scale` erlaubt -1 für eine Dimension und rechnet sie
    bei `preserve_aspect_ratio=True` selbst aus — das Seitenverhältnis des
    Originals ist hier ja gerade unbekannt."""
    stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_stream_at_scale(stream, _DECODE_WIDTH, -1, True, None)
    except GLib.Error:
        return None
    finally:
        stream.close()
    if pixbuf is None:
        return None
    return Gdk.Texture.new_for_pixbuf(pixbuf)


def _apply_from_bytes(picture: Gtk.Picture, data: bytes, gen: int) -> bool:
    if getattr(picture, "_goldfish_poster_gen", 0) != gen:
        return False  # Widget zeigt inzwischen ein anderes Item
    texture = _texture_from_bytes(data)
    if texture is not None:
        picture.set_paintable(texture)
    return False  # GLib.idle_add: einmalig ausführen


def _apply_from_file(picture: Gtk.Picture, path, gen: int) -> None:
    if getattr(picture, "_goldfish_poster_gen", 0) != gen:
        return
    try:
        data = path.read_bytes()
    except OSError:
        return
    texture = _texture_from_bytes(data)
    if texture is not None:
        picture.set_paintable(texture)
