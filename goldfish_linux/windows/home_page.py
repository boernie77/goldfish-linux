"""Startseite: pro Bibliothek drei Streifen — Fortsetzen, Als nächstes, Neu.

Alles kommt aus einer einzigen Abfrage (`GET /api/home`), die der Server
bereits nach der persönlichen Reihenfolge sortiert und auf die zugänglichen
Bibliotheken beschränkt. Welche Streifen erscheinen, entscheidet ebenfalls der
Server über `showContinue` und `showNextUp` — das sind Einstellungen des
Benutzers.

**Diese Abfrage ist teuer:** sie berechnet Fortsetzen und Als-nächstes über
alle Bibliotheken. Gemessen wurden 12 Sekunden auf einem beschäftigten Server.
Deshalb ein großzügiges Zeitlimit und eine sichtbare Ladeanzeige statt eines
scheinbar leeren Fensters.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..api import GoldfishAPIError  # noqa: E402
from ..widgets.card import CARD_WIDTH, CardWidget, ensure_card_css  # noqa: E402

# Länger als das übliche Zeitlimit des Clients: 12 Sekunden sind hier gemessen
# worden, und ein Abbruch wäre schlimmer als ein längeres Warten.
_HOME_TIMEOUT = 60


class HomePage(Adw.NavigationPage):
    def __init__(self, ctx, nav_view: Adw.NavigationView):
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Neu laden")
        refresh.connect("clicked", lambda *_: self._reload())
        header.pack_end(refresh)
        toolbar_view.add_top_bar(header)

        super().__init__(title="Startseite", tag="home", child=toolbar_view)
        ensure_card_css()
        self.ctx = ctx
        self.nav_view = nav_view
        self.toolbar_view = toolbar_view
        self._reload()

    def _reload(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        spinner = Gtk.Spinner(width_request=48, height_request=48)
        box.append(spinner)
        box.append(Gtk.Label(label="Startseite wird zusammengestellt …"))
        hint = Gtk.Label(label="Der Server sucht dafür in allen Bibliotheken; das dauert einen Moment.")
        hint.add_css_class("dim-label")
        box.append(hint)
        self.toolbar_view.set_content(box)
        spinner.start()
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            data = self.ctx.client.get("/api/home", timeout=_HOME_TIMEOUT) or {}
        except GoldfishAPIError as exc:
            GLib.idle_add(self._show_error, str(exc))
            return
        GLib.idle_add(self._apply, data)

    def _apply(self, data: dict) -> bool:
        sections = data.get("sections") or []
        show_continue = data.get("showContinue", True)
        show_next_up = data.get("showNextUp", True)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22, margin_top=16, margin_bottom=28)
        any_content = False

        for section in sections:
            library = section.get("library") or {}
            blocks: list[tuple[str, list[dict]]] = []
            if show_continue:
                blocks.append(("▶ Fortsetzen", section.get("continue") or []))
            if show_next_up:
                blocks.append(("📺 Als nächstes", section.get("nextUp") or []))
            blocks.append(("🆕 Zuletzt hinzugefügt", section.get("recent") or []))
            if not any(items for _, items in blocks):
                continue

            any_content = True
            heading = Gtk.Button(label=library.get("name") or "", has_frame=False, halign=Gtk.Align.START)
            heading.add_css_class("title-3")
            heading.set_margin_start(16)
            heading.connect("clicked", lambda _b, lib=library: self._open_library(lib))
            outer.append(heading)

            for title, items in blocks:
                if items:
                    outer.append(self._strip(title, items, library))

        if not any_content:
            return self._show_error(
                "Sobald etwas angesehen oder hinzugefügt wurde, erscheint es hier.",
                title="Noch nichts zu zeigen",
                icon="go-home-symbolic",
            )

        self.toolbar_view.set_content(Gtk.ScrolledWindow(vexpand=True, child=outer))
        return False

    def _strip(self, title: str, items: list[dict], library: dict) -> Gtk.Widget:
        """Ein waagerecht scrollbarer Streifen. Die Kacheln sind dieselben wie
        im Raster, damit Gesehen- und Favoritenschalter überall gleich
        funktionieren."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        label = Gtk.Label(label=title, xalign=0, margin_start=16)
        label.add_css_class("heading")
        box.append(label)

        # halign=START ist hier nötig, nicht bloß gefällig: ohne ihn verteilt
        # die Box ihren Restplatz zwischen den Kacheln, und bei einem kurzen
        # Streifen (zwei Titel unter "Fortsetzen") klaffte dadurch eine Lücke
        # von über hundert Pixeln zwischen ihnen, obwohl jede Kachel korrekt
        # 168 Pixel breit war.
        row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=14,
            margin_start=16,
            margin_end=16,
            halign=Gtk.Align.START,
        )
        kind = library.get("kind") or "movies"
        for item in items:
            card = CardWidget(
                self.ctx.client,
                kind,
                on_activate=lambda it: self._open_item(it, items),
                on_toggle_watched=lambda it, w: self._background(lambda: self.ctx.client.set_watched(it["id"], w)),
                on_toggle_favorite=lambda it, f: self._background(lambda: self.ctx.client.set_favorite(it["id"], f)),
            )
            card.bind(item)
            card.set_size_request(CARD_WIDTH, -1)
            row.append(card)

        scroller = Gtk.ScrolledWindow(
            child=row,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
        )
        box.append(scroller)
        return box

    def _open_item(self, item: dict, strip_items: list[dict]) -> None:
        from .detail_page import DetailPage

        self.nav_view.push(DetailPage(self.ctx, self.nav_view, item, queue=strip_items))

    def _open_library(self, library: dict) -> None:
        from .browse_page import BrowsePage

        if library.get("id"):
            self.nav_view.push(BrowsePage(self.ctx, self.nav_view, library))

    def _background(self, call) -> None:
        def worker() -> None:
            try:
                call()
            except GoldfishAPIError:
                pass  # Server bleibt die Wahrheit; beim nächsten Laden stimmt es

        threading.Thread(target=worker, daemon=True).start()

    def _show_error(self, message: str, title: str = "Fehler", icon: str = "dialog-error-symbolic") -> bool:
        self.toolbar_view.set_content(Adw.StatusPage(icon_name=icon, title=title, description=message))
        return False
