"""Ordner-/Item-Browser für eine Bibliothek.

Vereinfachtes Modell gegenüber der Web-UI (kein Staffel-Ansicht-Rendering,
kein Merge doppelter Serien-Ordner, keine Auflösungs-/Genre-Filter-Picker in
v1) — pro Ebene werden Unterordner (`GET /folders?parent=`) UND direkt darin
liegende Items (`GET /items?folder=`) geladen und als zwei Listen
untereinander gezeigt. Klick auf einen Ordner navigiert eine Ebene tiefer
(neue BrowsePage wird auf den Adw.NavigationView gepusht), Klick auf ein Item
öffnet die Detailseite.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..api import GoldfishAPIError  # noqa: E402
from ..formatting import format_duration, format_resolution, format_size  # noqa: E402
from ..widgets.poster import load_poster_async  # noqa: E402
from .detail_page import DetailPage  # noqa: E402


def build_item_row(ctx, item: dict, on_activate) -> Adw.ActionRow:
    """Gemeinsamer Zeilen-Baustein für Items — auch von downloads_page.py
    genutzt (dort ohne Poster-Nachladen, siehe dort)."""
    metadata = item.get("metadata") or {}
    title = metadata.get("title") or item.get("title") or "Unbenannt"
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
    subtitle = " · ".join(parts)

    row = Adw.ActionRow(title=GLib.markup_escape_text(title), subtitle=subtitle)
    row.set_activatable(True)

    picture = Gtk.Picture()
    picture.set_size_request(56, 84)
    picture.set_content_fit(Gtk.ContentFit.COVER)
    row.add_prefix(picture)

    if item.get("watched"):
        check = Gtk.Image(icon_name="object-select-symbolic")
        check.set_tooltip_text("Gesehen")
        row.add_suffix(check)

    row.connect("activated", lambda *_: on_activate(item))

    poster_path = ctx.client.poster_path_for_item(item)
    load_poster_async(picture, ctx.client, poster_path)
    return row


class BrowsePage(Adw.NavigationPage):
    def __init__(self, ctx, nav_view: Adw.NavigationView, library: dict, folder: str = "", title: str | None = None):
        page_title = title or (folder.rsplit("/", 1)[-1] if folder else library["name"])

        # Adw.NavigationPage.child ist eine construct-only-Property (kein
        # set_child() danach nutzbar) — das Inhalts-Widget muss deshalb VOR
        # super().__init__() fertig gebaut sein.
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        search_entry = Gtk.SearchEntry(placeholder_text="Suchen…")
        header.set_title_widget(search_entry)
        toolbar_view.add_top_bar(header)

        super().__init__(title=page_title, tag=f"browse-{library['id']}-{folder}", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.library = library
        self.folder = folder
        self.toolbar_view = toolbar_view
        self.search_entry = search_entry
        self.search_entry.connect("search-changed", self._on_search_changed)

        self._show_loading()
        self._load()

    # -- UI-Zustände ------------------------------------------------------

    def _show_loading(self) -> None:
        # Gtk.Spinner statt Adw.Spinner — letzteres braucht libadwaita ≥ 1.6,
        # neuer als das, was Debian 12/Ubuntu 22.04 mitbringen.
        spinner = Gtk.Spinner()
        spinner.set_size_request(48, 48)
        spinner.start()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        box.append(spinner)
        self.toolbar_view.set_content(box)

    def _show_error(self, message: str) -> None:
        status = Adw.StatusPage(icon_name="dialog-error-symbolic", title="Fehler", description=message)
        self.toolbar_view.set_content(status)

    def _show_results(self, folders: list[dict], items: list[dict]) -> None:
        scrolled = Gtk.ScrolledWindow(vexpand=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=24)
        scrolled.set_child(box)

        if not folders and not items:
            status = Adw.StatusPage(
                icon_name="folder-open-symbolic",
                title="Keine Inhalte",
                description="Dieser Ordner enthält nichts Sichtbares.",
            )
            status.set_vexpand(True)
            self.toolbar_view.set_content(status)
            return

        if folders:
            group = Adw.PreferencesGroup(title="Ordner")
            for folder in folders:
                group.add(self._build_folder_row(folder))
            box.append(group)

        if items:
            group = Adw.PreferencesGroup()
            if folders:
                group.set_title("Videos")
            for item in items:
                group.add(build_item_row(self.ctx, item, self._open_detail))
            box.append(group)

        clamp = Adw.Clamp(maximum_size=900)
        clamp.set_child(box)
        scrolled.set_child(clamp)
        self.toolbar_view.set_content(scrolled)

    def _build_folder_row(self, folder: dict) -> Adw.ActionRow:
        name = folder["name"].rsplit("/", 1)[-1]
        count = folder.get("itemCount", 0)
        row = Adw.ActionRow(title=GLib.markup_escape_text(name), subtitle=f"{count} Titel")
        row.set_activatable(True)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda *_: self._open_folder(folder))
        return row

    # -- Aktionen -----------------------------------------------------

    def _open_folder(self, folder: dict) -> None:
        page = BrowsePage(self.ctx, self.nav_view, self.library, folder=folder["name"])
        self.nav_view.push(page)

    def _open_detail(self, item: dict) -> None:
        page = DetailPage(self.ctx, self.nav_view, item)
        self.nav_view.push(page)

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._load(search=entry.get_text().strip())

    # -- Laden --------------------------------------------------------

    def _load(self, search: str = "") -> None:
        self._show_loading()
        threading.Thread(target=self._load_worker, args=(search,), daemon=True).start()

    def _load_worker(self, search: str) -> None:
        # WICHTIG: `folder=""` bedeutet server-seitig "kein Filter" (liefert
        # ALLE Items der Bibliothek rekursiv, nicht nur die auf Root-Ebene!)
        # — der Sonderwert `"/"` steht für "nur Root-Ebene" (kein Slash im
        # rel_path). Auf der Bibliotheks-Wurzel (self.folder == "") muss also
        # "/" gesendet werden, sonst zeigt die Root-Ansicht Items aus JEDEM
        # Unterordner gemischt mit den Root-Items (siehe internal/store/items.go
        # `switch f.Folder` im Server-Repo). Bei einer aktiven Suche bleibt es
        # bewusst bei self.folder (auch "" = library-weite Suche).
        item_folder = self.folder if (search or self.folder) else "/"
        try:
            folders = [] if search else self.ctx.client.folders(self.library["id"], parent=self.folder)
            items = self.ctx.client.items(self.library["id"], folder=item_folder, search=search, sort="title")
        except GoldfishAPIError as exc:
            GLib.idle_add(self._show_error, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — Hintergrund-Thread darf nie
            # stumm sterben, sonst bleibt die Seite für immer auf dem
            # Lade-Spinner hängen ("keine Inhalte", ohne jede Fehlermeldung).
            GLib.idle_add(self._show_error, f"Unerwarteter Fehler: {exc}")
            return
        GLib.idle_add(self._show_results, folders, items)
