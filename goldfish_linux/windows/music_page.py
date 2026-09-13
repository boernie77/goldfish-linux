"""Musikbibliothek: Albenübersicht und Titelliste eines Albums.

Die Alben sind kein Abbild der Ordnerstruktur — der Server fasst sie über
Tags und den gemeinsamen Ordner zu einer Einheit zusammen
(`GroupMusicAlbums`). Deshalb eine eigene Ansicht und nicht der
Ordner-Browser: eine Musikbibliothek hat hier 2717 Alben aus mehr als 7000
Titeln, und über Ordner käme man nur mühsam dorthin.

Wer die Ordner sehen will, kommt weiterhin über den Bibliotheks-Browser
dorthin — das ist dieselbe zweigleisige Auslegung wie im Browser.

**Aufbau der Leiste (Wunsch vom 2026-09-13, „so wie auf dem Mac"):** drei
beschriftete Schalter für die drei Ansichten — Alben als Kacheln, Alben als
Liste, alle Titel — und rechts davon eigene Knöpfe für Spalten und Filter.
Vorher schaltete EIN Knopf reihum durch die drei Ansichten; man sah ihm nicht
an, wo man gerade ist und was als Nächstes käme.

Die beiden Listen sind echte Tabellen (`widgets/column_list.py`): Spalten
lassen sich in der Breite ziehen, mit dem Kopf umsortieren, über das
Spalten-Menü zu- und abwählen, und ein Klick auf den Kopf sortiert. Breite,
Reihenfolge und Auswahl bleiben je Ansicht gemerkt.
"""

from __future__ import annotations

import random
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from ..api import GoldfishAPIError  # noqa: E402
from ..formatting import format_date, format_duration  # noqa: E402
from ..widgets.card import _image_frame, ensure_card_css  # noqa: E402
from ..widgets.column_list import ColumnList, ColumnSpec  # noqa: E402
from ..widgets.grid import AlbumGrid  # noqa: E402
from ..widgets.poster import load_poster_async  # noqa: E402

# "—" statt leer: eine leere Zelle sieht in einer Tabelle wie ein Fehler aus.
_EMPTY = "—"


def _text(value) -> str:
    if value in (None, "", 0):
        return _EMPTY
    return str(value)


def _sort_text(value) -> str:
    return str(value or "").casefold()


def _sort_number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _album_columns(actions) -> list[ColumnSpec]:
    """Spalten der Albenliste. `default=False` heißt: erst nach dem Anhaken im
    Spalten-Menü sichtbar — sonst wäre die Tabelle beim ersten Öffnen voll mit
    Feldern, die die meisten nie brauchen."""
    return [
        ColumnSpec("album", "Album", width=260, expand=True, text=lambda a: _text(a.get("album")), sort_key=lambda a: _sort_text(a.get("album"))),
        ColumnSpec("artist", "Künstler", width=200, expand=True, text=lambda a: _text(a.get("artist")), sort_key=lambda a: _sort_text(a.get("artist"))),
        ColumnSpec("genre", "Genre", width=140, text=lambda a: _text(a.get("genre")), sort_key=lambda a: _sort_text(a.get("genre"))),
        ColumnSpec("year", "Jahr", width=70, numeric=True, text=lambda a: _text(a.get("year")), sort_key=lambda a: _sort_number(a.get("year"))),
        ColumnSpec("trackCount", "Titel", width=70, numeric=True, text=lambda a: _text(a.get("trackCount")), sort_key=lambda a: _sort_number(a.get("trackCount"))),
        ColumnSpec("lastPlayed", "Zuletzt gehört", width=130, default=False, text=lambda a: format_date(a.get("lastPlayedAt")) or _EMPTY, sort_key=lambda a: str(a.get("lastPlayedAt") or "")),
        ColumnSpec("playCount", "Wiedergaben", width=110, numeric=True, default=False, text=lambda a: str(a.get("playCount") or 0), sort_key=lambda a: _sort_number(a.get("playCount"))),
        ColumnSpec("added", "Hinzugefügt", width=130, default=False, text=lambda a: format_date(a.get("addedAt")) or _EMPTY, sort_key=lambda a: str(a.get("addedAt") or "")),
        ColumnSpec("actions", "", width=96, fixed=True, build=actions),
    ]


def _track_columns(actions, *, show_track_no: bool, show_album: bool) -> list[ColumnSpec]:
    specs = []
    if show_track_no:
        specs.append(
            ColumnSpec("trackNo", "Nr.", width=56, numeric=True, text=lambda t: _text(t.get("trackNo")), sort_key=lambda t: _sort_number(t.get("trackNo")))
        )
    specs.append(
        ColumnSpec("title", "Titel", width=280, expand=True, text=lambda t: _text(t.get("title")), sort_key=lambda t: _sort_text(t.get("title")))
    )
    specs.append(
        ColumnSpec("artist", "Künstler", width=190, expand=True, text=lambda t: _text(t.get("artist")), sort_key=lambda t: _sort_text(t.get("artist")))
    )
    specs.append(
        ColumnSpec("album", "Album", width=190, expand=True, default=show_album, text=lambda t: _text(t.get("album")), sort_key=lambda t: _sort_text(t.get("album")))
    )
    specs.extend(
        [
            ColumnSpec("genre", "Genre", width=130, default=False, text=lambda t: _text(t.get("genre")), sort_key=lambda t: _sort_text(t.get("genre"))),
            ColumnSpec("year", "Jahr", width=70, numeric=True, default=False, text=lambda t: _text(t.get("year")), sort_key=lambda t: _sort_number(t.get("year"))),
            ColumnSpec("duration", "Dauer", width=80, numeric=True, text=lambda t: format_duration(t.get("durationSec") or 0), sort_key=lambda t: _sort_number(t.get("durationSec"))),
            ColumnSpec("lastPlayed", "Zuletzt gehört", width=130, default=False, text=lambda t: format_date(t.get("lastPlayedAt")) or _EMPTY, sort_key=lambda t: str(t.get("lastPlayedAt") or "")),
            ColumnSpec("playCount", "Wiedergaben", width=110, numeric=True, default=False, text=lambda t: str(t.get("playCount") or 0), sort_key=lambda t: _sort_number(t.get("playCount"))),
            ColumnSpec("added", "Hinzugefügt", width=130, default=False, text=lambda t: format_date(t.get("addedAt")) or _EMPTY, sort_key=lambda t: str(t.get("addedAt") or "")),
            ColumnSpec("actions", "", width=118, fixed=True, build=actions),
        ]
    )
    return specs


def first_available_icon(*names: str) -> str:
    """Erstes Symbol, das das Icon-Thema des Systems wirklich kennt.

    Die Zielsysteme bringen verschiedene Themen mit (Adwaita, Yaru, Papirus,
    Mint-X) und teilen sich nur einen Teil der Namen — ein fehlendes Symbol
    erscheint als leere Fläche, nicht als Fehler. Deshalb eine Kette mit einem
    sicheren letzten Glied."""
    display = Gdk.Display.get_default()
    if display is not None:
        theme = Gtk.IconTheme.get_for_display(display)
        for name in names:
            if theme.has_icon(name):
                return name
    return names[-1]


# "playlist-symbolic" gibt es in Yaru/Papirus/Ubuntu-Mono, "view-list-ordered"
# in Adwaita — eines davon hat jedes hier vorkommende Thema.
PLAYLIST_ICON = ("playlist-symbolic", "view-list-ordered-symbolic", "view-list-symbolic")


def _is_audiobook(track: dict) -> bool:
    """Hörbücher gehören nicht in die Zufallswiedergabe (Vorgabe vom
    2026-09-05, seither auch auf dem Server so): ein Kapitel zwischen
    Musikstücken ist nie gewollt. Dieselben drei Merkmale wie der Server —
    Dateiformat .m4b, "Hörbuch"/"Audiobook" im Pfad oder Titel, oder ein
    entsprechendes Genre-Tag."""
    if (track.get("container") or "").lower() == "m4b":
        return True
    haystack = " ".join(
        str(track.get(key) or "") for key in ("relPath", "title", "genre", "album", "artist")
    ).lower()
    haystack = haystack.replace("ö", "o").replace("ü", "u")
    return "horbuch" in haystack or "audiobook" in haystack


def _icon_button(icon: str, tooltip: str, on_click) -> Gtk.Button:
    button = Gtk.Button(icon_name=icon, has_frame=False, valign=Gtk.Align.CENTER, tooltip_text=tooltip)
    button.connect("clicked", lambda *_: on_click())
    return button


def _columns_popover(column_list: ColumnList) -> Gtk.Popover:
    """Kontrollkästchen je abwählbarer Spalte der gerade gezeigten Tabelle."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=10, margin_bottom=10, margin_start=12, margin_end=12)
    title = Gtk.Label(label="Spalten", xalign=0)
    title.add_css_class("heading")
    box.append(title)
    for key in column_list.spec_order:
        spec = column_list.specs[key]
        if spec.fixed:
            continue
        check = Gtk.CheckButton(label=spec.title, active=key in column_list.visible)
        check.connect("toggled", lambda btn, k=key: column_list.set_column_visible(k, btn.get_active()))
        box.append(check)
    hint = Gtk.Label(label="Breite am Spaltenrand ziehen,\nReihenfolge am Spaltenkopf.", xalign=0)
    hint.add_css_class("dim-label")
    hint.add_css_class("caption")
    box.append(Gtk.Separator())
    box.append(hint)
    return Gtk.Popover(child=box)


class MusicLibraryPage(Adw.NavigationPage):
    """Albenübersicht mit Suche, Filter und drei Ansichten."""

    # Drei Schalter statt eines reihum schaltenden Knopfes (User-Wunsch
    # 2026-09-13): ein Umschalter zeigt, wo man ist; ein Reihum-Knopf zeigte
    # nur, was als Nächstes käme. Symbole statt Beschriftungen — sie stehen
    # neben dem Zufallsknopf und sollen sich in dieselbe Reihe fügen
    # (Nachtrag desselben Tages). Was sie bedeuten, sagt die Kurzhilfe.
    _MODES = (
        ("grid", ("view-grid-symbolic",), "Alben als Kacheln"),
        ("list", ("view-list-symbolic",), "Alben als Liste mit Spalten"),
        # "music-note" gibt es nur in Yaru, "audio-x-generic" überall.
        ("all", ("music-note-symbolic", "audio-x-generic-symbolic"), "Alle Titel der Bibliothek als Liste"),
    )

    def __init__(self, ctx, nav_view: Adw.NavigationView, library: dict):
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        search = Gtk.SearchEntry(placeholder_text="Titel, Künstler oder Album…")
        header.set_title_widget(search)
        toolbar_view.add_top_bar(header)

        super().__init__(title=library["name"], tag=f"music-{library['id']}", child=toolbar_view)
        ensure_card_css()  # für die Cover-Rundung (.gf-card-image) in den Suchtreffern
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
        self.column_list: ColumnList | None = None
        # Ob in der Tabelle gerade Titel oder Alben stehen — der Zufallsknopf
        # mischt Titel, keine Alben.
        self.list_shows_tracks = False
        self.all_tracks: list[dict] | None = None
        self.mode = self.ctx.view_prefs.music_view_mode(int(library["id"]))

        saved_filter = self.ctx.view_prefs.music_filter(int(library["id"]))
        self.favorites_only = bool(saved_filter.get("favorites"))
        self.genres: set[str] = set(saved_filter.get("genres") or [])

        playlists = Gtk.Button(
            icon_name=first_available_icon(*PLAYLIST_ICON), tooltip_text="Musik-Playlists"
        )
        playlists.connect("clicked", lambda *_: self._open_playlists())
        header.pack_end(playlists)

        folders = Gtk.Button(icon_name="folder-symbolic", tooltip_text="Ordner durchsehen")
        folders.connect("clicked", lambda *_: self._open_folders())
        header.pack_end(folders)

        toolbar_view.add_top_bar(self._build_toolbar())

        _busy(toolbar_view)
        threading.Thread(target=self._load, daemon=True).start()

    # -- Leiste ----------------------------------------------------------

    def _build_toolbar(self) -> Gtk.Widget:
        bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_top=6,
            margin_bottom=6,
            margin_start=12,
            margin_end=12,
        )
        bar.add_css_class("toolbar")

        modes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        modes.add_css_class("linked")
        self.mode_buttons: dict[str, Gtk.ToggleButton] = {}
        group: Gtk.ToggleButton | None = None
        for key, icons, tooltip in self._MODES:
            button = Gtk.ToggleButton(icon_name=first_available_icon(*icons), tooltip_text=tooltip)
            if group is None:
                group = button
            else:
                button.set_group(group)
            button.set_active(key == self.mode)
            button.connect("toggled", self._on_mode_toggled, key)
            modes.append(button)
            self.mode_buttons[key] = button
        bar.append(modes)

        # Zufallswiedergabe gehört in JEDE Ansicht (Wunsch 2026-09-13) —
        # deshalb in der Leiste und nicht in einer einzelnen Ansicht.
        shuffle = Gtk.Button(
            icon_name="media-playlist-shuffle-symbolic",
            tooltip_text="Zufällig abspielen",
        )
        shuffle.connect("clicked", lambda *_: self._play_shuffle())
        bar.append(shuffle)

        bar.append(Gtk.Box(hexpand=True))

        self.count_label = Gtk.Label(valign=Gtk.Align.CENTER)
        self.count_label.add_css_class("dim-label")
        bar.append(self.count_label)

        # Zwei eigene Knöpfe statt eines Sammelmenüs: beides wird oft
        # gebraucht und meint völlig Verschiedenes.
        self.columns_button = Gtk.MenuButton(label="Spalten", tooltip_text="Welche Spalten die Liste zeigt")
        bar.append(self.columns_button)

        self.filter_button = Gtk.MenuButton(label="Filter", tooltip_text="Favoriten und Genres")
        self.filter_popover = Gtk.Popover()
        self.filter_button.set_popover(self.filter_popover)
        self.filter_popover.connect("show", lambda *_: self.filter_popover.set_child(self._build_filter_box()))
        bar.append(self.filter_button)
        self._update_filter_label()
        return bar

    def _update_columns_button(self) -> None:
        """Das Spalten-Menü gehört zur gerade gezeigten Tabelle. Im
        Kachelmodus gibt es keine, dann ist der Knopf abgeblendet."""
        if self.column_list is None:
            self.columns_button.set_sensitive(False)
            self.columns_button.set_popover(None)
            return
        self.columns_button.set_sensitive(True)
        self.columns_button.set_popover(_columns_popover(self.column_list))

    def _update_filter_label(self) -> None:
        active = (1 if self.favorites_only else 0) + len(self.genres)
        self.filter_button.set_label("Filter" if not active else f"Filter ({active})")

    def _build_filter_box(self) -> Gtk.Widget:
        """Favoriten und Genre — beides wird hier im Client gefiltert, die
        Werte stehen in den bereits geladenen Alben bzw. Titeln. Eine eigene
        Server-Abfrage (wie bei den Video-Bibliotheken) wäre dafür unnötig."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        box.set_size_request(240, -1)

        fav_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        fav_row.append(Gtk.Label(label="Nur Favoriten", xalign=0, hexpand=True))
        switch = Gtk.Switch(active=self.favorites_only, valign=Gtk.Align.CENTER)
        switch.connect("notify::active", self._on_favorites_toggled)
        fav_row.append(switch)
        box.append(fav_row)

        known = sorted({(a.get("genre") or "").strip() for a in self.albums if (a.get("genre") or "").strip()})
        if known:
            box.append(Gtk.Separator())
            heading = Gtk.Label(label="Genre", xalign=0)
            heading.add_css_class("heading")
            box.append(heading)
            genre_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            for genre in known:
                check = Gtk.CheckButton(label=genre, active=genre in self.genres)
                check.connect("toggled", self._on_genre_toggled, genre)
                genre_box.append(check)
            box.append(
                Gtk.ScrolledWindow(
                    child=genre_box,
                    propagate_natural_height=True,
                    min_content_height=150,
                    max_content_height=320,
                    hscrollbar_policy=Gtk.PolicyType.NEVER,
                )
            )

        box.append(Gtk.Separator())
        reset = Gtk.Button(label="Filter zurücksetzen")
        reset.connect("clicked", lambda *_: self._reset_filters())
        box.append(reset)
        return box

    def _on_favorites_toggled(self, switch: Gtk.Switch, *_args) -> None:
        self.favorites_only = switch.get_active()
        self._save_filter()

    def _on_genre_toggled(self, check: Gtk.CheckButton, genre: str) -> None:
        if check.get_active():
            self.genres.add(genre)
        else:
            self.genres.discard(genre)
        self._save_filter()

    def _reset_filters(self) -> None:
        self.favorites_only = False
        self.genres.clear()
        self.filter_popover.popdown()
        self._save_filter()

    def _save_filter(self) -> None:
        self.ctx.view_prefs.set_music_filter(
            int(self.library["id"]), favorites=self.favorites_only, genres=sorted(self.genres)
        )
        self._update_filter_label()
        self._render()

    # -- Ansicht wechseln ------------------------------------------------

    def _on_mode_toggled(self, button: Gtk.ToggleButton, key: str) -> None:
        # Ein Umschalter meldet auch den Knopf, der gerade AUSgeht.
        if not button.get_active() or key == self.mode:
            return
        self.mode = key
        self.ctx.view_prefs.set_music_view_mode(int(self.library["id"]), key)
        if key == "all" and self.all_tracks is None:
            self._load_all_tracks()
            return
        self._render()

    def _load_all_tracks(self) -> None:
        _busy(self.toolbar_view)
        self.column_list = None
        self._update_columns_button()

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
        if self.mode == "all" and self.all_tracks is None:
            self._load_all_tracks()
            return False
        self._render()
        return False

    # -- Suche -----------------------------------------------------------

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

    # -- Filtern und Anzeigen --------------------------------------------

    def _matching_albums(self) -> list[dict]:
        needle = self.search.get_text().strip().lower()
        out = []
        for album in self.albums:
            if self.favorites_only and not album.get("favorite"):
                continue
            if self.genres and (album.get("genre") or "").strip() not in self.genres:
                continue
            if needle and needle not in (album.get("album") or "").lower() and needle not in (album.get("artist") or "").lower():
                continue
            out.append(album)
        return out

    def _matching_tracks(self, tracks: list[dict]) -> list[dict]:
        out = []
        for track in tracks:
            if self.favorites_only and not track.get("favorite"):
                continue
            if self.genres and (track.get("genre") or "").strip() not in self.genres:
                continue
            out.append(track)
        return out

    def _render(self) -> None:
        if self.search.get_text().strip():
            self._render_search(self._matching_albums(), self._matching_tracks(self.tracks))
            return
        if self.mode == "all":
            self._render_all_tracks()
            return

        albums = self._matching_albums()
        if not albums:
            self.grid = None
            self.column_list = None
            self._update_columns_button()
            self.count_label.set_label("")
            _error(
                self.toolbar_view,
                "Kein Album passt zu Suche und Filter." if (self.genres or self.favorites_only) else "Diese Bibliothek enthält keine Alben.",
                title="Nichts zu zeigen",
                icon="system-search-symbolic",
            )
            return

        self.count_label.set_label(f"{len(albums)} Alben")
        if self.mode == "list":
            self._render_album_list(albums)
            return

        self.column_list = None
        self._update_columns_button()
        if self.grid is None:
            self.grid = AlbumGrid(self.ctx.client, on_album=self._open_album)
        self.grid.set_albums(albums)
        if self.toolbar_view.get_content() is not self.grid:
            self.toolbar_view.set_content(self.grid)

    def _render_album_list(self, albums: list[dict]) -> None:
        self.grid = None
        self.list_shows_tracks = False
        self.column_list = ColumnList(
            self.ctx.view_prefs,
            "albums",
            _album_columns(self._album_actions),
            albums,
            on_activate=lambda rows, index: self._open_album(rows[index]),
        )
        self._update_columns_button()
        self.toolbar_view.set_content(self.column_list)

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
        tracks = self._matching_tracks(self.all_tracks)
        if not tracks:
            self.column_list = None
            self._update_columns_button()
            self.count_label.set_label("")
            _error(
                self.toolbar_view,
                "Kein Titel passt zum Filter." if (self.genres or self.favorites_only) else "Diese Bibliothek enthält keine Titel.",
                title="Nichts zu zeigen",
                icon="system-search-symbolic",
            )
            return
        self.count_label.set_label(f"{len(tracks)} Titel")
        self.list_shows_tracks = True
        self.column_list = ColumnList(
            self.ctx.view_prefs,
            "allTracks",
            _track_columns(self._track_actions, show_track_no=False, show_album=True),
            tracks,
            on_activate=lambda rows, index: self.ctx.music.play_queue(rows, index),
        )
        self._update_columns_button()
        self.toolbar_view.set_content(self.column_list)

    def _render_search(self, albums: list[dict], tracks: list[dict]) -> None:
        """Trefferliste: passende Alben als Knöpfe oben, passende Titel als
        Tabelle darunter.

        Bewusst KEINE zweite Tabelle für die Alben — zwei scrollende Bereiche
        ineinander sind in GTK unangenehm zu bedienen. Die Albentreffer sind
        meist wenige und als Knopfreihe schneller zu erfassen."""
        self.grid = None
        self.count_label.set_label(f"{len(albums)} Alben · {len(tracks)} Titel")
        if not albums and not tracks:
            self.column_list = None
            self._update_columns_button()
            _error(
                self.toolbar_view,
                "Kein Album und kein Titel passt zur Suche.",
                title="Keine Treffer",
                icon="system-search-symbolic",
            )
            return
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        if albums:
            box.append(_heading(f"Alben · {len(albums)}"))
            # Die Knopfreihe selbst bleibt wie sie ist (ausdrücklich so
            # gewünscht): gleich breite Zellen über die Fensterbreite. Nur der
            # INHALT jedes Treffers steht links — Cover, daneben der Text.
            flow = Gtk.FlowBox(
                selection_mode=Gtk.SelectionMode.NONE,
                max_children_per_line=4,
                row_spacing=6,
                column_spacing=6,
                margin_start=16,
                margin_end=16,
                margin_bottom=6,
            )
            for album in albums[:48]:
                flow.append(self._album_chip(album))
            box.append(flow)
        if tracks:
            box.append(_heading(f"Titel · {len(tracks)}"))
            self.list_shows_tracks = True
            self.column_list = ColumnList(
                self.ctx.view_prefs,
                "allTracks",
                _track_columns(self._track_actions, show_track_no=False, show_album=True),
                tracks,
                on_activate=lambda rows, index: self.ctx.music.play_queue(rows, index),
            )
            box.append(self.column_list)
        else:
            self.column_list = None
            self.list_shows_tracks = False
        self._update_columns_button()
        self.toolbar_view.set_content(box)

    def _album_chip(self, album: dict) -> Gtk.Widget:
        """Ein Albentreffer: kleines Cover links, Titel und Künstler daneben,
        alles linksbündig (Wunsch 2026-09-13)."""
        # Der Knopf füllt seine Zelle, der Inhalt darin steht links.
        button = Gtk.Button()
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, halign=Gtk.Align.START, hexpand=True)

        # Fester Bildrahmen statt eines nackten `Gtk.Picture` — siehe
        # `_image_frame` in widgets/card.py: ein Bild meldet sonst eine
        # Naturbreite nach seinem Seitenverhältnis und zieht die Zeile auf.
        frame, cover = _image_frame(38, 38)
        frame.set_valign(Gtk.Align.CENTER)
        load_poster_async(
            cover, self.ctx.client, self.ctx.client.album_cover_path(int(album["id"])), decode_width=76
        )
        row.append(frame)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, halign=Gtk.Align.START)
        title = Gtk.Label(label=album.get("album") or "Album", xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=28)
        text.append(title)
        if album.get("artist"):
            artist = Gtk.Label(label=album["artist"], xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=28)
            artist.add_css_class("dim-label")
            artist.add_css_class("caption")
            text.append(artist)
        row.append(text)

        button.set_child(row)
        button.connect("clicked", lambda _b, a=album: self._open_album(a))
        return button

    # -- Zufallswiedergabe ----------------------------------------------

    def _play_shuffle(self) -> None:
        """Gemischt wird, was gerade gezeigt wird.

        In einer Titelliste (Alle Titel, Suchtreffer) genau diese Titel — in
        der Alben-Ansicht alle Titel der Bibliothek, gefiltert wie die Ansicht
        selbst. Alben zu mischen ergäbe keine Warteschlange."""
        if self.column_list is not None and self.list_shows_tracks:
            self._start_shuffle(self.column_list.visible_rows())
            return
        if self.all_tracks is None:
            # In der Alben-Ansicht sind die Titel noch nie geladen worden.
            _toast(self, "Titel werden geladen …")
            threading.Thread(target=self._load_tracks_then_shuffle, daemon=True).start()
            return
        self._start_shuffle(self._matching_tracks(self.all_tracks))

    def _load_tracks_then_shuffle(self) -> None:
        try:
            tracks = self.ctx.client.items(self.library["id"], sort="title")
        except GoldfishAPIError as exc:
            GLib.idle_add(_toast, self, f"Titel ließen sich nicht laden: {exc}")
            return

        def apply() -> bool:
            self.all_tracks = tracks
            self._start_shuffle(self._matching_tracks(tracks))
            return False

        GLib.idle_add(apply)

    def _start_shuffle(self, tracks: list[dict]) -> None:
        pool = [t for t in tracks if not _is_audiobook(t)]
        if not pool:
            _toast(self, "Nichts zum Abspielen.")
            return
        random.shuffle(pool)
        self.ctx.music.play_queue(pool, 0)

    # -- Aktionen in den Zeilen ------------------------------------------

    def _album_actions(self, album: dict) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2, valign=Gtk.Align.CENTER)
        box.append(_icon_button("media-playback-start-symbolic", "Album abspielen", lambda: self._play_album(album)))
        fav = Gtk.ToggleButton(
            icon_name="emblem-favorite-symbolic",
            has_frame=False,
            valign=Gtk.Align.CENTER,
            active=bool(album.get("favorite")),
            tooltip_text="Album als Favorit",
        )
        fav.connect("toggled", lambda button, a=album: self._on_album_favorite(button, a))
        box.append(fav)
        return box

    def _track_actions(self, track: dict) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2, valign=Gtk.Align.CENTER)
        box.append(_icon_button("media-playback-start-symbolic", "Ab hier abspielen", lambda: self._play_track(track)))
        box.append(_icon_button("list-add-symbolic", "An die Warteschlange anhängen", lambda: self._enqueue_found(track)))
        fav = Gtk.ToggleButton(
            icon_name="emblem-favorite-symbolic",
            has_frame=False,
            valign=Gtk.Align.CENTER,
            active=bool(track.get("favorite")),
            tooltip_text="Titel als Favorit",
        )
        fav.connect("toggled", lambda button, t=track: self._on_track_favorite(button, t))
        box.append(fav)
        return box

    def _play_track(self, track: dict) -> None:
        """Ab dem angeklickten Titel spielen; die angezeigte Liste ist die
        Warteschlange — in genau der Reihenfolge, in der sie gerade sortiert
        ist."""
        rows = self.column_list.visible_rows() if self.column_list else [track]
        index = next((i for i, row in enumerate(rows) if row is track), None)
        if index is None:
            rows, index = [track], 0
        self.ctx.music.play_queue(rows, index)

    def _play_album(self, album: dict) -> None:
        album_id = int(album["id"])

        def worker() -> None:
            try:
                detail = self.ctx.client.album(album_id)
            except GoldfishAPIError as exc:
                GLib.idle_add(_toast, self, f"Album ließ sich nicht laden: {exc}")
                return
            tracks = detail.get("tracks") or []
            if not tracks:
                GLib.idle_add(_toast, self, "Dieses Album enthält keine Titel.")
                return
            GLib.idle_add(lambda: (self.ctx.music.play_queue(tracks, 0), False)[1])

        threading.Thread(target=worker, daemon=True).start()

    def _on_album_favorite(self, button: Gtk.ToggleButton, album: dict) -> None:
        state = button.get_active()
        if state == bool(album.get("favorite")):
            return  # nur das Wiederverwenden der Zeile, kein Klick
        album["favorite"] = state
        album_id = int(album["id"])

        def worker() -> None:
            try:
                self.ctx.client.set_album_favorite(album_id, state)
            except GoldfishAPIError as exc:
                GLib.idle_add(_toast, self, f"Favorit nicht gespeichert: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_track_favorite(self, button: Gtk.ToggleButton, track: dict) -> None:
        state = button.get_active()
        if state == bool(track.get("favorite")):
            return
        track["favorite"] = state
        item_id = int(track["id"])

        def worker() -> None:
            try:
                self.ctx.client.set_favorite(item_id, state)
            except GoldfishAPIError as exc:
                GLib.idle_add(_toast, self, f"Favorit nicht gespeichert: {exc}")

        threading.Thread(target=worker, daemon=True).start()

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
        # Das Spalten-Menü gehört zur Tabelle und entsteht deshalb erst mit
        # ihr — bis die Titel geladen sind, bleibt der Knopf abgeblendet.
        self.columns_button = Gtk.MenuButton(label="Spalten", tooltip_text="Welche Spalten die Titelliste zeigt", sensitive=False)
        header.pack_end(self.columns_button)
        toolbar_view.add_top_bar(header)

        super().__init__(title=album.get("album") or "Album", tag=f"album-{album['id']}", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.album = album
        self.toolbar_view = toolbar_view
        self.tracks: list[dict] = []
        self.column_list: ColumnList | None = None

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
        """Kopf (Cover, Angaben, Aktionen) fest oben, darunter die Tabelle.

        Die Tabelle bringt ihren eigenen Bildlauf mit — sie darf deshalb NICHT
        noch einmal in einen gesteckt werden: ineinander liegende Bildläufe
        sind mit dem Rad kaum zu treffen, und die Spaltenköpfe wären beim
        Blättern weg."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=16, margin_bottom=12)
        box.append(self._header())
        box.append(self._track_list())
        self.toolbar_view.set_content(box)

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
        """Die Titel als Tabelle mit verschiebbaren Spalten — dieselbe
        Mechanik wie in der Bibliotheksliste, nur ein eigener Kontext, damit
        beide ihre eigenen Breiten behalten."""
        self.column_list = ColumnList(
            self.ctx.view_prefs,
            "albumTracks",
            _track_columns(self._track_actions, show_track_no=True, show_album=False),
            self.tracks,
            on_activate=lambda rows, index: self.ctx.music.play_queue(rows, index),
        )
        self.columns_button.set_sensitive(True)
        self.columns_button.set_popover(_columns_popover(self.column_list))
        return self.column_list

    def _track_actions(self, track: dict) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2, valign=Gtk.Align.CENTER)
        box.append(_icon_button("media-playback-start-symbolic", "Ab hier abspielen", lambda: self._play_from(track)))
        # Einzelnen Titel anhängen. Vorher gab es das nur für ein ganzes
        # Album ("➕ Anhängen" oben) — für einen einzelnen Titel fehlte
        # jeder Weg in die Warteschlange.
        box.append(_icon_button("list-add-symbolic", "An die Warteschlange anhängen", lambda: self._enqueue_track(track)))
        fav = Gtk.ToggleButton(
            icon_name="emblem-favorite-symbolic",
            has_frame=False,
            valign=Gtk.Align.CENTER,
            active=bool(track.get("favorite")),
            tooltip_text="Titel als Favorit",
        )
        fav.connect("toggled", lambda button, t=track: self._on_track_favorite(button, t))
        box.append(fav)
        return box

    def _play_from(self, track: dict) -> None:
        rows = self.column_list.visible_rows() if self.column_list else self.tracks
        index = next((i for i, row in enumerate(rows) if row is track), None)
        if index is None:
            rows, index = [track], 0
        self.ctx.music.play_queue(rows, index)

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
        if state == bool(track.get("favorite")):
            return  # nur das Wiederverwenden der Zeile, kein Klick
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
