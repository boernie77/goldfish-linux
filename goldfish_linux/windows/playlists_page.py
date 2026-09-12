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
from ..widgets.grid import CardGrid  # noqa: E402


class PlaylistsPage(Adw.NavigationPage):
    def __init__(self, ctx, nav_view: Adw.NavigationView):
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Neue Playlist")
        add.connect("clicked", lambda *_: self._ask_new())
        header.pack_end(add)
        shuffle = Gtk.Button(
            icon_name="media-playlist-shuffle-symbolic",
            tooltip_text="Zufällig aus allen Playlists",
        )
        shuffle.connect("clicked", lambda *_: self._play_random())
        header.pack_start(shuffle)
        toolbar_view.add_top_bar(header)

        super().__init__(title="Playlists", tag="playlists", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.toolbar_view = toolbar_view
        self.entries: list[dict] = []

        self._reload()

    def _reload(self) -> None:
        _busy(self.toolbar_view)
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            # Beide Arten holen: die Übersicht zeigt alles, gekennzeichnet
            # durch ein Symbol.
            video = self.ctx.client.playlists("video")
            music = self.ctx.client.playlists("music")
        except GoldfishAPIError as exc:
            GLib.idle_add(_error, self.toolbar_view, str(exc))
            return
        GLib.idle_add(self._apply, video, music)

    def _apply(self, video: list[dict], music: list[dict]) -> bool:
        entries = video + music
        self.entries = entries
        if not entries:
            _error(
                self.toolbar_view,
                "Über das Plus oben rechts lässt sich eine anlegen.",
                title="Noch keine Playlists",
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
        """Ein zufälliger Titel aus allen Playlists zusammen.

        Der Server kann nur innerhalb EINER Playlist zufällig ziehen
        (`playlistId=`). Damit trotzdem jeder Titel dieselbe Chance hat, wird
        die Playlist vorher nach ihrer Länge gewichtet gezogen — das ergibt
        zusammen eine Gleichverteilung über alle enthaltenen Titel und kostet
        nur eine Abfrage."""
        candidates = [pl for pl in self.entries if (pl.get("itemCount") or 0) > 0]
        if not candidates:
            _toast(self, "Die Playlists sind leer.")
            return
        weights = [pl.get("itemCount") or 0 for pl in candidates]
        playlist = random.choices(candidates, weights=weights, k=1)[0]

        def worker() -> None:
            try:
                item = self.ctx.client.random_item(playlist_id=int(playlist["id"]))
            except GoldfishAPIError as exc:
                GLib.idle_add(_toast, self, f"Kein Zufallstreffer: {exc}")
                return
            if not item:
                GLib.idle_add(_toast, self, "Kein passender Titel gefunden.")
                return
            GLib.idle_add(self._open_random, item, playlist)

        threading.Thread(target=worker, daemon=True).start()

    def _open_random(self, item: dict, playlist: dict) -> bool:
        page = PlaylistItemsPage(self.ctx, self.nav_view, playlist)
        self.nav_view.push(page)
        page.start_with(item)
        return False

    def _ask_new(self) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self.ctx.window,
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
                self.ctx.client.create_playlist(name, "video")
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
        self._start_item: dict | None = None

        _busy(toolbar_view)
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            items = self.ctx.client.playlist_items(self.playlist["id"])
        except GoldfishAPIError as exc:
            GLib.idle_add(_error, self.toolbar_view, str(exc))
            return
        GLib.idle_add(self._apply, items)

    def _apply(self, items: list[dict]) -> bool:
        self.items = items
        if not items:
            _error(
                self.toolbar_view,
                "Titel kommen über die Detailansicht hinzu.",
                title="Playlist ist leer",
                icon="view-list-symbolic",
            )
            return False
        grid = CardGrid(
            self.ctx.client,
            "music" if self.playlist.get("kind") == "music" else "movies",
            on_item=self._open_item,
            on_toggle_watched=lambda it, w: self._background(lambda: self.ctx.client.set_watched(it["id"], w)),
            on_toggle_favorite=lambda it, f: self._background(lambda: self.ctx.client.set_favorite(it["id"], f)),
        )
        grid.set_content([], items)
        self.toolbar_view.set_content(grid)
        if self._start_item is not None:
            item, self._start_item = self._start_item, None
            # Die Warteschlange ist jetzt die ganze Playlist, beginnend beim
            # gezogenen Titel — sonst endete der Zufallstreffer nach einem Stück.
            ids = [i.get("id") for i in items]
            start = ids.index(item.get("id")) if item.get("id") in ids else 0
            queue = items[start:] + items[:start]
            if self._is_music():
                self.ctx.music.play_queue(queue, 0)
            else:
                from .detail_page import DetailPage

                self.nav_view.push(DetailPage(self.ctx, self.nav_view, queue[0], queue=queue))
        return False

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
        from .detail_page import DetailPage

        self.nav_view.push(DetailPage(self.ctx, self.nav_view, order[0], queue=order))

    def start_with(self, item: dict) -> None:
        """Von der Übersicht aus: diese Playlist öffnen und mit genau diesem
        Titel beginnen. Die Titel sind beim Öffnen noch nicht geladen, deshalb
        wird der Wunsch gemerkt und in `_apply` ausgeführt."""
        self._start_item = item

    def _ask_rename(self) -> None:
        dialog = Adw.MessageDialog(transient_for=self.ctx.window, heading="Playlist umbenennen")
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
            transient_for=self.ctx.window,
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
