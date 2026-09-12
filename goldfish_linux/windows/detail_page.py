"""Detail-Ansicht für ein einzelnes Item: Poster, Plot, Play/Download/
Gesehen/Favorit."""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..api import GoldfishAPIError  # noqa: E402
from ..formatting import format_duration, format_resolution, format_size  # noqa: E402
from ..widgets.poster import load_poster_async  # noqa: E402
from .player_window import PlayerWindow  # noqa: E402


class DetailPage(Adw.NavigationPage):
    def __init__(self, ctx, nav_view: Adw.NavigationView, item: dict):
        metadata = item.get("metadata") or {}
        title = metadata.get("title") or item.get("title") or "Unbenannt"

        # Adw.NavigationPage.child ist construct-only — Widget-Baum muss vor
        # super().__init__() feststehen (Inhalt darf danach noch befüllt
        # werden, nur der Ziel-Container selbst nicht mehr getauscht).
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        clamp = Adw.Clamp(maximum_size=700, margin_top=24, margin_bottom=24, margin_start=18, margin_end=18)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        clamp.set_child(box)
        scrolled.set_child(clamp)
        toolbar_view.set_content(scrolled)

        super().__init__(title=title, tag=f"detail-{item['id']}", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.item = item
        self.item_id = int(item["id"])

        self.picture = Gtk.Picture()
        self.picture.set_size_request(200, 300)
        self.picture.set_content_fit(Gtk.ContentFit.COVER)
        self.picture.set_halign(Gtk.Align.CENTER)
        box.append(self.picture)
        poster_path = ctx.client.poster_path_for_item(item)
        load_poster_async(self.picture, ctx.client, poster_path)

        title_label = Gtk.Label(label=title, wrap=True, justify=Gtk.Justification.CENTER)
        title_label.add_css_class("title-1")
        box.append(title_label)

        parts = []
        year = metadata.get("year")
        if year:
            parts.append(str(year))
        res = format_resolution(item.get("width", 0), item.get("height", 0))
        if res:
            parts.append(res)
        dur = format_duration(item.get("durationSec", 0))
        if dur:
            parts.append(dur)
        size = format_size(item.get("sizeBytes", 0))
        if size:
            parts.append(size)
        if parts:
            subtitle_label = Gtk.Label(label=" · ".join(parts), justify=Gtk.Justification.CENTER)
            subtitle_label.add_css_class("dim-label")
            box.append(subtitle_label)

        overview = metadata.get("overview")
        if overview:
            overview_label = Gtk.Label(label=overview, wrap=True, justify=Gtk.Justification.LEFT)
            overview_label.set_halign(Gtk.Align.FILL)
            box.append(overview_label)

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.CENTER)
        box.append(button_row)

        play_button = Gtk.Button(label="▶ Abspielen")
        play_button.add_css_class("suggested-action")
        play_button.add_css_class("pill")
        play_button.connect("clicked", lambda *_: self._open_player(local_path=None))
        button_row.append(play_button)

        self.watched_toggle = Gtk.ToggleButton(label="✓ Gesehen", active=bool(item.get("watched")))
        self.watched_toggle.connect("toggled", self._on_watched_toggled)
        button_row.append(self.watched_toggle)

        self.favorite_toggle = Gtk.ToggleButton(label="♥ Favorit", active=bool(item.get("favorite")))
        self.favorite_toggle.connect("toggled", self._on_favorite_toggled)
        button_row.append(self.favorite_toggle)

        self.download_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER)
        box.append(self.download_box)
        self._refresh_download_ui()

    # -- Wiedergabe -----------------------------------------------------

    def _open_player(self, local_path: str | None) -> None:
        window = PlayerWindow(self.ctx.application, self.ctx.client, self.item, local_path=local_path)
        window.set_transient_for(self.ctx.window)
        window.present()

    # -- Gesehen/Favorit --------------------------------------------------

    def _on_watched_toggled(self, button: Gtk.ToggleButton) -> None:
        watched = button.get_active()
        button.set_label("✓ Gesehen" if watched else "Als gesehen markieren")
        threading.Thread(target=self._set_watched_worker, args=(watched,), daemon=True).start()

    def _set_watched_worker(self, watched: bool) -> None:
        try:
            self.ctx.client.set_watched(self.item_id, watched)
        except GoldfishAPIError:
            pass  # v1: stiller Fehler, Server bleibt Wahrheitsquelle beim nächsten Laden

    def _on_favorite_toggled(self, button: Gtk.ToggleButton) -> None:
        favorite = button.get_active()
        button.set_label("♥ Favorit" if favorite else "♡ Favorit")
        threading.Thread(target=self._set_favorite_worker, args=(favorite,), daemon=True).start()

    def _set_favorite_worker(self, favorite: bool) -> None:
        try:
            self.ctx.client.set_favorite(self.item_id, favorite)
        except GoldfishAPIError:
            pass

    # -- Download ----------------------------------------------------------

    def _refresh_download_ui(self) -> None:
        child = self.download_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.download_box.remove(child)
            child = nxt

        if self.ctx.downloads.is_downloaded(self.item_id):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            offline_button = Gtk.Button(label="▶ Offline abspielen")
            offline_button.connect("clicked", self._on_play_offline)
            row.append(offline_button)
            delete_button = Gtk.Button(label="🗑 Download löschen")
            delete_button.add_css_class("destructive-action")
            delete_button.connect("clicked", self._on_delete_download)
            row.append(delete_button)
            self.download_box.append(row)
        elif self.ctx.downloads.is_downloading(self.item_id):
            spinner = Gtk.Spinner()
            spinner.start()
            label = Gtk.Label(label="Download läuft …")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.CENTER)
            row.append(spinner)
            row.append(label)
            self.download_box.append(row)
        else:
            download_button = Gtk.Button(label="⬇ Herunterladen")
            download_button.connect("clicked", self._on_download_clicked)
            self.download_box.append(download_button)

    def _on_download_clicked(self, *_args) -> None:
        child = self.download_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.download_box.remove(child)
            child = nxt
        self.progress_bar = Gtk.ProgressBar(show_text=True)
        self.download_box.append(self.progress_bar)
        self.ctx.downloads.start_download(
            self.item,
            on_progress=self._on_download_progress,
            on_done=self._on_download_done,
            on_error=self._on_download_error,
        )

    def _on_download_progress(self, item_id: int, fraction: float) -> bool:
        if item_id != self.item_id or not hasattr(self, "progress_bar"):
            return False
        self.progress_bar.set_fraction(fraction)
        self.progress_bar.set_text(f"{int(fraction * 100)} %")
        return False

    def _on_download_done(self, item_id: int, _local_path: str) -> bool:
        if item_id != self.item_id:
            return False
        self._refresh_download_ui()
        return False

    def _on_download_error(self, item_id: int, message: str) -> bool:
        if item_id != self.item_id:
            return False
        self._refresh_download_ui()
        toast = Adw.Toast(title=f"Download fehlgeschlagen: {message}")
        root = self.get_root()
        if isinstance(root, Adw.ApplicationWindow) and hasattr(root, "toast_overlay"):
            root.toast_overlay.add_toast(toast)
        return False

    def _on_play_offline(self, *_args) -> None:
        path = self.ctx.downloads.local_path(self.item_id)
        if path:
            self._open_player(local_path=str(path))

    def _on_delete_download(self, *_args) -> None:
        self.ctx.downloads.delete_download(self.item_id)
        self._refresh_download_ui()
