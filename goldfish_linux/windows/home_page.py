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

        # Für den Serien-/Kanalname-Link (`_open_folder_from_item`): "Fortsetzen"/
        # "Als nächstes" mischen Items ALLER Bibliotheken flach, jedes Item
        # braucht trotzdem seine eigene, richtige `library`.
        self._library_by_id: dict[int, dict] = {
            lib["id"]: lib
            for section in sections
            if (lib := section.get("library")) and lib.get("id")
        }

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
        # **Der Streifen liegt in einer Hülle mit Füllstück.** Der Viewport der
        # Bildlaufleiste dehnt sein Kind auf die Fensterbreite — das `halign`
        # der Reihe hilft dagegen nicht, und die Box verteilt den Überschuss
        # zwischen ihre Kacheln (im breiten Fenster gemessen: 323 statt 14
        # Pixel Abstand, genau die Lücken aus dem Bildschirmfoto). Ein
        # ausdrücklich dehnbares Kind DANEBEN schluckt den Rest, die Reihe
        # selbst behält ihre natürliche Breite.
        holder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.set_hexpand(False)
        holder.append(row)
        holder.append(Gtk.Box(hexpand=True))
        scroller = Gtk.ScrolledWindow(
            child=holder,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
        )
        for item in items:
            # NICHT `self.ctx.library_kind(...)` — der wird von `MainWindow`
            # in einem eigenen Hintergrund-Ladevorgang befüllt, der beim
            # allerersten Öffnen der Startseite (sie erscheint sofort beim
            # Start, noch vor jenem Ladevorgang) regelmäßig noch leer ist.
            # `self._library_by_id` kommt dagegen direkt aus DIESER
            # Home-Antwort (siehe `_apply` oben) und ist deshalb nie leer,
            # wenn hier überhaupt schon Items zum Anzeigen da sind. Bug:
            # ohne diesen Fix blieb die Serien-/Kanalname-Zeile in
            # "Fortsetzen"/"Als nächstes" (den einzigen zwei Streifen ohne
            # explizit übergebenes `kind`) dauerhaft leer, weil `card_kind`
            # so gut wie immer auf den Fallback "movies" zurückfiel.
            item_library = self._library_by_id.get(item.get("libraryId")) or {}
            card_kind = kind or item_library.get("kind") or "movies"
            card = CardWidget(
                self.ctx.client,
                card_kind,
                on_activate=lambda it: self._open_item(it, items),
                on_toggle_watched=lambda it, w: self._background(lambda: self.ctx.client.set_watched(it["id"], w)),
                on_toggle_favorite=lambda it, f: self._background(lambda: self.ctx.client.set_favorite(it["id"], f)),
                scroller=scroller,
                # Alle Kacheln eines Streifens gleich groß, auch die von
                # YouTube: in einer Reihe mit Filmpostern sähen 16:9-Kacheln
                # wie ein Fehler aus. Die Form ist immer die des Posters.
                aspect_kind="movies",
                # User-Wunsch 2026-09-13: Serien-/Kanalname klickbar → zur
                # Serien-/Kanalübersicht statt zum einzelnen Item.
                on_open_folder=self._open_folder_from_item,
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

    def _open_folder_from_item(self, item: dict) -> None:
        """Serien-/Kanalname-Klick auf einer Home-Kachel (User-Wunsch
        2026-09-13): springt zur Serien- bzw. Kanalübersicht statt zum
        einzelnen Item. Der oberste Ordner von `relPath` ist bei Serien immer
        die Serie und bei Privatvideos der Kanal (`CardWidget.bind()` zeigt
        die Zeile nur genau dann an)."""
        library = self._library_by_id.get(item.get("libraryId"))
        rel = item.get("relPath") or ""
        top_folder = rel.split("/", 1)[0] if "/" in rel else ""
        if not library or not top_folder:
            return

        # Serien-Bibliothek: derselbe Zweig wie `BrowsePage._open_folder` beim
        # normalen Browsing von der Wurzel aus (dort mit `not self.folder`
        # abgesichert — hier immer erfüllt, Home kennt keine "aktuelle
        # Ordner-Ebene"). Kein Root-Fetch nötig, der Top-Ordner IST die Serie.
        if library.get("kind") == "tv" and self.ctx.view_prefs.season_view(library["id"], top_folder):
            from .seasons_page import SeasonsPage

            self.nav_view.push(SeasonsPage(self.ctx, self.nav_view, library, top_folder))
            return

        # Privatvideos (oder eine Serie ohne Staffelstruktur): ob der Kanal-
        # Ordner selbst wieder Unterordner zeigt (drilldown) oder eine flache
        # Videoliste, weiß nur der Server — anders als beim normalen Browsing
        # (wo die Geschwister-Ordner-Kacheln der aktuellen Ebene schon geladen
        # sind) kennt die Startseite das nicht, ein einmaliger Root-Fetch holt
        # es nach.
        threading.Thread(
            target=self._resolve_folder_worker, args=(library, top_folder), daemon=True
        ).start()

    def _resolve_folder_worker(self, library: dict, folder: str) -> None:
        drilldown = False
        try:
            tiles = self.ctx.client.folders(library["id"])
            tile = next((t for t in tiles if t.get("name") == folder), None)
            if tile:
                drilldown = bool(tile.get("drilldown"))
        except GoldfishAPIError:
            pass  # Fällt auf die flache Ordneransicht zurück (drilldown=False)
        GLib.idle_add(self._push_folder, library, folder, drilldown)

    def _push_folder(self, library: dict, folder: str, drilldown: bool) -> bool:
        from .browse_page import BrowsePage

        self.nav_view.push(
            BrowsePage(self.ctx, self.nav_view, library, folder=folder, drilldown=drilldown)
        )
        return False

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
