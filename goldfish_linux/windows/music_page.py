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
from ..formatting import format_date, format_duration  # noqa: E402
from ..widgets.grid import AlbumGrid  # noqa: E402
from ..widgets.poster import load_poster_async  # noqa: E402

# Server-Wunsch 2026-09-14 (siehe Server-CLAUDE.md "Listenspalten Zuletzt
# abgespielt/Wiedergaben/Hinzugefügt + Spalten-Auswahl") — drei optionale
# Spalten für Alben- und Titel-Listen, ein-/ausblendbar über ein
# "Spalten"-Menü, analog zum Browser-Dropdown und dem Mac-"☰ Spalten"-Menü.
_MUSIC_COLUMN_LABELS = {
    "lastPlayed": "Zuletzt gehört",
    "playCount": "Wiedergaben",
    "added": "Hinzugefügt",
}


def _build_columns_popover(ctx, context: str, on_change) -> Gtk.Popover:
    """Kontrollkästchen pro optionaler Spalte — `context` unterscheidet
    Albenliste/"Alle Titel"/Album-Detail (jede hat ihre eigene gemerkte
    Sichtbarkeit, siehe `ViewPrefs.music_columns_visible`)."""
    visible = ctx.view_prefs.music_columns_visible(context)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
    for key, label in _MUSIC_COLUMN_LABELS.items():
        check = Gtk.CheckButton(label=label, active=key in visible)

        def _toggled(btn: Gtk.CheckButton, k=key) -> None:
            current = ctx.view_prefs.music_columns_visible(context)
            if btn.get_active():
                current.add(k)
            else:
                current.discard(k)
            ctx.view_prefs.set_music_columns_visible(context, current)
            on_change()

        check.connect("toggled", _toggled)
        box.append(check)
    return Gtk.Popover(child=box)


def _build_columns_menu_button(ctx, context: str, on_change) -> Gtk.MenuButton:
    popover = _build_columns_popover(ctx, context, on_change)
    return Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text="Spalten", popover=popover)


def _column_suffixes(ctx, context: str, entry: dict) -> list[Gtk.Widget]:
    """Suffix-Labels für die drei optionalen Spalten, nur für die aktuell
    sichtbaren (siehe `ViewPrefs.music_columns_visible`)."""
    visible = ctx.view_prefs.music_columns_visible(context)
    widgets: list[Gtk.Widget] = []
    if "lastPlayed" in visible:
        label = Gtk.Label(label=format_date(entry.get("lastPlayedAt")) or "—", valign=Gtk.Align.CENTER)
        label.add_css_class("dim-label")
        widgets.append(label)
    if "playCount" in visible:
        label = Gtk.Label(label=str(entry.get("playCount") or 0), valign=Gtk.Align.CENTER)
        label.add_css_class("dim-label")
        widgets.append(label)
    if "added" in visible:
        label = Gtk.Label(label=format_date(entry.get("addedAt")) or "—", valign=Gtk.Align.CENTER)
        label.add_css_class("dim-label")
        widgets.append(label)
    return widgets


class MusicLibraryPage(Adw.NavigationPage):
    """Albenübersicht mit Suche und Genre-Filter."""

    # Grid → Liste → Alle Titel → Grid, ein Knopf statt drei — analog zur
    # Mac/iOS-App (Build 211) und der Android-App. Icons bewusst nur
    # "view-grid-symbolic"/"view-list-symbolic" (beide Teil der
    # Freedesktop-Icon-Namenskonvention, auf jedem Zielsystem vorhanden) —
    # die Kurzhilfe unterscheidet Alben-Liste von Alle-Titel.
    _MODE_CYCLE = {"grid": "list", "list": "all", "all": "grid"}
    _MODE_ICON = {"grid": "view-grid-symbolic", "list": "view-list-symbolic", "all": "view-list-symbolic"}
    _MODE_TOOLTIP = {
        "grid": "Alben als Kacheln",
        "list": "Alben als Liste",
        "all": "Alle Titel",
    }

    def __init__(self, ctx, nav_view: Adw.NavigationView, library: dict):
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        search = Gtk.SearchEntry(placeholder_text="Titel, Künstler oder Album…")
        header.set_title_widget(search)
        toolbar_view.add_top_bar(header)

        super().__init__(title=library["name"], tag=f"music-{library['id']}", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.library = library
        self.toolbar_view = toolbar_view
        self.albums: list[dict] = []
        self.search = search
        # Titelsuche läuft über den Server (er durchsucht Titel, Künstler und
        # Album) und ist deshalb entprellt — sonst eine Abfrage pro Tastendruck.
        self.tracks: list[dict] = []
        self._search_seq = 0
        self._search_timeout = 0
        search.connect("search-changed", lambda *_: self._on_search_changed())
        # Ein Raster mit Recycling, kein fließendes: eine Musikbibliothek hat
        # hier 2717 Alben, und als einzeln gebaute Kacheln kostet das 4,65
        # Sekunden blockierten Hauptablauf (nachgemessen) — in dieser Zeit
        # stand auch die laufende Wiedergabe scheinbar still.
        self.grid: AlbumGrid | None = None
        self.all_tracks: list[dict] | None = None
        self.mode = self.ctx.view_prefs.music_view_mode(int(library["id"]))

        self.mode_button = Gtk.Button(
            icon_name=self._MODE_ICON[self.mode], tooltip_text=self._MODE_TOOLTIP[self.mode]
        )
        self.mode_button.connect("clicked", lambda *_: self._cycle_mode())
        header.pack_end(self.mode_button)

        # "Spalten"-Menü nur sinnvoll, wo tatsächlich Zeilen (keine Kacheln)
        # stehen — Kachelmodus bekommt keinen Knopf, analog zum Verzicht auf
        # Spaltenbreiten in `AlbumGrid`.
        self.columns_button = _build_columns_menu_button(
            self.ctx, self._columns_context(), self._render
        )
        self.columns_button.set_visible(self._columns_context() is not None)
        header.pack_end(self.columns_button)

        # Kein Freedesktop-Standardsymbol für "Playlist" — Emoji statt Icon,
        # dieselbe Konvention wie überall sonst in dieser App (Kachel-Ecken,
        # Seitenleiste), damit nichts von der Icon-Theme-Verfügbarkeit abhängt.
        playlists = Gtk.Button(label="📋", tooltip_text="Musik-Playlists")
        playlists.connect("clicked", lambda *_: self._open_playlists())
        header.pack_end(playlists)

        folders = Gtk.Button(icon_name="folder-symbolic", tooltip_text="Ordner durchsehen")
        folders.connect("clicked", lambda *_: self._open_folders())
        header.pack_end(folders)

        _busy(toolbar_view)
        threading.Thread(target=self._load, daemon=True).start()

    def _columns_context(self) -> str | None:
        """Welcher Spalten-Sichtbarkeits-Kontext gerade gilt — `None` im
        Kachelmodus, wo es keine Zeilen mit Suffix-Platz gibt."""
        if self.mode == "list":
            return "albums"
        if self.mode == "all":
            return "allTracks"
        return None

    def _refresh_columns_button(self) -> None:
        context = self._columns_context()
        self.columns_button.set_visible(context is not None)
        if context is not None:
            self.columns_button.set_popover(_build_columns_popover(self.ctx, context, self._render))

    def _cycle_mode(self) -> None:
        self.mode = self._MODE_CYCLE[self.mode]
        self.ctx.view_prefs.set_music_view_mode(int(self.library["id"]), self.mode)
        self.mode_button.set_icon_name(self._MODE_ICON[self.mode])
        self.mode_button.set_tooltip_text(self._MODE_TOOLTIP[self.mode])
        self._refresh_columns_button()
        if self.mode == "all" and self.all_tracks is None:
            self._load_all_tracks()
            return
        self._render()

    def _load_all_tracks(self) -> None:
        _busy(self.toolbar_view)

        def worker() -> None:
            try:
                tracks = self.ctx.client.items(self.library["id"], sort="title")
            except GoldfishAPIError as exc:
                GLib.idle_add(_error, self.toolbar_view, str(exc))
                return
            GLib.idle_add(self._apply_all_tracks, tracks)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_all_tracks(self, tracks: list[dict]) -> bool:
        self.all_tracks = tracks
        self._render()
        return False

    def _open_playlists(self) -> None:
        from .playlists_page import PlaylistsPage

        self.nav_view.push(PlaylistsPage(self.ctx, self.nav_view, kind="music"))

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

    def _on_search_changed(self) -> None:
        if self._search_timeout:
            GLib.source_remove(self._search_timeout)
        self._search_timeout = GLib.timeout_add(300, self._run_search)

    def _run_search(self) -> bool:
        self._search_timeout = 0
        needle = self.search.get_text().strip()
        self._search_seq += 1
        seq = self._search_seq
        if not needle:
            self.tracks = []
            self._render()
            return False

        def worker() -> None:
            try:
                # Der Server durchsucht für Musik auch Künstler und Album
                # (ListItems-Suchklausel) — hier ist nichts nachzufiltern.
                tracks = self.ctx.client.items(self.library["id"], search=needle, sort="title")
            except GoldfishAPIError:
                tracks = []
            if seq != self._search_seq:
                return  # eine neuere Suche ist unterwegs
            GLib.idle_add(self._apply_tracks, tracks, seq)

        threading.Thread(target=worker, daemon=True).start()
        # Die Alben passen sofort (die Liste liegt vor), die Titel kommen nach.
        self._render()
        return False

    def _apply_tracks(self, tracks: list[dict], seq: int) -> bool:
        if seq != self._search_seq:
            return False
        self.tracks = tracks
        self._render()
        return False

    def _render(self) -> None:
        needle = self.search.get_text().strip().lower()
        shown = [
            a
            for a in self.albums
            if not needle or needle in (a.get("album") or "").lower() or needle in (a.get("artist") or "").lower()
        ]
        if needle:
            # Bei einer Suche zählen BEIDE Ebenen: passende Alben und passende
            # Titel. Vorher wurde nur in der schon geladenen Albenliste
            # gesucht — nach einem Titel zu suchen fand deshalb nie etwas.
            self._render_search(shown, self.tracks)
            return
        if not shown:
            self.grid = None
            _error(
                self.toolbar_view,
                "Diese Bibliothek enthält keine Alben.",
                title="Leer",
                icon="system-search-symbolic",
            )
            return

        if self.mode == "all":
            self._render_all_tracks()
            return
        if self.mode == "list":
            self._render_album_list(shown)
            return

        if self.grid is None:
            self.grid = AlbumGrid(self.ctx.client, on_album=self._open_album)
        self.grid.set_albums(shown)
        if self.toolbar_view.get_content() is not self.grid:
            self.toolbar_view.set_content(self.grid)

    def _render_album_list(self, albums: list[dict]) -> None:
        """Alben als Liste statt Kacheln (Build-211-Parität) — bewusst eine
        `Gtk.ListBox`, keine eigene Recycling-Liste: die Albenzahl ist zwar
        groß, aber `boxed-list` skaliert dafür gut genug und spart eine
        zweite Widget-Klasse für denselben Anwendungsfall wie `_render_search`."""
        self.grid = None
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_start=16, margin_end=16)
        listbox.add_css_class("boxed-list")
        for album in albums:
            listbox.append(self._build_album_row(album))
        self.toolbar_view.set_content(Gtk.ScrolledWindow(vexpand=True, margin_top=12, margin_bottom=24, child=listbox))

    def _render_all_tracks(self) -> None:
        """Alle Titel der Bibliothek flach, unabhängig von der Album-
        Gruppierung — Pendant zum Browser/Android „Alle Titel". `None` heißt
        "noch nie geladen" (z. B. Seite frisch geöffnet, Modus war aber schon
        vorher auf "all" gemerkt) — dann wird nachgeladen statt fälschlich
        "leer" zu melden; `[]` heißt "geladen, wirklich keine Titel"."""
        self.grid = None
        if self.all_tracks is None:
            self._load_all_tracks()
            return
        tracks = self.all_tracks
        if not tracks:
            _error(
                self.toolbar_view,
                "Diese Bibliothek enthält keine Titel.",
                title="Leer",
                icon="system-search-symbolic",
            )
            return
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_start=16, margin_end=16)
        listbox.add_css_class("boxed-list")
        for index, track in enumerate(tracks):
            listbox.append(self._build_track_row(track, tracks, index, show_album=True))
        self.toolbar_view.set_content(Gtk.ScrolledWindow(vexpand=True, margin_top=12, margin_bottom=24, child=listbox))

    def _build_album_row(self, album: dict) -> Gtk.Widget:
        row = Adw.ActionRow(
            title=album.get("album") or "",
            subtitle=" · ".join(str(p) for p in (album.get("artist"), album.get("year") or "") if p),
            activatable=True,
        )
        for widget in _column_suffixes(self.ctx, "albums", album):
            row.add_suffix(widget)
        count = album.get("trackCount") or 0
        if count:
            label = Gtk.Label(label=f"{count} Titel", valign=Gtk.Align.CENTER)
            label.add_css_class("dim-label")
            row.add_suffix(label)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda _r, a=album: self._open_album(a))
        return row

    def _build_track_row(self, track: dict, queue: list[dict], index: int, *, show_album: bool) -> Gtk.Widget:
        subtitle_parts = [track.get("artist")]
        if show_album:
            subtitle_parts.append(track.get("album"))
        subtitle = " · ".join(str(p) for p in subtitle_parts if p)
        row = Adw.ActionRow(title=track.get("title") or "", subtitle=subtitle, activatable=True)
        for widget in _column_suffixes(self.ctx, "allTracks", track):
            row.add_suffix(widget)
        duration = Gtk.Label(label=format_duration(track.get("durationSec") or 0), valign=Gtk.Align.CENTER)
        duration.add_css_class("gf-mini-time")
        row.add_suffix(duration)
        enqueue = Gtk.Button(
            icon_name="list-add-symbolic",
            has_frame=False,
            valign=Gtk.Align.CENTER,
            tooltip_text="An die Warteschlange anhängen",
        )
        enqueue.connect("clicked", lambda _b, t=track: self._enqueue_found(t))
        row.add_suffix(enqueue)
        # Ab dem angeklickten Titel spielen, die angezeigte Liste ist die
        # Warteschlange (egal ob Suchtreffer oder "Alle Titel").
        row.connect("activated", lambda _r, i=index, q=queue: self.ctx.music.play_queue(q, i))
        return row

    def _render_search(self, albums: list[dict], tracks: list[dict]) -> None:
        """Trefferliste: Alben und Titel untereinander in EINER Liste.

        Bewusst eine Liste und kein Raster mit eingebetteter Liste — zwei
        scrollende Bereiche ineinander sind in GTK unangenehm zu bedienen, und
        eine Trefferliste ist ohnehin kurz."""
        self.grid = None
        if not albums and not tracks:
            _error(
                self.toolbar_view,
                "Kein Album und kein Titel passt zur Suche.",
                title="Keine Treffer",
                icon="system-search-symbolic",
            )
            return
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=24)
        if albums:
            box.append(_heading(f"Alben · {len(albums)}"))
            listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_start=16, margin_end=16)
            listbox.add_css_class("boxed-list")
            for album in albums:
                listbox.append(self._build_album_row(album))
            box.append(listbox)
        if tracks:
            box.append(_heading(f"Titel · {len(tracks)}"))
            listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_start=16, margin_end=16)
            listbox.add_css_class("boxed-list")
            for index, track in enumerate(tracks):
                listbox.append(self._build_track_row(track, tracks, index, show_album=True))
            box.append(listbox)
        self.toolbar_view.set_content(Gtk.ScrolledWindow(vexpand=True, child=box))

    def _enqueue_found(self, track: dict) -> None:
        self.ctx.music.append([track])
        _toast(self, "An die Warteschlange angehängt.")

    def _open_album(self, album: dict) -> None:
        self.nav_view.push(AlbumPage(self.ctx, self.nav_view, album))

    def _open_folders(self) -> None:
        from .browse_page import BrowsePage

        self.nav_view.push(BrowsePage(self.ctx, self.nav_view, self.library))


class AlbumPage(Adw.NavigationPage):
    """Titelliste eines Albums, mit Cover und den Aktionen dazu."""

    def __init__(self, ctx, nav_view: Adw.NavigationView, album: dict):
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.columns_button = _build_columns_menu_button(ctx, "albumTracks", self._rerender)
        header.pack_end(self.columns_button)
        toolbar_view.add_top_bar(header)

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
        self._rerender()
        return False

    def _rerender(self) -> None:
        """Baut den Inhalt neu — auch für das "Spalten"-Menü, das keine
        eigenen Daten nachlädt, nur die schon vorhandenen Titel neu zeigt."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=16, margin_bottom=24)
        box.append(self._header())
        box.append(self._track_list())
        self.toolbar_view.set_content(Gtk.ScrolledWindow(vexpand=True, child=box))

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
        play = Gtk.Button()
        play.set_child(_icon_label("media-playback-start-symbolic", "Album abspielen"))
        play.add_css_class("suggested-action")
        play.add_css_class("pill")
        play.connect("clicked", lambda *_: self.ctx.music.play_queue(self.tracks, 0))
        actions.append(play)

        shuffle = Gtk.Button(
            icon_name="media-playlist-shuffle-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text="Album in zufälliger Reihenfolge",
        )
        shuffle.add_css_class("circular")
        shuffle.connect("clicked", lambda *_: self._play_shuffled())
        actions.append(shuffle)

        queue = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        queue.add_css_class("circular")
        queue.set_tooltip_text("An die laufende Warteschlange anhängen")
        queue.connect("clicked", lambda *_: self.ctx.music.append(self.tracks))
        actions.append(queue)

        self.offline_button = Gtk.Button(
            icon_name="folder-download-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text="Alle Titel offline mitnehmen",
        )
        self.offline_button.add_css_class("circular")
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

            for widget in _column_suffixes(self.ctx, "albumTracks", track):
                row.add_suffix(widget)

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
            self.offline_button.set_tooltip_text("Alle Titel offline mitnehmen")
            _toast(self, f"{total} Titel liegen jetzt offline vor.")
            return
        # Fortschritt in die Kurzhilfe, nicht in die Beschriftung: der Knopf
        # trägt jetzt ein Symbol.
        self.offline_button.set_tooltip_text(f"Lädt Titel {index + 1} von {total} …")
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


def _icon_label(icon: str, text: str) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.CENTER)
    box.append(Gtk.Image.new_from_icon_name(icon))
    box.append(Gtk.Label(label=text))
    return box


def _heading(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0, margin_start=16)
    label.add_css_class("heading")
    return label


def _toast(page: Adw.NavigationPage, message: str) -> bool:
    root = page.get_root()
    if hasattr(root, "show_toast"):
        root.show_toast(message)
    return False
