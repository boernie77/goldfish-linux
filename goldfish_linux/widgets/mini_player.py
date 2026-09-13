"""Abspielleiste am unteren Fensterrand.

Sitzt bewusst AUSSERHALB des Navigationsstapels — als Geschwister neben der
geteilten Ansicht. Nur so übersteht sie jeden Wechsel von Bibliothek und
Ansicht, statt beim Navigieren mit dem Seiteninhalt zu verschwinden. Im Browser
liegt sie aus demselben Grund außerhalb des Rasters.

Sie zeigt sich erst, wenn etwas läuft, und verschwindet wieder, wenn die
Warteschlange leer ist.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from gi.repository import Gio, GObject  # noqa: E402

from ..formatting import format_duration  # noqa: E402
from ..music_player import MusicPlayer  # noqa: E402
from .poster import load_poster_async  # noqa: E402

_TICK_MS = 250
_COVER = 48

_CSS = b"""
.gf-mini-bar {
  background-color: alpha(@window_fg_color, 0.06);
  border-top: 1px solid alpha(@window_fg_color, 0.12);
  padding: 6px 12px;
}
.gf-mini-title { font-weight: 500; }
.gf-mini-artist { font-size: 0.85rem; opacity: 0.65; }
.gf-mini-time { font-family: monospace; font-size: 0.82rem; opacity: 0.8; }
.gf-mini-cover { border-radius: 4px; background-color: alpha(@window_fg_color, 0.1); }
"""

_css_loaded = False


class _QueueRow(GObject.Object):
    """Eine Zeile der Warteschlange. `Gtk.ListView` nimmt nur GObjects."""

    __gtype_name__ = "GfQueueRow"

    def __init__(self, index: int, track: dict) -> None:
        super().__init__()
        self.index = index
        self.track = track


def _queue_icon() -> str:
    """Symbol für die Warteschlange — eine nummerierte Liste. ("music-queue"
    kennt nur Yaru und zeigt dort einen Abspiel-Kasten, der eher nach Video
    aussieht; deshalb erst an zweiter Stelle.)"""
    from gi.repository import Gdk

    display = Gdk.Display.get_default()
    names = ("view-list-ordered-symbolic", "music-queue-symbolic", "view-list-symbolic")
    if display is not None:
        theme = Gtk.IconTheme.get_for_display(display)
        for name in names:
            if theme.has_icon(name):
                return name
    return names[-1]


def _ensure_css() -> None:
    global _css_loaded
    if _css_loaded:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_loaded = True


class MiniPlayer(Gtk.Box):
    def __init__(self, player: MusicPlayer) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        _ensure_css()
        self.add_css_class("gf-mini-bar")
        self.player = player
        self.player.on_change = self.refresh
        self._seeking = False
        self.set_visible(False)

        self.cover = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        self.cover.set_size_request(_COVER, _COVER)
        self.cover.add_css_class("gf-mini-cover")
        self.cover.set_overflow(Gtk.Overflow.HIDDEN)
        self.append(self.cover)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, width_request=200)
        self.title_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1, width_request=200)
        self.title_label.add_css_class("gf-mini-title")
        text.append(self.title_label)
        self.artist_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1, width_request=200)
        self.artist_label.add_css_class("gf-mini-artist")
        text.append(self.artist_label)
        self.append(text)

        self.prev_button = Gtk.Button(icon_name="media-skip-backward-symbolic", has_frame=False, tooltip_text="Vorheriger Titel")
        self.prev_button.connect("clicked", lambda *_: self.player.previous_track())
        self.append(self.prev_button)

        self.play_button = Gtk.Button(icon_name="media-playback-start-symbolic", has_frame=False)
        self.play_button.connect("clicked", lambda *_: self.player.toggle_play())
        self.append(self.play_button)

        self.next_button = Gtk.Button(icon_name="media-skip-forward-symbolic", has_frame=False, tooltip_text="Nächster Titel")
        self.next_button.connect("clicked", lambda *_: self.player.next_track())
        self.append(self.next_button)

        self.position_label = Gtk.Label(label="0:00", valign=Gtk.Align.CENTER)
        self.position_label.add_css_class("gf-mini-time")
        self.append(self.position_label)

        self.scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            adjustment=Gtk.Adjustment(lower=0, upper=1, value=0),
            draw_value=False,
            hexpand=True,
            valign=Gtk.Align.CENTER,
        )
        self.scale.connect("change-value", self._on_seek)
        press = Gtk.GestureClick()
        press.connect("pressed", lambda *_: setattr(self, "_seeking", True))
        press.connect("released", lambda *_: setattr(self, "_seeking", False))
        self.scale.add_controller(press)
        self.append(self.scale)

        self.duration_label = Gtk.Label(label="0:00", valign=Gtk.Align.CENTER)
        self.duration_label.add_css_class("gf-mini-time")
        self.append(self.duration_label)

        self.shuffle_button = Gtk.Button(icon_name="media-playlist-shuffle-symbolic", has_frame=False, tooltip_text="Warteschlange mischen")
        self.shuffle_button.connect("clicked", lambda *_: self.player.shuffle())
        self.append(self.shuffle_button)

        # Die Warteschlange war da, aber niemand fand sie ("wo ist die
        # Schlange?"): ein Listensymbol zwischen lauter anderen Symbolen sagt
        # nicht, dass dahinter die Titelfolge steckt. Jetzt mit der Anzahl
        # daneben — eine Zahl fällt auf und sagt gleich, wie viel drin ist.
        self.queue_count = Gtk.Label(label="0")
        self.queue_count.add_css_class("numeric")
        queue_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        queue_box.append(Gtk.Image.new_from_icon_name(_queue_icon()))
        queue_box.append(self.queue_count)
        self.queue_button = Gtk.MenuButton(tooltip_text="Warteschlange")
        self.queue_button.set_child(queue_box)
        self.queue_popover = Gtk.Popover()
        self.queue_button.set_popover(self.queue_popover)
        self.queue_popover.connect("show", lambda *_: self._fill_queue())
        self.append(self.queue_button)

        self.volume = Gtk.VolumeButton(valign=Gtk.Align.CENTER)
        self.volume.set_value(1.0)
        self.volume.connect("value-changed", lambda _b, v: self.player.set_volume(v))
        self.append(self.volume)

        close = Gtk.Button(icon_name="window-close-symbolic", has_frame=False, tooltip_text="Wiedergabe beenden")
        close.connect("clicked", lambda *_: self.player.stop())
        self.append(close)

        GLib.timeout_add(_TICK_MS, self._tick)

    # -- Auffrischen -----------------------------------------------------

    def refresh(self) -> None:
        item = self.player.current
        if item is None:
            self.set_visible(False)
            return
        self.set_visible(True)
        self.title_label.set_text(item.get("title") or "")
        parts = [p for p in (item.get("artist"), item.get("album")) if p]
        self.artist_label.set_text(" – ".join(parts))
        album_id = item.get("musicAlbumId")
        load_poster_async(
            self.cover,
            self.player.client,
            self.player.client.album_cover_path(int(album_id)) if album_id else None,
            decode_width=_COVER * 2,
        )
        self._update_play_icon()
        self._update_queue_count()

    def _update_queue_count(self) -> None:
        count = len(self.player.queue)
        self.queue_count.set_label(str(count))
        self.queue_button.set_tooltip_text(
            f"Warteschlange · {count} Titel — hier einzelne Titel entfernen oder alles leeren"
        )

    def _update_play_icon(self) -> None:
        playing = self.player.playing
        self.play_button.set_icon_name("media-playback-pause-symbolic" if playing else "media-playback-start-symbolic")
        self.play_button.set_tooltip_text("Anhalten" if playing else "Abspielen")

    def _tick(self) -> bool:
        if not self.get_visible():
            return True
        duration = self.player.duration
        position = self.player.position
        if duration > 0 and not self._seeking:
            self.scale.get_adjustment().set_upper(duration)
            self.scale.get_adjustment().set_value(min(position, duration))
        self.position_label.set_label(format_duration(position))
        self.duration_label.set_label(format_duration(duration))
        self._update_play_icon()
        # Anhängen an die Warteschlange meldet sich nicht eigens — der Zähler
        # kommt deshalb mit dem Sekundentakt mit.
        self._update_queue_count()
        return True

    def _on_seek(self, _scale, _scroll, value: float) -> bool:
        self.player.seek(value)
        return False

    # -- Warteschlange ---------------------------------------------------

    def _fill_queue(self) -> None:
        """Das Fenster zur Warteschlange.

        **Die Liste MUSS wiederverwenden** (`Gtk.ListView` über einem
        `Gio.ListStore`): eine Zeile je Titel zu bauen, ging bei einer
        gemischten Bibliothek mit tausenden Titeln schlicht nicht mehr auf —
        das Fenster öffnete gar nicht erst. Gebaut wird jetzt nur, was man
        sieht."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        box.set_size_request(380, -1)

        head_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        heading = Gtk.Label(label=f"Warteschlange · {len(self.player.queue)} Titel", xalign=0, hexpand=True)
        heading.add_css_class("heading")
        head_row.append(heading)
        clear = Gtk.Button(
            label="Leeren",
            valign=Gtk.Align.CENTER,
            tooltip_text="Warteschlange leeren (beendet die Wiedergabe)",
        )
        clear.add_css_class("destructive-action")
        clear.connect("clicked", lambda *_: self._clear())
        head_row.append(clear)
        box.append(head_row)

        self._queue_store = Gio.ListStore.new(_QueueRow)
        for i, track in enumerate(self.player.queue):
            self._queue_store.append(_QueueRow(i, track))

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._queue_setup)
        factory.connect("bind", self._queue_bind)
        view = Gtk.ListView(model=Gtk.NoSelection.new(self._queue_store), factory=factory)
        view.add_css_class("navigation-sidebar")

        scroll = Gtk.ScrolledWindow(
            child=view,
            propagate_natural_height=True,
            min_content_height=240,
            max_content_height=380,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        box.append(scroll)
        self.queue_popover.set_child(box)
        # Zur laufenden Stelle springen, damit man nicht erst scrollen muss.
        if 0 <= self.player.index < len(self.player.queue):
            GLib.idle_add(lambda: (view.scroll_to(self.player.index, Gtk.ListScrollFlags.NONE, None), False)[1])

    def _queue_setup(self, _factory, list_item: Gtk.ListItem) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_top=2, margin_bottom=2)
        play = Gtk.Button(icon_name="media-playback-start-symbolic", has_frame=False, valign=Gtk.Align.CENTER)
        play.set_tooltip_text("Diesen Titel abspielen")
        row.append(play)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, single_line_mode=True)
        artist = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, single_line_mode=True)
        artist.add_css_class("dim-label")
        artist.add_css_class("caption")
        text.append(title)
        text.append(artist)
        row.append(text)
        remove = Gtk.Button(icon_name="list-remove-symbolic", has_frame=False, valign=Gtk.Align.CENTER)
        remove.set_tooltip_text("Aus der Warteschlange nehmen")
        row.append(remove)
        list_item.set_child(row)

    def _queue_bind(self, _factory, list_item: Gtk.ListItem) -> None:
        entry = list_item.get_item()
        row = list_item.get_child()
        play = row.get_first_child()
        text = play.get_next_sibling()
        remove = text.get_next_sibling()
        title = text.get_first_child()
        artist = title.get_next_sibling()

        running = entry.index == self.player.index
        title.set_label(("▶  " if running else "") + (entry.track.get("title") or ""))
        artist.set_label(entry.track.get("artist") or "")
        play.set_sensitive(not running)

        # Frisch verbinden — die Zeile wird für andere Titel wiederverwendet.
        for button, handler in ((play, "_queue_play_handler"), (remove, "_queue_remove_handler")):
            old = getattr(button, handler, 0)
            if old:
                button.disconnect(old)
        setattr(play, "_queue_play_handler", play.connect("clicked", lambda _b, i=entry.index: self._jump(i)))
        setattr(remove, "_queue_remove_handler", remove.connect("clicked", lambda _b, i=entry.index: self._remove(i)))

    def _jump(self, index: int) -> None:
        self.player.play_index(index)
        self.queue_popover.popdown()

    def _remove(self, index: int) -> None:
        self.player.remove_index(index)
        self._fill_queue()

    def _clear(self) -> None:
        """Warteschlange leeren.

        `MusicPlayer.stop()` macht genau das — Wiedergabe beenden UND die Liste
        leeren; danach verschwindet die Leiste von selbst, weil sie sich an
        einer leeren Warteschlange ausblendet."""
        self.queue_popover.popdown()
        self.player.stop()
