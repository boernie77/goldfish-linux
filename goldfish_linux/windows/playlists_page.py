"""Playlists: eigene Zusammenstellungen, getrennt nach Video und Musik.

Die Trennung kommt vom Server (`playlists.kind`) und ist Absicht — ein
Musiktitel soll nicht in einer Videoliste landen. Playlists sind streng
privat: der Server liefert nur die eigenen, auch einem Verwalter.
"""

from __future__ import annotations

import random
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..api import GoldfishAPIError  # noqa: E402
from ..widgets.card import SimpleCard, card_flow  # noqa: E402
from ..widgets.column_list import ColumnList  # noqa: E402
from ..widgets.grid import CardGrid  # noqa: E402


class PlaylistsPage(Adw.NavigationPage):
    """Übersicht der eigenen Playlists.

    `kind=None` (Standard, von der Seitenleiste aus) zeigt Video- UND
    Musik-Playlists gemischt, unterschieden per 🎵/🎬-Abzeichen — wie bisher.
    `kind="music"` (vom Musik-Bibliotheks-Header aus, Build-211-Parität)
    zeigt und erzeugt NUR Musik-Playlists — eine eigenständige Ansicht, ohne
    dass zum Umschauen erst die Musikbibliothek verlassen werden muss."""

    def __init__(self, ctx, nav_view: Adw.NavigationView, kind: str | None = None):
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Neue Playlist")
        add.connect("clicked", lambda *_: self._ask_new())
        header.pack_end(add)
        shuffle = Gtk.Button(
            icon_name="media-playlist-shuffle-symbolic",
            tooltip_text=f"Zufällig aus allen {'Musik-' if kind == 'music' else ''}Playlists",
        )
        shuffle.connect("clicked", lambda *_: self._play_random())
        header.pack_start(shuffle)
        toolbar_view.add_top_bar(header)

        title = "Musik-Playlists" if kind == "music" else "Playlists"
        super().__init__(title=title, tag=f"playlists-{kind or 'all'}", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.kind = kind
        self.toolbar_view = toolbar_view
        self.entries: list[dict] = []

        self._reload()

    def _reload(self) -> None:
        _busy(self.toolbar_view)
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            if self.kind:
                entries = self.ctx.client.playlists(self.kind)
            else:
                # Beide Arten holen: die gemischte Übersicht zeigt alles,
                # unterschieden durch ein Abzeichen.
                entries = self.ctx.client.playlists("video") + self.ctx.client.playlists("music")
        except GoldfishAPIError as exc:
            GLib.idle_add(_error, self.toolbar_view, str(exc))
            return
        GLib.idle_add(self._apply, entries)

    def _apply(self, entries: list[dict]) -> bool:
        self.entries = entries
        if not entries:
            empty_title = "Noch keine Musik-Playlists" if self.kind == "music" else "Noch keine Playlists"
            _error(
                self.toolbar_view,
                "Über das Plus oben rechts lässt sich eine anlegen.",
                title=empty_title,
                icon="view-list-symbolic",
            )
            return False

        flow = card_flow()
        for pl in entries:
            flow.append(self._card(pl))
        self.toolbar_view.set_content(Gtk.ScrolledWindow(vexpand=True, child=flow))
        return False

    def _card(self, pl: dict) -> Gtk.Widget:
        music = pl.get("kind") == "music"
        count = pl.get("itemCount") or 0
        return SimpleCard(
            self.ctx.client,
            f"/api/thumb/{pl['posterItemId']}" if pl.get("posterItemId") else None,
            pl.get("name") or "",
            subtitle=f"{count} Titel" if count != 1 else "1 Titel",
            corner="🎵" if music else "🎬",
            aspect="music" if music else "movies",
            on_click=lambda p=pl: self.nav_view.push(PlaylistItemsPage(self.ctx, self.nav_view, p)),
        )

    def _play_random(self) -> None:
        """Ein zufälliger Titel aus allen Playlists zusammen — spielt SOFORT,
        bleibt aber auf der Playlist-Übersicht stehen (User-Korrektur: sprang
        vorher in die getroffene Playlist hinein und schränkte ⏭/⏮ danach auf
        genau diese eine Playlist ein — beides nicht gewollt).

        Der Server kann nur innerhalb EINER Playlist zufällig ziehen
        (`playlistId=`). Damit trotzdem jeder Titel dieselbe Chance hat, wird
        die Playlist vorher nach ihrer Länge gewichtet gezogen — das ergibt
        zusammen eine Gleichverteilung über alle enthaltenen Titel und kostet
        nur eine Abfrage pro Ziehung.

        ⏭/⏮ im Player ziehen danach *innerhalb derselben Art* (Video ODER
        Musik) weiter — einmal getroffen, bleibt es dabei, damit kein
        Musiktitel im Videofenster landet."""

        def worker() -> None:
            try:
                result = self._draw(None)
            except GoldfishAPIError as exc:
                GLib.idle_add(_toast, self, f"Kein Zufallstreffer: {exc}")
                return
            if not result:
                GLib.idle_add(_toast, self, "Die Playlists sind leer.")
                return
            item, playlist = result
            GLib.idle_add(self._open_random, item, playlist.get("kind") or "video")

        threading.Thread(target=worker, daemon=True).start()

    def _draw(self, kind_filter: str | None) -> tuple[dict, dict] | None:
        """Zieht eine Playlist gewichtet nach Länge, dann einen Zufallstitel
        daraus. `kind_filter=None` = über alle Playlists (Erstwahl),
        andernfalls nur innerhalb dieser Art (Fortsetzung im Player)."""
        candidates = [
            pl
            for pl in self.entries
            if (pl.get("itemCount") or 0) > 0 and (kind_filter is None or pl.get("kind") == kind_filter)
        ]
        if not candidates:
            return None
        weights = [pl.get("itemCount") or 0 for pl in candidates]
        playlist = random.choices(candidates, weights=weights, k=1)[0]
        item = self.ctx.client.random_item(playlist_id=int(playlist["id"]))
        if not item:
            return None
        return item, playlist

    def _open_random(self, item: dict, kind: str) -> bool:
        if kind == "music":
            self.ctx.music.play_queue([item], 0)
            return False

        def draw_next() -> dict:
            result = self._draw(kind)
            return result[0] if result else None

        from .player_window import open_player

        open_player(self.ctx, item, random_fetch=draw_next)
        return False

    def _ask_new(self) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self.ctx.dialog_parent(),
            heading="Neue Playlist",
            body="Wie soll sie heißen?",
        )
        entry = Gtk.Entry(placeholder_text="Name", activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Abbrechen")
        dialog.add_response("create", "Anlegen")
        dialog.set_default_response("create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)

        def on_response(_d, response: str) -> None:
            name = entry.get_text().strip()
            if response != "create" or not name:
                return
            self._create(name)

        dialog.connect("response", on_response)
        dialog.present()

    def _create(self, name: str) -> None:
        def worker() -> None:
            try:
                self.ctx.client.create_playlist(name, self.kind or "video")
            except GoldfishAPIError as exc:
                GLib.idle_add(_toast, self, f"Anlegen fehlgeschlagen: {exc}")
                return
            GLib.idle_add(self._reload)

        threading.Thread(target=worker, daemon=True).start()


class PlaylistItemsPage(Adw.NavigationPage):
    def __init__(self, ctx, nav_view: Adw.NavigationView, playlist: dict):
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        rename = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Umbenennen")
        rename.connect("clicked", lambda *_: self._ask_rename())
        header.pack_end(rename)
        delete = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Playlist löschen")
        delete.connect("clicked", lambda *_: self._ask_delete())
        header.pack_end(delete)
        shuffle = Gtk.Button(
            icon_name="media-playlist-shuffle-symbolic",
            tooltip_text="Playlist in zufälliger Reihenfolge abspielen",
        )
        shuffle.connect("clicked", lambda *_: self._play_shuffled())
        header.pack_start(shuffle)
        toolbar_view.add_top_bar(header)

        super().__init__(title=playlist.get("name") or "Playlist", tag=f"playlist-{playlist['id']}", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.playlist = playlist
        self.toolbar_view = toolbar_view
        self.items: list[dict] = []
        self.column_list: ColumnList | None = None
        self.mode = ctx.view_prefs.playlist_view_mode(int(playlist["id"]))

        # Umschalter + Spalten-Menü nur bei Musik: die Spalten (Künstler,
        # Album, Dauer) sind Musikbegriffe, und eine Video-Playlist öffnet
        # ohnehin die Detailseite statt etwas abzuspielen.
        if self._is_music():
            toolbar_view.add_top_bar(self._build_toolbar())

        _busy(toolbar_view)
        threading.Thread(target=self._load, daemon=True).start()

    def _build_toolbar(self) -> Gtk.Widget:
        bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_top=6,
            margin_bottom=6,
            margin_start=12,
            margin_end=12,
        )
        # BEWUSST ohne die Klasse "toolbar": libadwaita macht Knöpfe darin
        # rahmenlos. Der Zufallsknopf stand dadurch als einziger nackt neben
        # dem verbundenen Umschalter ("hängt lose in der Gegend"). Ohne die
        # Klasse trägt jeder Knopf seinen normalen Rahmen.

        modes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        modes.add_css_class("linked")
        self.mode_buttons: dict[str, Gtk.ToggleButton] = {}
        group: Gtk.ToggleButton | None = None
        # Symbole wie in der Musikbibliothek — dieselbe Bedeutung, dieselbe
        # Optik neben dem Zufallsknopf.
        for key, icon, tooltip in (
            ("grid", "view-grid-symbolic", "Titel als Kacheln"),
            ("list", "view-list-symbolic", "Titel als Liste mit Spalten"),
        ):
            button = Gtk.ToggleButton(icon_name=icon, tooltip_text=tooltip)
            if group is None:
                group = button
            else:
                button.set_group(group)
            button.set_active(key == self.mode)
            button.connect("toggled", self._on_mode_toggled, key)
            modes.append(button)
            self.mode_buttons[key] = button
        bar.append(modes)
        bar.append(Gtk.Box(hexpand=True))

        self.count_label = Gtk.Label(valign=Gtk.Align.CENTER)
        self.count_label.add_css_class("dim-label")
        bar.append(self.count_label)

        self.columns_button = Gtk.MenuButton(label="Spalten", tooltip_text="Welche Spalten die Liste zeigt", sensitive=False)
        bar.append(self.columns_button)
        return bar

    def _on_mode_toggled(self, button: Gtk.ToggleButton, key: str) -> None:
        if not button.get_active() or key == self.mode:
            return
        self.mode = key
        self.ctx.view_prefs.set_playlist_view_mode(int(self.playlist["id"]), key)
        self._render()

    def _load(self) -> None:
        try:
            items = self.ctx.client.playlist_items(self.playlist["id"])
        except GoldfishAPIError as exc:
            GLib.idle_add(_error, self.toolbar_view, str(exc))
            return
        GLib.idle_add(self._apply, items)

    def _apply(self, items: list[dict]) -> bool:
        self.items = items
        self._render()
        return False

    def _render(self) -> None:
        if not self.items:
            _error(
                self.toolbar_view,
                "Titel kommen über die Detailansicht hinzu.",
                title="Playlist ist leer",
                icon="view-list-symbolic",
            )
            return
        if self._is_music():
            self.count_label.set_label(f"{len(self.items)} Titel")
        if self._is_music() and self.mode == "list":
            self._render_list()
            return
        self.column_list = None
        if self._is_music():
            self.columns_button.set_sensitive(False)
            self.columns_button.set_popover(None)
        grid = CardGrid(
            self.ctx.client,
            "music" if self._is_music() else "movies",
            on_item=self._open_item,
            on_toggle_watched=lambda it, w: self._background(lambda: self.ctx.client.set_watched(it["id"], w)),
            on_toggle_favorite=lambda it, f: self._background(lambda: self.ctx.client.set_favorite(it["id"], f)),
        )
        grid.set_content([], self.items)
        self.toolbar_view.set_content(grid)

    def _render_list(self) -> None:
        """Dieselbe Tabelle wie in der Musikbibliothek, nur mit eigenem
        Spalten-Kontext — eine Playlist darf andere Spalten und Breiten
        haben als "Alle Titel"."""
        from .music_page import _columns_popover, _track_columns

        self.column_list = ColumnList(
            self.ctx.view_prefs,
            "playlistTracks",
            _track_columns(self._track_actions, show_track_no=False, show_album=True),
            self.items,
            on_activate=lambda rows, index: self.ctx.music.play_queue(rows, index),
        )
        self.columns_button.set_sensitive(True)
        self.columns_button.set_popover(_columns_popover(self.column_list))
        self.toolbar_view.set_content(self.column_list)

    def _track_actions(self, track: dict) -> Gtk.Widget:
        from .music_page import _icon_button

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2, valign=Gtk.Align.CENTER)
        box.append(_icon_button("media-playback-start-symbolic", "Ab hier abspielen", lambda: self._play_from(track)))
        box.append(
            _icon_button(
                "list-add-symbolic",
                "An die Warteschlange anhängen",
                lambda: (self.ctx.music.append([track]), _toast(self, "An die Warteschlange angehängt."))[0],
            )
        )
        fav = Gtk.ToggleButton(
            icon_name="emblem-favorite-symbolic",
            has_frame=False,
            valign=Gtk.Align.CENTER,
            active=bool(track.get("favorite")),
            tooltip_text="Titel als Favorit",
        )
        fav.connect("toggled", self._on_track_favorite, track)
        box.append(fav)
        return box

    def _play_from(self, track: dict) -> None:
        rows = self.column_list.visible_rows() if self.column_list else self.items
        index = next((i for i, row in enumerate(rows) if row is track), None)
        if index is None:
            rows, index = [track], 0
        self.ctx.music.play_queue(rows, index)

    def _on_track_favorite(self, button: Gtk.ToggleButton, track: dict) -> None:
        state = button.get_active()
        if state == bool(track.get("favorite")):
            return  # nur das Wiederverwenden der Zeile, kein Klick
        track["favorite"] = state
        self._background(lambda: self.ctx.client.set_favorite(int(track["id"]), state))

    def _open_item(self, item: dict) -> None:
        from .detail_page import DetailPage

        # Die Playlist ist die Warteschlange — das ist ihr eigentlicher Sinn.
        self.nav_view.push(DetailPage(self.ctx, self.nav_view, item, queue=self.items))

    def _is_music(self) -> bool:
        return self.playlist.get("kind") == "music"

    def _play_shuffled(self) -> None:
        """Die ganze Playlist in zufälliger Reihenfolge.

        Nicht bloß ein zufälliger Titel: gemischt wird die komplette Liste, die
        danach als Warteschlange weiterläuft — genau das meint "Shuffle Play".
        Sind die Titel noch nicht geladen, wartet der Knopf auf sie."""
        if not self.items:
            _toast(self, "Die Playlist ist leer.")
            return
        order = list(self.items)
        random.shuffle(order)
        if self._is_music():
            self.ctx.music.play_queue(order, 0)
            return
        self._open_video(order[0], order)

    def _open_video(self, item: dict, queue: list[dict]) -> None:
        """Video sofort abspielen, mit der Liste als Warteschlange — ein
        Zufallsknopf soll spielen, nicht nur eine Seite aufschlagen."""
        from .player_window import open_player

        open_player(self.ctx, item, queue=queue, queue_index=0)

    def _ask_rename(self) -> None:
        dialog = Adw.MessageDialog(transient_for=self.ctx.dialog_parent(), heading="Playlist umbenennen")
        entry = Gtk.Entry(text=self.playlist.get("name") or "", activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Abbrechen")
        dialog.add_response("save", "Speichern")
        dialog.set_default_response("save")

        def on_response(_d, response: str) -> None:
            name = entry.get_text().strip()
            if response != "save" or not name:
                return
            self._background(
                lambda: self.ctx.client.rename_playlist(self.playlist["id"], name),
                after=lambda: (self.set_title(name), self.playlist.update({"name": name})),
            )

        dialog.connect("response", on_response)
        dialog.present()

    def _ask_delete(self) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self.ctx.dialog_parent(),
            heading="Playlist löschen?",
            body=f"„{self.playlist.get('name')}“ wird entfernt. Die Videos selbst bleiben erhalten.",
        )
        dialog.add_response("cancel", "Abbrechen")
        dialog.add_response("delete", "Löschen")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_d, response: str) -> None:
            if response != "delete":
                return
            self._background(
                lambda: self.ctx.client.delete_playlist(self.playlist["id"]),
                after=lambda: self.nav_view.pop(),
            )

        dialog.connect("response", on_response)
        dialog.present()

    def _background(self, call, after=None) -> None:
        def worker() -> None:
            try:
                call()
            except GoldfishAPIError as exc:
                GLib.idle_add(_toast, self, str(exc))
                return
            if after is not None:
                GLib.idle_add(lambda: (after(), False)[1])

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
