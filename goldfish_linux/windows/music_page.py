"""Musikbibliothek: Albenübersicht und Titelliste eines Albums.

Die Alben sind kein Abbild der Ordnerstruktur — der Server fasst sie über
Tags und den gemeinsamen Ordner zu einer Einheit zusammen
(`GroupMusicAlbums`). Deshalb eine eigene Ansicht und nicht der
Ordner-Browser: eine Musikbibliothek hat hier 2717 Alben aus mehr als 7000
Titeln, und über Ordner käme man nur mühsam dorthin.

Wer die Ordner sehen will, kommt weiterhin über den Bibliotheks-Browser
dorthin — das ist dieselbe zweigleisige Auslegung wie im Browser.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..api import GoldfishAPIError  # noqa: E402
from ..formatting import format_duration  # noqa: E402
from ..widgets.grid import AlbumGrid  # noqa: E402
from ..widgets.poster import load_poster_async  # noqa: E402


class MusicLibraryPage(Adw.NavigationPage):
    """Albenübersicht mit Suche und Genre-Filter."""

    def __init__(self, ctx, nav_view: Adw.NavigationView, library: dict):
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        search = Gtk.SearchEntry(placeholder_text="Künstler oder Album…")
        header.set_title_widget(search)
        toolbar_view.add_top_bar(header)

        super().__init__(title=library["name"], tag=f"music-{library['id']}", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.library = library
        self.toolbar_view = toolbar_view
        self.albums: list[dict] = []
        self.search = search
        search.connect("search-changed", lambda *_: self._render())
        # Ein Raster mit Recycling, kein fließendes: eine Musikbibliothek hat
        # hier 2717 Alben, und als einzeln gebaute Kacheln kostet das 4,65
        # Sekunden blockierten Hauptablauf (nachgemessen) — in dieser Zeit
        # stand auch die laufende Wiedergabe scheinbar still.
        self.grid: AlbumGrid | None = None

        folders = Gtk.Button(icon_name="folder-symbolic", tooltip_text="Ordner durchsehen")
        folders.connect("clicked", lambda *_: self._open_folders())
        header.pack_end(folders)

        _busy(toolbar_view)
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            albums = self.ctx.client.albums(self.library["id"])
        except GoldfishAPIError as exc:
            GLib.idle_add(_error, self.toolbar_view, str(exc))
            return
        GLib.idle_add(self._apply, albums)

    def _apply(self, albums: list[dict]) -> bool:
        self.albums = albums
        self._render()
        return False

    def _render(self) -> None:
        needle = self.search.get_text().strip().lower()
        shown = [
            a
            for a in self.albums
            if not needle or needle in (a.get("album") or "").lower() or needle in (a.get("artist") or "").lower()
        ]
        if not shown:
            self.grid = None
            _error(
                self.toolbar_view,
                "Kein Album passt zur Suche." if needle else "Diese Bibliothek enthält keine Alben.",
                title="Keine Treffer" if needle else "Leer",
                icon="system-search-symbolic",
            )
            return

        if self.grid is None:
            self.grid = AlbumGrid(self.ctx.client, on_album=self._open_album)
        self.grid.set_albums(shown)
        if self.toolbar_view.get_content() is not self.grid:
            self.toolbar_view.set_content(self.grid)

    def _open_album(self, album: dict) -> None:
        self.nav_view.push(AlbumPage(self.ctx, self.nav_view, album))

    def _open_folders(self) -> None:
        from .browse_page import BrowsePage

        self.nav_view.push(BrowsePage(self.ctx, self.nav_view, self.library))


class AlbumPage(Adw.NavigationPage):
    """Titelliste eines Albums, mit Cover und den Aktionen dazu."""

    def __init__(self, ctx, nav_view: Adw.NavigationView, album: dict):
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())

        super().__init__(title=album.get("album") or "Album", tag=f"album-{album['id']}", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.album = album
        self.toolbar_view = toolbar_view
        self.tracks: list[dict] = []

        _busy(toolbar_view)
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            detail = self.ctx.client.album(int(self.album["id"]))
        except GoldfishAPIError as exc:
            GLib.idle_add(_error, self.toolbar_view, str(exc))
            return
        GLib.idle_add(self._apply, detail)

    def _apply(self, detail: dict) -> bool:
        self.album = {**self.album, **(detail.get("album") or {})}
        self.tracks = detail.get("tracks") or []

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=16, margin_bottom=24)
        box.append(self._header())
        box.append(self._track_list())
        self.toolbar_view.set_content(Gtk.ScrolledWindow(vexpand=True, child=box))
        return False

    def _header(self) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18, margin_start=16, margin_end=16)

        cover = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        cover.set_size_request(170, 170)
        cover.set_valign(Gtk.Align.START)
        cover.add_css_class("gf-card-image")
        cover.set_overflow(Gtk.Overflow.HIDDEN)
        load_poster_async(cover, self.ctx.client, self.ctx.client.album_cover_path(int(self.album["id"])), decode_width=340)
        row.append(cover)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        title = Gtk.Label(label=self.album.get("album") or "", xalign=0, wrap=True)
        title.add_css_class("title-2")
        text.append(title)

        facts = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        total = sum(float(t.get("durationSec") or 0) for t in self.tracks)
        for value in (
            self.album.get("artist") or "",
            str(self.album.get("year")) if self.album.get("year") else "",
            self.album.get("genre") or "",
            f"{len(self.tracks)} Titel",
            format_duration(total),
        ):
            if value:
                chip = Gtk.Label(label=value)
                chip.add_css_class("gf-badge")
                facts.append(chip)
        text.append(facts)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        play = Gtk.Button(label="▶ Album abspielen")
        play.add_css_class("suggested-action")
        play.add_css_class("pill")
        play.connect("clicked", lambda *_: self.ctx.music.play_queue(self.tracks, 0))
        actions.append(play)

        shuffle = Gtk.Button(label="🔀 Zufällig")
        shuffle.connect("clicked", lambda *_: self._play_shuffled())
        actions.append(shuffle)

        queue = Gtk.Button(label="➕ Anhängen")
        queue.set_tooltip_text("An die laufende Warteschlange anhängen")
        queue.connect("clicked", lambda *_: self.ctx.music.append(self.tracks))
        actions.append(queue)

        self.offline_button = Gtk.Button(label="⬇ Offline")
        self.offline_button.set_tooltip_text("Alle Titel dieses Albums herunterladen")
        self.offline_button.connect("clicked", lambda *_: self._download_album())
        actions.append(self.offline_button)

        self.fav_button = Gtk.ToggleButton(icon_name="emblem-favorite-symbolic", tooltip_text="Album als Favorit")
        self.fav_button.connect("toggled", self._on_favorite)
        actions.append(self.fav_button)
        text.append(actions)

        row.append(text)
        return row

    def _play_shuffled(self) -> None:
        import random

        order = list(self.tracks)
        random.shuffle(order)
        self.ctx.music.play_queue(order, 0)

    def _track_list(self) -> Gtk.Widget:
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_start=16, margin_end=16)
        listbox.add_css_class("boxed-list")
        for index, track in enumerate(self.tracks):
            number = track.get("trackNo") or index + 1
            row = Adw.ActionRow(
                title=track.get("title") or "",
                subtitle=track.get("artist") or "",
                activatable=True,
            )
            num_label = Gtk.Label(label=str(number), width_chars=3, xalign=1)
            num_label.add_css_class("dim-label")
            row.add_prefix(num_label)

            duration = Gtk.Label(label=format_duration(track.get("durationSec") or 0), valign=Gtk.Align.CENTER)
            duration.add_css_class("gf-mini-time")
            row.add_suffix(duration)

            # Einzelnen Titel anhängen. Vorher gab es das nur für ein ganzes
            # Album ("➕ Anhängen" oben) — für einen einzelnen Titel fehlte
            # jeder Weg in die Warteschlange.
            enqueue = Gtk.Button(
                icon_name="list-add-symbolic",
                has_frame=False,
                valign=Gtk.Align.CENTER,
                tooltip_text="An die Warteschlange anhängen",
            )
            enqueue.connect("clicked", lambda _b, t=track: self._enqueue_track(t))
            row.add_suffix(enqueue)

            fav = Gtk.ToggleButton(
                icon_name="emblem-favorite-symbolic",
                has_frame=False,
                valign=Gtk.Align.CENTER,
                active=bool(track.get("favorite")),
                tooltip_text="Titel als Favorit",
            )
            fav.connect("toggled", lambda b, t=track: self._on_track_favorite(b, t))
            row.add_suffix(fav)

            # Ab dem angeklickten Titel spielen, der Rest des Albums folgt.
            row.connect("activated", lambda _r, i=index: self.ctx.music.play_queue(self.tracks, i))
            listbox.append(row)
        return listbox

    def _enqueue_track(self, track: dict) -> None:
        """Titel hinten anhängen. Läuft gerade nichts, beginnt er sofort —
        so verhält sich `MusicPlayer.append` auch für ganze Alben."""
        self.ctx.music.append([track])
        _toast(self, f"„{track.get('title') or 'Titel'}" + "\u201c an die Warteschlange angehängt.")

    def _download_album(self) -> None:
        """Lädt die Titel NACHEINANDER herunter.

        `DownloadManager.start_download` startet je Titel einen eigenen Thread.
        Alle auf einmal anzustoßen hieße bei diesem Album 19 gleichzeitige
        Übertragungen — deshalb hängt der jeweils nächste am Abschluss des
        vorherigen."""
        pending = [t for t in self.tracks if not self.ctx.downloads.is_downloaded(int(t["id"]))]
        if not pending:
            _toast(self, "Alle Titel liegen schon offline vor.")
            return
        self.offline_button.set_sensitive(False)
        self._queue_next(pending, 0, len(pending))

    def _queue_next(self, pending: list[dict], index: int, total: int) -> None:
        if index >= len(pending):
            self.offline_button.set_sensitive(True)
            self.offline_button.set_label("⬇ Offline")
            _toast(self, f"{total} Titel liegen jetzt offline vor.")
            return
        self.offline_button.set_label(f"⬇ {index + 1}/{total}")
        track = pending[index]
        self.ctx.downloads.start_download(
            track,
            on_progress=lambda *_: False,
            on_done=lambda *_: (self._queue_next(pending, index + 1, total), False)[1],
            on_error=lambda _i, msg: (self._download_failed(msg, pending, index, total), False)[1],
        )

    def _download_failed(self, message: str, pending: list[dict], index: int, total: int) -> None:
        # Ein einzelner Fehlschlag hält das Album nicht auf — melden und weiter.
        _toast(self, f"Ein Titel ließ sich nicht laden: {message}")
        self._queue_next(pending, index + 1, total)

    def _on_favorite(self, button: Gtk.ToggleButton) -> None:
        state = button.get_active()
        album_id = int(self.album["id"])

        def worker() -> None:
            try:
                self.ctx.client.set_album_favorite(album_id, state)
            except GoldfishAPIError as exc:
                GLib.idle_add(_toast, self, f"Favorit nicht gespeichert: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_track_favorite(self, button: Gtk.ToggleButton, track: dict) -> None:
        state = button.get_active()
        track["favorite"] = state
        item_id = int(track["id"])

        def worker() -> None:
            try:
                self.ctx.client.set_favorite(item_id, state)
            except GoldfishAPIError as exc:
                GLib.idle_add(_toast, self, f"Favorit nicht gespeichert: {exc}")

        threading.Thread(target=worker, daemon=True).start()


def _busy(toolbar_view: Adw.ToolbarView) -> None:
    spinner = Gtk.Spinner(width_request=48, height_request=48)
    wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    wrap.append(spinner)
    toolbar_view.set_content(wrap)
    spinner.start()


def _error(toolbar_view: Adw.ToolbarView, message: str, title: str = "Fehler", icon: str = "dialog-error-symbolic") -> bool:
    toolbar_view.set_content(Adw.StatusPage(icon_name=icon, title=title, description=message))
    return False


def _toast(page: Adw.NavigationPage, message: str) -> bool:
    root = page.get_root()
    if hasattr(root, "show_toast"):
        root.show_toast(message)
    return False
