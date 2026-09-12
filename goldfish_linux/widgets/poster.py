"""Asynchrones Laden von Postern/Thumbnails in ein Gtk.Picture.

Lädt im Hintergrund-Thread per requests (über GoldfishClient), cached die
Bytes lokal (Dateiname = Hash des Server-Pfads) und setzt das Ergebnis via
GLib.idle_add auf das Picture-Widget — GTK-Widgets dürfen nur vom Main-Thread
aus angefasst werden.
"""

from __future__ import annotations

import hashlib
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from .. import config  # noqa: E402
from ..api import GoldfishClient  # noqa: E402


def _cache_path(server_path: str):
    digest = hashlib.sha1(server_path.encode("utf-8")).hexdigest()
    return config.POSTER_CACHE_DIR / f"{digest}.img"


def load_poster_async(picture: Gtk.Picture, client: GoldfishClient, server_path: str | None) -> None:
    """Setzt sofort keinen Platzhalter (der Aufrufer setzt vorher ein Icon/
    leeres Bild), lädt dann asynchron nach. Mehrfacher Aufruf auf demselben
    Widget ist unschädlich — das jeweils zuletzt fertige Ergebnis gewinnt,
    Reihenfolge spielt bei Postern keine Rolle (kein schnelles Neu-Scrollen
    mit Recycling-Widgets in dieser einfachen v1-Listenansicht)."""
    if not server_path:
        return
    config.ensure_dirs()
    cache_file = _cache_path(server_path)
    if cache_file.exists():
        _apply_texture_from_file(picture, cache_file)
        return
    threading.Thread(
        target=_fetch_and_apply,
        args=(picture, client, server_path, cache_file),
        daemon=True,
    ).start()


def _fetch_and_apply(picture: Gtk.Picture, client: GoldfishClient, server_path: str, cache_file) -> None:
    data = client.fetch_bytes(server_path)
    if not data:
        return
    try:
        cache_file.write_bytes(data)
    except OSError:
        pass
    GLib.idle_add(_apply_texture_from_bytes, picture, data)


def _apply_texture_from_bytes(picture: Gtk.Picture, data: bytes) -> bool:
    try:
        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        pixbuf = loader.get_pixbuf()
        if pixbuf:
            picture.set_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))
    except GLib.Error:
        pass
    return False  # GLib.idle_add: einmalig ausführen


def _apply_texture_from_file(picture: Gtk.Picture, path) -> None:
    try:
        texture = Gdk.Texture.new_from_filename(str(path))
        picture.set_paintable(texture)
    except GLib.Error:
        pass
