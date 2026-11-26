"""Bildbetrachter für Bilder-Bibliotheken (Server 1.6.12+).

Gegenstück zu `imageviewer.js` im Browser: ein Bild groß, ←/→ blättern,
Leertaste startet/stoppt die Diashow (5 s je Bild), Esc geht zurück.

Bewusst eine Seite auf dem Adw.NavigationView und KEIN eigenes Fenster: ein
zweites Toplevel-Fenster ist in dieser App schon einmal zur Falle geworden
(Dialog unsichtbar dahinter, siehe AGENTS.md „nur EIN Wiedergabefenster").

Das Original wird im Hintergrund geladen und auf höchstens 2560 px Breite
dekodiert — Kamera-Fotos mit 6000 px kosten sonst unnötig Speicher.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from ..widgets.poster import _texture_from_bytes  # noqa: E402

_DECODE_WIDTH = 2560
_SLIDESHOW_MS = 5000


class ImageViewerPage(Adw.NavigationPage):
    def __init__(self, ctx, nav_view: Adw.NavigationView, images: list[dict], index: int):
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)
        super().__init__(title="Bild", tag=f"image-viewer-{id(self)}", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.images = images
        self.index = max(0, min(index, len(images) - 1))
        self._gen = 0
        self._slideshow = 0

        self.prev_button = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Vorheriges Bild (←)")
        self.prev_button.connect("clicked", lambda *_: self.step(-1))
        self.next_button = Gtk.Button(icon_name="go-next-symbolic", tooltip_text="Nächstes Bild (→)")
        self.next_button.connect("clicked", lambda *_: self.step(1))
        self.play_button = Gtk.Button(icon_name="media-playback-start-symbolic", tooltip_text="Diashow (Leertaste)")
        self.play_button.connect("clicked", lambda *_: self.toggle_slideshow())
        header.pack_start(self.prev_button)
        header.pack_start(self.next_button)
        header.pack_end(self.play_button)

        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True)
        self.spinner = Gtk.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, width_request=48, height_request=48)
        overlay = Gtk.Overlay(child=self.picture)
        overlay.add_overlay(self.spinner)
        self.caption = Gtk.Label(margin_top=6, margin_bottom=8, ellipsize=Pango.EllipsizeMode.MIDDLE)
        self.caption.add_css_class("dim-label")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(overlay)
        box.append(self.caption)
        toolbar_view.set_content(box)

        # Wischen/Klick: Klick auf die linke Bildhälfte = zurück, rechte = weiter.
        click = Gtk.GestureClick()
        click.connect("released", self._on_click)
        self.picture.add_controller(click)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.connect("hidden", lambda *_: self._stop_slideshow())

        self._show()

    # -- Anzeige ---------------------------------------------------------

    def _show(self) -> None:
        if not self.images:
            return
        image = self.images[self.index]
        title = image.get("title") or (image.get("relPath") or "").rsplit("/", 1)[-1]
        self.set_title(f"{self.index + 1} / {len(self.images)}")
        self.caption.set_text(title)
        self.prev_button.set_sensitive(len(self.images) > 1)
        self.next_button.set_sensitive(len(self.images) > 1)
        self._gen += 1
        gen = self._gen
        self.spinner.set_visible(True)
        self.spinner.start()
        image_id = image.get("id")

        def worker() -> None:
            data = self.ctx.client.fetch_bytes(f"/api/images/{image_id}/file", timeout=60)
            texture = _texture_from_bytes(data, _DECODE_WIDTH) if data else None
            GLib.idle_add(self._apply, gen, texture)

        threading.Thread(target=worker, daemon=True).start()

    def _apply(self, gen: int, texture: Gdk.Texture | None) -> bool:
        if gen != self._gen:
            return False  # inzwischen weitergeblättert
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.picture.set_paintable(texture)
        if texture is None:
            self.caption.set_text(self.caption.get_text() + " — konnte nicht geladen werden")
        return False

    def step(self, delta: int) -> None:
        if not self.images:
            return
        self.index = (self.index + delta) % len(self.images)
        self._show()

    # -- Diashow ---------------------------------------------------------

    def toggle_slideshow(self) -> None:
        if self._slideshow:
            self._stop_slideshow()
            return
        self._slideshow = GLib.timeout_add(_SLIDESHOW_MS, self._slideshow_tick)
        self.play_button.set_icon_name("media-playback-pause-symbolic")

    def _slideshow_tick(self) -> bool:
        self.step(1)
        return True

    def _stop_slideshow(self) -> None:
        if self._slideshow:
            GLib.source_remove(self._slideshow)
            self._slideshow = 0
        self.play_button.set_icon_name("media-playback-start-symbolic")

    # -- Eingaben --------------------------------------------------------

    def _on_click(self, _gesture, _n, x: float, _y: float) -> None:
        width = self.picture.get_width() or 1
        self.step(-1 if x < width / 2 else 1)

    def _on_key(self, _ctrl, keyval: int, _code: int, _state) -> bool:
        if keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left):
            self.step(-1)
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right):
            self.step(1)
        elif keyval == Gdk.KEY_space:
            self.toggle_slideshow()
        elif keyval == Gdk.KEY_Escape:
            self._stop_slideshow()
            self.nav_view.pop()
        else:
            return False
        return True
