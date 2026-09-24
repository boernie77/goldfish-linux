"""Übersicht der offline heruntergeladenen Videos."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..formatting import format_duration, format_size  # noqa: E402
from .player_window import open_player  # noqa: E402


class DownloadsPage(Adw.NavigationPage):
    def __init__(self, ctx, nav_view: Adw.NavigationView):
        # Adw.NavigationPage.child ist construct-only — toolbar_view muss
        # bereits vor super().__init__() existieren.
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())

        super().__init__(title="Downloads", tag="downloads", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.toolbar_view = toolbar_view
        self.refresh()

    def refresh(self) -> None:
        records = self.ctx.downloads.list_downloads()
        if not records:
            status = Adw.StatusPage(
                icon_name="folder-download-symbolic",
                title="Keine Downloads",
                description="Lade Videos über die Detailansicht herunter, um sie hier offline abzuspielen.",
            )
            self.toolbar_view.set_content(status)
            return

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        clamp = Adw.Clamp(maximum_size=900, margin_top=12, margin_bottom=24, margin_start=12, margin_end=12)
        group = Adw.PreferencesGroup()
        for rec in records:
            group.add(self._build_row(rec))
        clamp.set_child(group)
        scrolled.set_child(clamp)
        self.toolbar_view.set_content(scrolled)

    def _build_row(self, rec: dict) -> Adw.ActionRow:
        title = rec.get("title") or f"Item {rec['id']}"
        parts = []
        dur = format_duration(rec.get("durationSec", 0))
        if dur:
            parts.append(dur)
        size = format_size(rec.get("sizeBytes", 0))
        if size:
            parts.append(size)
        row = Adw.ActionRow(title=GLib.markup_escape_text(title), subtitle=" · ".join(parts))
        row.set_activatable(True)
        row.connect("activated", lambda *_: self._play(rec))

        delete_button = Gtk.Button(icon_name="user-trash-symbolic")
        delete_button.add_css_class("flat")
        delete_button.set_tooltip_text("Download löschen")
        delete_button.connect("clicked", lambda *_: self._delete(rec))
        row.add_suffix(delete_button)
        return row

    def _play(self, rec: dict) -> None:
        item = {"id": rec["id"], "title": rec.get("title", ""), "durationSec": rec.get("durationSec", 0)}
        self._play_with_resume_check(item, rec["path"])

    def _play_with_resume_check(self, item: dict, path: str) -> None:
        """Fragt nach, wenn der Download schon einmal angesehen wurde.

        Die Position kommt rein lokal aus der Downloads-Registry (kein
        Server-Aufruf — beim Offline-Abspielen ist der Server typischerweise
        gar nicht erreichbar), genau wie bei Server-Titeln in `detail_page.py`."""
        item_id = int(item["id"])
        position = self.ctx.downloads.get_local_resume(item_id)
        duration = item.get("durationSec") or 0
        if position < 60 or (duration and position > duration * 0.95):
            open_player(self.ctx, item, local_path=path)
            return

        dialog = Adw.MessageDialog(
            transient_for=self.ctx.dialog_parent(),
            heading="Weiterschauen?",
            body=f"Du warst bei {format_duration(position)} von {format_duration(duration)}.",
        )
        dialog.add_response("start", "Von Anfang")
        dialog.add_response("resume", f"Bei {format_duration(position)} fortsetzen")
        dialog.set_default_response("resume")
        dialog.set_response_appearance("resume", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _d, response: open_player(
                self.ctx, item, local_path=path, start_position=position if response == "resume" else 0.0
            ),
        )
        dialog.present()

    def _delete(self, rec: dict) -> None:
        self.ctx.downloads.delete_download(rec["id"])
        self.refresh()
