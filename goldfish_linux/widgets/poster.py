"""Asynchrones Laden von Postern/Thumbnails in ein Gtk.Picture.

Lädt im Hintergrund-Thread per requests (über GoldfishClient), legt die Bytes
lokal ab (Dateiname = Hash des Server-Pfads) und setzt das Ergebnis via
GLib.idle_add auf das Picture-Widget — GTK-Widgets dürfen nur vom Hauptablauf
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

**Drei Regeln fürs Tempo**, alle nachträglich eingezogen, weil sich das
Blättern zäh anfühlte:

1. **Fertige Texturen bleiben im Speicher** (`_TEXTURES`). Beim Scrollen wird
   dieselbe Kachel dutzende Male neu belegt; ohne Zwischenspeicher würde jedes
   Mal dieselbe Datei erneut gelesen und dekodiert. Begrenzt auf
   `_TEXTURE_CACHE_MAX` Einträge (älteste fallen heraus), weil jede Textur
   echten Speicher hält.
2. **Dekodiert wird NIE im Hauptablauf**, auch nicht bei einem Treffer im
   Dateizwischenspeicher. Ein JPEG zu lesen und zu skalieren kostet einige
   Millisekunden — bei einer Rasterzeile aus acht Kacheln reicht das für ein
   sichtbares Stocken. Nur das Setzen der fertigen Textur läuft im Hauptablauf.
3. **In einem Raster wird nur geladen, was man sehen kann.** Das ist nicht
   dasselbe wie "was gebunden ist": `Gtk.GridView` legt für eine lange Liste
   rund 385 Kacheln an, unabhängig von der Fenstergröße — nachgemessen sowohl
   hier als auch mit einem nackten GridView ohne unseren Code, bei 626 Pixel
   Höhe und 301 Pixel hohen Zeilen (also etwa zwölf wirklich sichtbaren
   Kacheln). Ohne diese Regel stellte jeder Aufruf einer Bibliothek 385
   Bildanfragen, von denen 370 niemand sieht; die zwölf sichtbaren standen
   entsprechend spät. Der Browser erreicht dasselbe mit `loading="lazy"`.

**⚠ Wer die Sichtbarkeit prüft, darf NICHT die Elternkette der Kachel
ablaufen.** Das war der erste Ansatz (`picture.get_parent()` hoch bis zur
nächsten Bildlaufleiste) und er hat GTK zum Absturz gebracht: oberhalb einer
Kachel liegen die internen Widgets des GridView (GtkListItemWidget), und schon
das Anfassen aus Python heraus erzeugt dafür Hüllobjekte, die GTKs eigenem
Aufräumen in die Quere kommen — reproduzierbar erst
`gtk_widget_insert_after: assertion 'GTK_IS_WIDGET (widget)' failed` beim
Scrollen, dann `Gtk:ERROR ... gtk_list_factory_widget_teardown_factory:
assertion failed: (priv->object == NULL)` und ein Speicherauszug. Deshalb gibt
das Raster seine Bildlaufleiste ausdrücklich mit (`scroller=`); gerechnet wird
nur mit `compute_bounds()` zwischen Kachel und dieser Leiste, was komplett in
C bleibt.
"""

from __future__ import annotations

import pathlib
import threading
import weakref
from collections import OrderedDict
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
# einer großen Bibliothek würde sonst für jede vorbeiziehende Kachel ein
# eigener Thread starten und gegen den Server feuern. Sechs gleichzeitige
# Anfragen füllen die Leitung gut aus und halten die Last für den Server
# berechenbar.
_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="poster")

# Eine Poster-Textur in Kachelbreite belegt gut 130 KB, 400 Einträge also etwa
# 50 MB — das deckt mehrere Bildschirmseiten Blättern ab und bleibt für einen
# Desktop-Rechner unauffällig.
_TEXTURE_CACHE_MAX = 400
_TEXTURES: "OrderedDict[tuple[str, int], Gdk.Texture]" = OrderedDict()
# Wird aus den Ladefäden geschrieben und aus dem Hauptablauf gelesen.
_TEXTURES_LOCK = threading.Lock()

# Offene Bildwünsche von Kacheln in einem Raster, bis sie in Sicht kommen.
# Schlüssel ist die Identität des Gtk.Picture. Ausschließlich im Hauptablauf
# angefasst.
_PENDING: dict[int, "_Request"] = {}
_PUMP_SCHEDULED = False

# Wie weit über den sichtbaren Bereich hinaus vorgeladen wird: ein
# Bildschirmabschnitt in jede Richtung, damit beim Scrollen schon etwas da ist.
_PRELOAD_FACTOR = 1.0


class _Request:
    """Ein noch nicht abgeschickter Bildwunsch."""

    __slots__ = ("picture", "client", "path", "cache_file", "gen", "width", "scroller")

    def __init__(self, picture, client, path, cache_file, gen, width, scroller) -> None:
        self.picture = picture
        self.client = client
        self.path = path
        self.cache_file = cache_file
        self.gen = gen
        self.width = width
        # Schwach, damit ein abgeräumtes Raster nicht an einem offenen Wunsch
        # hängen bleibt.
        self.scroller = weakref.ref(scroller)


def _cache_path(server_path: str):
    # Gemeinsam mit `local_library.prefetch_thumbnails`, das im Vorgriff in
    # denselben Ablageort schreibt.
    return config.poster_cache_file(server_path)


def _next_generation(picture: Gtk.Picture) -> int:
    gen = getattr(picture, "_goldfish_poster_gen", 0) + 1
    picture._goldfish_poster_gen = gen
    return gen


def _cached_texture(key: tuple[str, int]) -> Gdk.Texture | None:
    with _TEXTURES_LOCK:
        texture = _TEXTURES.get(key)
        if texture is not None:
            _TEXTURES.move_to_end(key)  # zuletzt gebraucht = zuletzt hinaus
        return texture


def _remember_texture(key: tuple[str, int], texture: Gdk.Texture) -> None:
    with _TEXTURES_LOCK:
        _TEXTURES[key] = texture
        _TEXTURES.move_to_end(key)
        while len(_TEXTURES) > _TEXTURE_CACHE_MAX:
            _TEXTURES.popitem(last=False)


def load_poster_async(
    picture: Gtk.Picture,
    client: GoldfishClient,
    server_path: str | None,
    decode_width: int = _DECODE_WIDTH,
    scroller: Gtk.ScrolledWindow | None = None,
) -> None:
    """Lädt das Poster für `server_path` in `picture`.

    Bei Wiederverwendung des Widgets einfach erneut aufrufen, auch mit `None`,
    um ein noch unterwegs befindliches Ergebnis zu verwerfen.

    `decode_width` sollte der Anzeigebreite entsprechen: `Gtk.Picture` meldet
    die Pixelbreite des geladenen Bildes als seine natürliche Breite, und ein
    fließendes Raster richtet die Kachelgröße danach aus. Ein zu groß
    dekodiertes Bild macht die Kachel also breiter als gewollt — genau so
    passten in der Folgenübersicht nur zwei Kacheln pro Zeile statt drei.

    `scroller` ist die Bildlaufleiste, in der die Kachel liegt. Wird sie
    mitgegeben, wartet der Wunsch, bis die Kachel in Sicht ist (Regel 3 im
    Dateikopf); ohne sie wird sofort geladen — richtig für alles, was ohnehin
    als Ganzes auf dem Schirm steht (Detailansicht, Besetzungsleiste,
    Abspielleiste)."""
    gen = _next_generation(picture)
    _PENDING.pop(id(picture), None)
    if not server_path:
        picture.set_paintable(None)
        return
    key = (server_path, decode_width)
    texture = _cached_texture(key)
    if texture is not None:
        # Der häufigste Fall beim Blättern: nichts zu lesen, nichts zu
        # dekodieren, die Kachel steht sofort.
        picture.set_paintable(texture)
        return
    # Solange nichts da ist, den Platzhalter zeigen — sonst bliebe beim
    # Wiederverwenden der Kachel das Poster des vorherigen Titels stehen.
    picture.set_paintable(None)
    config.ensure_dirs()
    cache_file = _cache_path(server_path)
    if scroller is None:
        _POOL.submit(_fetch_and_apply, picture, client, server_path, cache_file, gen, decode_width)
        return
    _PENDING[id(picture)] = _Request(picture, client, server_path, cache_file, gen, decode_width, scroller)
    _watch_map(picture)
    _watch_scrolling(scroller)
    _schedule_pump()


def _watch_map(picture: Gtk.Picture) -> None:
    """Sobald die Kachel tatsächlich dargestellt wird, erneut prüfen.

    Nötig, weil eine gerade aufgeschlagene Seite ihre Widgets erst nach der
    Übergangsbewegung darstellt: zum Zeitpunkt der ersten Prüfung gilt die
    Kachel als nicht sichtbar, und ohne dieses Signal bliebe ihr Bild aus, bis
    jemand scrollt."""
    if getattr(picture, "_goldfish_map_watched", False):
        return
    picture._goldfish_map_watched = True
    picture.connect("map", lambda *_: _schedule_pump())


def _watch_scrolling(scroller: Gtk.ScrolledWindow) -> None:
    """Beim Scrollen erneut prüfen, was inzwischen in Sicht gekommen ist.
    Pro Leiste genau einmal verbunden."""
    for adjustment in (scroller.get_vadjustment(), scroller.get_hadjustment()):
        if adjustment is not None and not getattr(adjustment, "_goldfish_pump", False):
            adjustment._goldfish_pump = True
            adjustment.connect("value-changed", lambda *_: _schedule_pump())


def _schedule_pump() -> None:
    global _PUMP_SCHEDULED
    if _PUMP_SCHEDULED:
        return
    _PUMP_SCHEDULED = True
    # Nicht sofort, sondern nach einem Wimpernschlag: beim Aufbau einer Seite
    # kommen hunderte Wünsche in Folge, die sollen gemeinsam bewertet werden.
    # Außerdem läuft so nichts davon innerhalb der Scroll-Meldung selbst.
    GLib.timeout_add(50, _pump)


def _pump() -> bool:
    """Prüft alle offenen Wünsche und schickt die sichtbaren los."""
    global _PUMP_SCHEDULED
    _PUMP_SCHEDULED = False
    retry = False
    for key, request in list(_PENDING.items()):
        picture = request.picture
        scroller = request.scroller()
        if scroller is None or picture.get_root() is None:
            del _PENDING[key]  # Raster oder Kachel ist weg
            continue
        if getattr(picture, "_goldfish_poster_gen", 0) != request.gen:
            del _PENDING[key]  # Kachel zeigt inzwischen etwas anderes
            continue
        texture = _cached_texture((request.path, request.width))
        if texture is not None:
            del _PENDING[key]
            picture.set_paintable(texture)
            continue
        visible = _visible_enough(picture, scroller)
        if visible is None:
            # Größen stehen noch nicht — gleich nochmal nachsehen. Ohne diese
            # Wartestelle gilt beim Seitenaufbau ALLES als sichtbar (jedes
            # Rechteck ist dann leer) und die Sparsamkeit wäre wirkungslos.
            retry = True
            continue
        if not visible:
            continue  # bleibt liegen, bis gescrollt oder dargestellt wird
        del _PENDING[key]
        _POOL.submit(
            _fetch_and_apply,
            picture,
            request.client,
            request.path,
            request.cache_file,
            request.gen,
            request.width,
        )
    if retry:
        _schedule_pump()
    return False  # einmalig; erneut geplant wird beim Scrollen oder neuen Wunsch


def _visible_enough(picture: Gtk.Picture, scroller: Gtk.ScrolledWindow) -> bool | None:
    """Liegt die Kachel im Sichtbereich der Leiste (plus einem Abschnitt
    Vorlauf)?

    Rückgabe `None` heißt "noch nicht entscheidbar": solange das Fenster seine
    Größen nicht zugeteilt hat, ist jedes Rechteck leer und alles sähe sichtbar
    aus."""
    if not picture.get_mapped():
        return False
    if picture.get_height() <= 0:
        return None
    width, height = scroller.get_width(), scroller.get_height()
    if width <= 0 or height <= 0:
        return None
    ok, rect = picture.compute_bounds(scroller)
    if not ok:
        return True  # keine Auskunft möglich: lieber laden als nie laden
    margin_x, margin_y = width * _PRELOAD_FACTOR, height * _PRELOAD_FACTOR
    if rect.origin.x + rect.size.width < -margin_x or rect.origin.x > width + margin_x:
        return False
    if rect.origin.y + rect.size.height < -margin_y or rect.origin.y > height + margin_y:
        return False
    return True


def _fetch_and_apply(
    picture: Gtk.Picture,
    client: GoldfishClient,
    server_path: str,
    cache_file,
    gen: int,
    decode_width: int = _DECODE_WIDTH,
) -> None:
    # Vor dem Netzwerkzugriff prüfen: wer lange in der Warteschlange stand,
    # zeigt womöglich längst ein anderes Item und kann sich die Anfrage sparen.
    if getattr(picture, "_goldfish_poster_gen", 0) != gen:
        return
    key = (server_path, decode_width)
    data: bytes | None = None
    if cache_file.exists():
        try:
            data = cache_file.read_bytes()
        except OSError:
            data = None
    if data is None:
        # Vier Quellen: ein selbst zu erzeugendes Vorschaubild einer lokalen
        # Datei (`gstthumb://`), eine Datei auf der Platte (Vorschaubild des
        # Dateimanagers), eine fremde Adresse (TMDB liefert Standbilder und
        # Poster direkt aus) oder ein Pfad am eigenen Server.
        if server_path.startswith("gstthumb://"):
            # Vorschaubild einer lokalen Datei selbst erzeugen (GStreamer).
            # Erst hier, im Ladefaden, und danach wie jedes andere Bild im
            # Dateizwischenspeicher — erzeugt wird es also nur einmal.
            from ..local_library import thumbnail_for

            data = thumbnail_for(server_path[len("gstthumb://") :])
            if not data:
                return
        elif server_path.startswith("file://"):
            try:
                data = pathlib.Path(server_path[7:]).read_bytes()
            except OSError:
                return
        elif server_path.startswith(("http://", "https://")):
            data = client.fetch_external_bytes(server_path)
        else:
            data = client.fetch_bytes(server_path)
        if not data:
            return
        try:
            cache_file.write_bytes(data)
        except OSError:
            pass  # Zwischenspeicher beschleunigt nur — volle Platte ist kein Fehler
    texture = _texture_from_bytes(data, decode_width)
    if texture is None:
        return
    _remember_texture(key, texture)
    GLib.idle_add(_apply_texture, picture, texture, gen)


def _texture_from_bytes(data: bytes, decode_width: int = _DECODE_WIDTH) -> Gdk.Texture | None:
    """Dekodiert und skaliert auf `decode_width` herunter.

    Über `new_from_stream_at_scale` und nicht über `PixbufLoader.set_size`:
    letzteres verlangt BEIDE Maße >= 0 und wirft bei einer -1 für die Höhe ein
    `gdk_pixbuf_loader_set_size: assertion 'width >= 0 && height >= 0' failed`
    (beim ersten Rastertest genau so passiert, drei Meldungen pro Poster).
    `new_from_stream_at_scale` erlaubt -1 für eine Dimension und rechnet sie
    bei `preserve_aspect_ratio=True` selbst aus — das Seitenverhältnis des
    Originals ist hier ja gerade unbekannt."""
    stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_stream_at_scale(stream, decode_width, -1, True, None)
    except GLib.Error:
        return None
    finally:
        stream.close()
    if pixbuf is None:
        return None
    return Gdk.Texture.new_for_pixbuf(pixbuf)


def _apply_texture(picture: Gtk.Picture, texture: Gdk.Texture, gen: int) -> bool:
    """Läuft im Hauptablauf und tut nur noch das Nötigste: die fertige Textur
    setzen, falls das Widget inzwischen nicht auf ein anderes Item umgestellt
    oder abgeräumt wurde."""
    if picture.get_root() is None:
        return False
    if getattr(picture, "_goldfish_poster_gen", 0) == gen:
        picture.set_paintable(texture)
    return False  # GLib.idle_add: einmalig ausführen
