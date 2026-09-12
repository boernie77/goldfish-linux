"""Sammlungen: Filmreihen, wie TMDB sie führt (James Bond, Star Wars …).

Die Übersicht zeigt jede Reihe mit "vorhanden/gesamt". Beim Öffnen erscheinen
alle Teile chronologisch — die vorhandenen normal, die fehlenden
zurückgeblendet mit dem Hinweis "Fehlt". So ist auf einen Blick zu sehen, was
der Reihe noch abgeht.

Ein Teil kann auch dann als fehlend erscheinen, wenn die Datei existiert, der
angemeldete Benutzer aber keinen Zugriff auf ihre Bibliothek hat — der Server
verschweigt den Unterschied bewusst, statt die Existenz preiszugeben.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..api import GoldfishAPIError  # noqa: E402
from ..widgets.card import SimpleCard, card_flow  # noqa: E402


class CollectionsPage(Adw.NavigationPage):
    def __init__(self, ctx, nav_view: Adw.NavigationView):
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        search = Gtk.SearchEntry(placeholder_text="Sammlung suchen…")
        header.set_title_widget(search)
        toolbar_view.add_top_bar(header)

        super().__init__(title="Sammlungen", tag="collections", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.toolbar_view = toolbar_view
        self.collections: list[dict] = []
        self.search = search
        search.connect("search-changed", lambda *_: self._render())

        _busy(toolbar_view)
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            data = self.ctx.client.collections()
        except GoldfishAPIError as exc:
            GLib.idle_add(_error, self.toolbar_view, str(exc))
            return
        GLib.idle_add(self._apply, data)

    def _apply(self, data: list[dict]) -> bool:
        self.collections = data
        self._render()
        return False

    def _render(self) -> None:
        needle = self.search.get_text().strip().lower()
        shown = [c for c in self.collections if not needle or needle in (c.get("name") or "").lower()]
        if not shown:
            _error(self.toolbar_view, "Keine Sammlung passt zur Suche.", title="Keine Treffer", icon="system-search-symbolic")
            return

        flow = card_flow()
        for col in shown:
            flow.append(self._collection_card(col))
        self.toolbar_view.set_content(Gtk.ScrolledWindow(vexpand=True, child=flow))

    def _collection_card(self, col: dict) -> Gtk.Widget:
        have = col.get("movieCount") or 0
        total = col.get("partCount") or 0
        # Ohne Poster der Reihe das Poster des ältesten enthaltenen Films.
        image = (
            f"/api/poster/collection/{col['id']}"
            if col.get("posterPath")
            else (f"/api/poster/metadata/{col['fallbackMetaId']}" if col.get("fallbackMetaId") else None)
        )
        return SimpleCard(
            self.ctx.client,
            image,
            (col.get("name") or "").replace(" Filmreihe", ""),
            subtitle=f"{have} von {total} Filmen" if total else f"{have} Filme",
            badge=f"{have}/{total}" if total else str(have),
            corner="✓" if total and have >= total else "",
            on_click=lambda c=col: self._open(c),
        )

    def _open(self, col: dict) -> None:
        self.nav_view.push(CollectionPartsPage(self.ctx, self.nav_view, col))


class CollectionPartsPage(Adw.NavigationPage):
    def __init__(self, ctx, nav_view: Adw.NavigationView, collection: dict):
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        super().__init__(title=collection.get("name") or "Sammlung", tag=f"collection-{collection['id']}", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.collection = collection
        self.toolbar_view = toolbar_view

        _busy(toolbar_view)
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            parts = self.ctx.client.collection_parts(self.collection["id"])
        except GoldfishAPIError as exc:
            GLib.idle_add(_error, self.toolbar_view, str(exc))
            return
        GLib.idle_add(self._apply, parts)

    def _apply(self, parts: list[dict]) -> bool:
        # Chronologisch, wie im Browser — bei einer Filmreihe ist die
        # Erscheinungsfolge die einzige sinnvolle Ordnung.
        parts = sorted(parts, key=lambda p: (p.get("releaseDate") or "9999"))
        flow = card_flow()
        for part in parts:
            flow.append(self._part_card(part))
        self.toolbar_view.set_content(Gtk.ScrolledWindow(vexpand=True, child=flow))
        return False

    def _part_card(self, part: dict) -> Gtk.Widget:
        owned = bool(part.get("owned"))
        item = part.get("item") or {}
        year = (part.get("releaseDate") or "")[:4]
        unreleased = not part.get("releaseDate") or (part.get("releaseDate") or "") > _today()

        if owned and item.get("id"):
            image = self.ctx.client.poster_path_for_item(item)
        else:
            image = self.ctx.client.tmdb_image_url(part.get("posterPath"))

        return SimpleCard(
            self.ctx.client,
            image,
            part.get("title") or "",
            subtitle=year,
            badge="" if owned else ("Bald" if unreleased else "Fehlt"),
            on_click=(lambda p=part: self._open_part(p)) if owned and item.get("id") else None,
            dimmed=not owned,
            tooltip="" if owned else ("Noch nicht erschienen" if unreleased else "Nicht in der Sammlung vorhanden"),
        )

    def _open_part(self, part: dict) -> None:
        from .detail_page import DetailPage

        item = part.get("item") or {}
        if item.get("id"):
            self.nav_view.push(DetailPage(self.ctx, self.nav_view, item))


def _today() -> str:
    from datetime import date

    return date.today().isoformat()


def _busy(toolbar_view: Adw.ToolbarView) -> None:
    spinner = Gtk.Spinner(width_request=48, height_request=48)
    wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    wrap.append(spinner)
    toolbar_view.set_content(wrap)
    spinner.start()


def _error(toolbar_view: Adw.ToolbarView, message: str, title: str = "Fehler", icon: str = "dialog-error-symbolic") -> bool:
    toolbar_view.set_content(Adw.StatusPage(icon_name=icon, title=title, description=message))
    return False
