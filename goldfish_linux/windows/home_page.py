"""Startseite: zwei bibliotheksübergreifende Streifen, dann "Neu" je Bibliothek.

Aufbau wie im Browser (`views.js renderHomeView`, Layout seit 2026-05-10):
**ein** Streifen "▶ Fortsetzen" und **ein** Streifen "📺 Als nächstes" ganz
oben, jeweils mit den Titeln ALLER gewählten Bibliotheken zusammen — danach
pro Bibliothek "🆕 Zuletzt hinzugefügt". Der Server liefert beides pro
Bibliothek getrennt (`sections`), das Zusammenführen und Sortieren ist Aufgabe
des Clients: Fortsetzen nach dem letzten Abspielen, Als nächstes nach dem
Hinzufügen, je die 24 neuesten.

**Bis 0.1.17 stand hier ein Fortsetzen-Streifen PRO Bibliothek** — dadurch
musste man drei Überschriften weit scrollen, um zu sehen, was man überall
angefangen hatte.

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

# Obergrenze je übergreifendem Streifen, wie im Browser: darüber wird die
# Reihe unübersichtlich lang, und jede Kachel will ein Poster laden.
_STRIP_CAP = 24


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

        # Bibliotheksübergreifend zusammenführen. Sortiert wird nach dem
        # letzten Abspielen bzw. dem Hinzufügen; `lastPlayedAt` liefert diese
        # Abfrage nicht immer mit, dann tritt `addedAt` an seine Stelle
        # (gleiche Ersatzregel wie im Browser).
        all_continue: list[dict] = []
        all_next_up: list[dict] = []
        for section in sections:
            all_continue.extend(section.get("continue") or [])
            all_next_up.extend(section.get("nextUp") or [])

        def stamp(item: dict, preferred: str) -> str:
            return item.get(preferred) or item.get("addedAt") or ""

        all_continue.sort(key=lambda it: stamp(it, "lastPlayedAt"), reverse=True)
        all_next_up.sort(key=lambda it: stamp(it, "addedAt"), reverse=True)

        if show_continue and all_continue:
            any_content = True
            outer.append(self._strip("▶ Fortsetzen", all_continue[:_STRIP_CAP]))
        if show_next_up and all_next_up:
            any_content = True
            outer.append(self._strip("📺 Als nächstes", all_next_up[:_STRIP_CAP]))

        # Danach je Bibliothek nur noch das Neue.
        for section in sections:
            recent = section.get("recent") or []
            if not recent:
                continue
            any_content = True
            library = section.get("library") or {}
            heading = Gtk.Button(label=library.get("name") or "", has_frame=False, halign=Gtk.Align.START)
            heading.add_css_class("title-3")
            heading.set_margin_start(16)
            heading.connect("clicked", lambda _b, lib=library: self._open_library(lib))
            outer.append(heading)
            outer.append(self._strip("🆕 Zuletzt hinzugefügt", recent, library.get("kind")))

        if not any_content:
            return self._show_error(
                "Sobald etwas angesehen oder hinzugefügt wurde, erscheint es hier.",
                title="Noch nichts zu zeigen",
                icon="go-home-symbolic",
            )

        self.toolbar_view.set_content(Gtk.ScrolledWindow(vexpand=True, child=outer))
        return False

    def _strip(self, title: str, items: list[dict], kind: str | None = None) -> Gtk.Widget:
        """Ein waagerecht scrollbarer Streifen. Die Kacheln sind dieselben wie
        im Raster, damit Gesehen- und Favoritenschalter überall gleich
        funktionieren.

        `kind` gibt die Bibliotheksart vor (Seitenverhältnis der Kachel). In
        den übergreifenden Streifen steht sie nicht fest: dort liegen Filme,
        Folgen und Privatvideos nebeneinander, jede Kachel bestimmt ihre Art
        deshalb selbst über `libraryId`."""
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
        # Die Leiste steht VOR den Kacheln, weil jede Kachel sie mitbekommt:
        # so lädt ein Streifen nur die Poster, die man tatsächlich sieht
        # (ein "Zuletzt hinzugefügt" bringt zwanzig Titel mit, sichtbar sind
        # sechs). Selbst danach suchen darf die Kachel nicht — siehe Kopf von
        # widgets/poster.py.
        scroller = Gtk.ScrolledWindow(
            child=row,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
        )
        for item in items:
            card_kind = kind or self.ctx.library_kind(item.get("libraryId")) or "movies"
            card = CardWidget(
                self.ctx.client,
                card_kind,
                on_activate=lambda it: self._open_item(it, items),
                on_toggle_watched=lambda it, w: self._background(lambda: self.ctx.client.set_watched(it["id"], w)),
                on_toggle_favorite=lambda it, f: self._background(lambda: self.ctx.client.set_favorite(it["id"], f)),
                scroller=scroller,
            )
            card.bind(item)
            card.set_size_request(CARD_WIDTH, -1)
            row.append(card)

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
