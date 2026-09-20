"""Ordner-/Item-Browser für eine Bibliothek.

Pro Ebene werden Unterordner (`GET /folders?parent=`) UND die darin liegenden
Items (`GET /items?folder=`) geladen und gemeinsam als Kachelraster gezeigt
(`widgets.grid.CardGrid`, Ordner zuerst). Klick auf einen Ordner navigiert eine
Ebene tiefer (neue BrowsePage auf dem Adw.NavigationView), Klick auf eine
Videokachel öffnet die Detailseite, und die Abzeichen für Gesehen und Favorit
auf der Kachel schalten direkt um.

Noch nicht hier abgebildet: Staffelansicht für Serien sowie die Filter- und
Sortierleiste — beides kommt in den folgenden Etappen.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..api import GoldfishAPIError  # noqa: E402
from ..formatting import format_count  # noqa: E402
from ..widgets.alpha_sidebar import AlphaSidebar, first_letter  # noqa: E402
from ..widgets.filterbar import FilterBar, FilterState  # noqa: E402
from ..variants import group_variants  # noqa: E402
from ..widgets.grid import CardGrid  # noqa: E402
from ..widgets.people_row import PeopleSearchRow  # noqa: E402
from .detail_page import DetailPage  # noqa: E402


class BrowsePage(Adw.NavigationPage):
    def __init__(
        self,
        ctx,
        nav_view: Adw.NavigationView,
        library: dict,
        folder: str = "",
        title: str | None = None,
        drilldown: bool = False,
    ):
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
        self.drilldown = drilldown
        self.toolbar_view = toolbar_view
        self.grid: CardGrid | None = None
        self.ctx.add_item_state_listener(self._on_item_state_changed)
        self.content_box: Gtk.Box | None = None
        self.people_container: Gtk.Box | None = None
        self.people_row: Gtk.Widget | None = None
        self.alpha: AlphaSidebar | None = None
        self.all_folders: list[dict] = []
        self.all_items: list[dict] = []
        self.shown_items: list[dict] = []
        # Zahlen der ganzen Bibliothek (aus /stats), für die Zeile über dem
        # Raster. Nur in der Wurzel gefüllt — in einem Ordner zählt, was
        # geladen wurde.
        self.stats: dict = {}
        self.count_label: Gtk.Label | None = None
        self.search_entry = search_entry
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_text = ""
        # Fuzzy-Zusatztreffer (FTS5-Präfixsuche, `searchMode=fuzzy`): der
        # Server meldet ihre Anzahl über den `X-Fuzzy-Extra-Count`-Header zum
        # normalen (Ganze-Wörter-)Suchergebnis dazu. Erst auf Klick nachladen
        # statt immer beide Modi anzufragen — die meisten Suchen brauchen sie
        # nicht.
        self.fuzzy_button: Gtk.Button | None = None
        self._fuzzy_extra_count = 0
        # Ein Film liegt fast immer in seinem eigenen Release-Ordner —
        # Ordnerkacheln wären dort sinnlos (siehe `_load_worker`).
        self._always_flat = (library.get("kind") or "") == "movies"
        # Zähler gegen überholte Antworten: tippt man schnell, kommen die
        # Antworten nicht zwangsläufig in der Reihenfolge zurück, in der sie
        # gestellt wurden. Nur die jeweils neueste darf ins Raster.
        self._load_seq = 0
        self._search_timeout = 0

        self.view_prefs = ctx.view_prefs
        self.filters = FilterState()
        remembered = self.view_prefs.get_sort(library["id"], folder)
        if remembered is not None:
            self.filters.sort, self.filters.ascending = remembered
        elif (library.get("kind") or "") == "private" and folder:
            # **Privatvideos in einem Ordner: älteste zuerst.** Bei einem
            # YouTube-Kanal ist die Reihenfolge der Veröffentlichung die
            # natürliche (so hält es der Browser für `kind=private` ebenfalls,
            # siehe `restoreSortForContext`). In der Bibliothekswurzel bleibt
            # es bei Titel A–Z, sonst verschwänden dort die Kanal-Kacheln —
            # "Veröffentlicht" gehört zu den Sortierungen, die die
            # Ordnerstruktur übergehen.
            self.filters.sort, self.filters.ascending = "released", True
        self.filter_bar = FilterBar(
            library.get("kind") or "movies",
            self.filters,
            on_change=self._on_filters_changed,
            load_genres=self._load_genres,
        )
        header.pack_end(self.filter_bar)

        random_button = Gtk.Button(icon_name="media-playlist-shuffle-symbolic", tooltip_text="Zufällig abspielen")
        random_button.connect("clicked", lambda *_: self._play_random())
        header.pack_start(random_button)

        self._show_loading()
        self._load()

    # -- UI-Zustände ------------------------------------------------------

    def _show_loading(self) -> None:
        # Gtk.Spinner statt Adw.Spinner — letzteres braucht libadwaita ≥ 1.6,
        # neuer als das, was Debian 12/Ubuntu 22.04 mitbringen.
        spinner = Gtk.Spinner()
        spinner.set_size_request(48, 48)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        box.append(spinner)
        self.toolbar_view.set_content(box)
        # start() ERST nach dem Einhängen: ein Spinner, der sich zu drehen
        # beginnt, bevor er im Widget-Baum hängt, hat noch keine Frame-Clock
        # und löst beim Start der App ein
        # "gdk_frame_clock_get_frame_time: assertion 'GDK_IS_FRAME_CLOCK
        # (frame_clock)' failed" aus (in 0.1.7 auf der Konsole zu sehen).
        spinner.start()

    def _show_error(self, message: str) -> None:
        status = Adw.StatusPage(icon_name="dialog-error-symbolic", title="Fehler", description=message)
        self.toolbar_view.set_content(status)

    def _show_results(
        self,
        folders: list[dict],
        items: list[dict],
        stats: dict | None = None,
        fuzzy_extra_count: int = 0,
        people: list[dict] | None = None,
    ) -> None:
        if stats:
            self.stats = stats
        self._fuzzy_extra_count = fuzzy_extra_count
        people = people or []
        if not folders and not items:
            # Bei aktiven Filtern ist "leer" fast immer der Filter und nicht
            # der Ordner — sonst sucht man den Fehler an der falschen Stelle.
            active = self.filters.active_filter_count()
            if self.search_text:
                title, desc = "Keine Treffer", f"Für \u201e{self.search_text}\u201c wurde nichts gefunden."
            elif active:
                title, desc = (
                    "Nichts passt zum Filter",
                    f"{active} Filter sind aktiv. Über den Filterknopf zurücksetzen.",
                )
            else:
                title, desc = "Keine Inhalte", "Dieser Ordner enthält nichts Sichtbares."
            status = Adw.StatusPage(
                icon_name="folder-open-symbolic",
                title=title,
                description=desc,
            )
            status.set_vexpand(True)
            self.grid = None
            self.content_box = None
            self.people_container = None
            self.people_row = None
            self.count_label = None
            self.alpha = None
            self.fuzzy_button = None
            self.shown_items = []
            self.toolbar_view.set_content(status)
            return

        # Das Raster wird einmal gebaut und danach nur neu befüllt — so bleiben
        # beim Tippen im Suchfeld die Kachel-Widgets erhalten, statt bei jedem
        # Tastendruck komplett neu zu entstehen.
        if self.grid is None:
            self.grid = CardGrid(
                self.ctx.client,
                self.library.get("kind") or "movies",
                on_item=self._open_detail,
                on_folder=self._open_folder,
                on_toggle_watched=self._toggle_watched,
                on_toggle_favorite=self._toggle_favorite,
            )
        # Varianten bündeln: derselbe Film in mehreren Auflösungen ist mehrere
        # Items mit gleicher metadataId — im Raster darf davon nur EINE Kachel
        # stehen (Browser `groupVariants`, User-Report 2026-09-18). Das passiert
        # VOR allem anderen, damit Anzahl-Zeile, Buchstabenfilter und Raster
        # dieselbe Liste sehen.
        items = group_variants(items)
        self.shown_items = items
        self.all_folders = folders
        self.all_items = items
        self._apply_alpha()

        if self.content_box is None:
            # Raster und Buchstabenleiste nebeneinander, darüber die Zeile mit
            # der Anzahl. Die Leiste kommt nur, wenn sie in den Einstellungen
            # aktiv ist.
            grid_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0, vexpand=True)
            grid_row.append(self.grid)
            if self.ctx.view_prefs.alpha_sidebar():
                self.alpha = AlphaSidebar(on_select=lambda letter: self._apply_alpha())
                grid_row.append(self.alpha)
            self.count_label = Gtk.Label(xalign=0, margin_start=16, margin_top=8, margin_end=16)
            self.count_label.add_css_class("dim-label")
            self.count_label.add_css_class("caption")
            self.fuzzy_button = Gtk.Button(
                halign=Gtk.Align.CENTER, margin_top=8, margin_bottom=12, visible=False
            )
            self.fuzzy_button.connect("clicked", self._on_fuzzy_button_clicked)
            # Schauspieler-Reihe (aufgegliederte Trefferanzeige, Server 1.4.22)
            # steht als erstes Kind — sichtbar nur, wenn eine Suche welche
            # findet (browser cards.js: appendSearchResultCards).
            self.people_container = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                margin_start=16,
                margin_end=16,
                margin_top=12,
                visible=False,
            )
            self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            self.content_box.append(self.people_container)
            self.content_box.append(self.count_label)
            self.content_box.append(grid_row)
            self.content_box.append(self.fuzzy_button)
        self._update_people_row(people)
        self._update_count()
        self._update_fuzzy_button()
        if self.toolbar_view.get_content() is not self.content_box:
            self.toolbar_view.set_content(self.content_box)

    def _update_people_row(self, people: list[dict]) -> None:
        """Baut die Schauspieler-Reihe neu — nur bei aktiver Suche mit
        mindestens einem Treffer sichtbar (dieselbe Regel wie im Browser)."""
        if self.people_container is None:
            return
        if self.people_row is not None:
            self.people_container.remove(self.people_row)
            self.people_row = None
        if self.search_text and people:
            self.people_row = PeopleSearchRow(self.ctx.client, people, on_person=self._open_person)
            self.people_container.append(self.people_row)
            self.people_container.set_visible(True)
        else:
            self.people_container.set_visible(False)

    def _open_person(self, person: dict) -> None:
        """Klick auf eine Schauspieler-Karte der Suchergebnisse öffnet dieselbe
        Personen-Ansicht wie ein Klick auf ein Besetzungsportrait in der
        Detailseite (`DetailPage._open_person`, `SeasonsPage._open_person`)."""
        tmdb_id = person.get("tmdbId")
        if not tmdb_id:
            return
        from .person_page import PersonPage

        self.nav_view.push(PersonPage(self.ctx, self.nav_view, int(tmdb_id), person.get("name") or ""))

    def _update_count(self) -> None:
        """Die Zeile über dem Raster: wie viel hier drin ist.

        Drei Fälle, weil drei verschiedene Zahlen gemeint sind:

        * **Suche oder Filter aktiv** → wie viele Treffer. Eine Gesamtzahl
          daneben wäre irreführend, weil der Server nur die Treffer liefert.
        * **Bibliothekswurzel** → die Zahlen der ganzen Bibliothek aus
          `/stats`; bei Serien die Serien UND die Folgen (18.574 Folgen in 267
          Serien sind zwei Angaben, die beide interessieren), bei Musik die
          Titel und Alben.
        * **In einem Ordner** → was tatsächlich geladen wurde. Dafür braucht
          es keine Abfrage, die Liste liegt ja vor."""
        if self.count_label is None:
            return
        kind = self.library.get("kind") or ""
        noun = {"music": "Titel", "tv": "Folgen"}.get(kind, "Videos")
        shown_items, shown_folders = len(self.shown_items), len(self.grid_folder_count())

        letter = self.alpha.active if self.alpha is not None else None
        if letter:
            # Mit gewähltem Buchstaben zählt, was davon übrig ist — die
            # Gesamtzahl der Bibliothek wäre hier irreführend.
            parts = [f"{format_count(shown_items)} {noun} mit „{letter}“"]
            if shown_folders:
                parts.append(f"{format_count(shown_folders)} Ordner")
            self.count_label.set_text(" · ".join(parts))
        elif self.search_text or self.filters.active_filter_count():
            parts = [f"{format_count(shown_items)} Treffer"]
            if shown_folders:
                parts.append(f"{format_count(shown_folders)} Ordner")
            self.count_label.set_text(" · ".join(parts))
        elif not self.folder and self.stats:
            total = int(self.stats.get("totalItems") or 0)
            parts = []
            if kind == "tv":
                parts.append(f"{format_count(len(self.all_folders) or int(self.stats.get('folderCount') or 0))} Serien")
            elif kind == "music" and self.stats.get("albumCount"):
                parts.append(f"{format_count(int(self.stats['albumCount']))} Alben")
            parts.append(f"{format_count(total)} {noun}")
            self.count_label.set_text(" · ".join(parts))
        else:
            parts = []
            if shown_folders:
                parts.append(f"{format_count(shown_folders)} Ordner")
            parts.append(f"{format_count(shown_items)} {noun}")
            self.count_label.set_text(" · ".join(parts))

    def _update_fuzzy_button(self) -> None:
        """Zeigt/versteckt den "N weitere Treffer"-Knopf unter dem Raster.

        Nur bei aktiver Textsuche sinnvoll — Filter allein kennen keinen
        Fuzzy-Modus."""
        if self.fuzzy_button is None:
            return
        if self.search_text and self._fuzzy_extra_count > 0:
            noun = "weiterer Treffer" if self._fuzzy_extra_count == 1 else "weitere Treffer"
            self.fuzzy_button.set_label(f"{format_count(self._fuzzy_extra_count)} {noun} anzeigen")
            self.fuzzy_button.set_sensitive(True)
            self.fuzzy_button.set_visible(True)
        else:
            self.fuzzy_button.set_visible(False)

    def _on_fuzzy_button_clicked(self, button: Gtk.Button) -> None:
        """Lädt zusätzlich per `searchMode=fuzzy` (FTS5-Präfixsuche statt nur
        ganzer Wörter) und hängt die neuen Treffer ans bestehende Raster an,
        statt die Suche komplett neu zu laden — Scrollposition und bereits
        gebundene Kacheln bleiben so erhalten."""
        button.set_sensitive(False)
        search = self.search_text
        lib_id = self.library["id"]
        f = self.filters
        common = {
            "sort": f.sort,
            "sort_dir": f.sort_dir,
            "watched": f.watched,
            "favorite": "yes" if f.favorites_only else "",
            "buckets": sorted(f.buckets),
            "genres": sorted(f.genres),
        }
        # shown_items ist bereits gruppiert (group_variants) — Geschwister-
        # Varianten stecken in item["_variants"], nicht als eigene Einträge
        # in shown_items. Ohne die Variants mit einzubeziehen, rutschen
        # Geschwister-IDs eines bereits gezeigten Mehrfach-Varianten-Treffers
        # am Dedup-Filter vorbei und erzeugen eine zweite Kachel desselben
        # Films (QM-Review FTS5-Fuzzy-Suche, 2026-09-19).
        known_ids = {
            v.get("id")
            for it in self.shown_items
            for v in (it.get("_variants") or [it])
        }

        def worker() -> None:
            try:
                fuzzy_items, _ = self.ctx.client.get_items_with_fuzzy_count(
                    lib_id, folder=self.folder, search=search, search_mode="fuzzy", **common
                )
            except GoldfishAPIError as exc:
                GLib.idle_add(self._toast, f"Weitere Treffer konnten nicht geladen werden: {exc}")
                GLib.idle_add(button.set_sensitive, True)
                return
            new_items = [it for it in fuzzy_items if it.get("id") not in known_ids]
            new_items = group_variants(new_items)
            GLib.idle_add(self._apply_fuzzy_extra, new_items)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_fuzzy_extra(self, new_items: list[dict]) -> bool:
        self.all_items = self.all_items + new_items
        self.shown_items = self.shown_items + new_items
        if self.grid is not None:
            self.grid.append_items(new_items)
        self._fuzzy_extra_count = 0
        self._update_count()
        self._update_fuzzy_button()
        return False

    def grid_folder_count(self) -> list[dict]:
        """Die aktuell gezeigten Ordnerkacheln (nach Buchstabenfilter)."""
        letter = self.alpha.active if self.alpha is not None else None
        if letter is None:
            return self.all_folders
        return [f for f in self.all_folders if first_letter(_folder_label(f)) == letter]

    def _apply_alpha(self) -> None:
        """Filtert auf den gewählten Anfangsbuchstaben.

        Clientseitig, weil der Server dafür keinen Filter kennt — und weil die
        Liste ohnehin schon geladen ist. Ordner und Videos werden gleich
        behandelt, sonst verschwände beim Filtern die halbe Ansicht."""
        letter = self.alpha.active if self.alpha is not None else None
        if letter is None:
            self.grid.set_content(self.all_folders, self.all_items)
            self.shown_items = self.all_items
            self._update_count()
            return
        folders = [f for f in self.all_folders if first_letter(_folder_label(f)) == letter]
        items = [i for i in self.all_items if first_letter(_item_label(i)) == letter]
        self.shown_items = items
        self.grid.set_content(folders, items)
        self._update_count()

    # -- Kachel-Abzeichen ------------------------------------------------

    def _on_item_state_changed(self, item_id: int, watched=None, favorite=None) -> None:
        """Zustand wurde woanders umgeschaltet (Info-Karte) — Kachel nachziehen.

        Ohne das blieb der grüne Haken auf der Kachel stehen, obwohl er in der
        Info-Karte gerade entfernt wurde (User-Report 2026-09-18)."""
        if self.grid is not None:
            self.grid.apply_item_state(item_id, watched=watched, favorite=favorite)

    def _toggle_watched(self, item: dict, watched: bool) -> None:
        """Die Kachel hat ihren Zustand schon selbst umgeschaltet; hier wird er
        nur serverseitig nachgezogen. Ein Fehler kommt als Hinweis, ohne die
        Ansicht neu zu laden."""
        threading.Thread(
            target=self._state_worker,
            args=(lambda: self.ctx.client.set_watched(item["id"], watched), "Gesehen-Status"),
            daemon=True,
        ).start()

    def _toggle_favorite(self, item: dict, favorite: bool) -> None:
        threading.Thread(
            target=self._state_worker,
            args=(lambda: self.ctx.client.set_favorite(item["id"], favorite), "Favorit"),
            daemon=True,
        ).start()

    def _state_worker(self, call, label: str) -> None:
        try:
            call()
        except GoldfishAPIError as exc:
            GLib.idle_add(self._toast, f"{label} konnte nicht gespeichert werden: {exc}")

    def _toast(self, message: str) -> bool:
        window = self.get_root()
        if hasattr(window, "show_toast"):
            window.show_toast(message)
        return False

    # -- Aktionen -----------------------------------------------------

    def _open_folder(self, folder: dict) -> None:
        """Serienordner öffnen die Staffelansicht, alles andere die
        Ordneransicht.

        Nur auf der obersten Ebene einer Serien-Bibliothek: dort ist ein Ordner
        eine Serie. Tiefer liegende Ordner sind Staffel- oder Release-Ordner und
        haben keine eigene Staffelstruktur. Hat sich die Staffelansicht für
        diesen Ordner schon einmal als Sackgasse erwiesen (kein
        TMDB-Staffelgerüst), bleibt es bei der Ordneransicht."""
        name = folder["name"]
        if (
            (self.library.get("kind") == "tv")
            and not self.folder
            and self.ctx.view_prefs.season_view(self.library["id"], name)
        ):
            from .seasons_page import SeasonsPage

            self.nav_view.push(SeasonsPage(self.ctx, self.nav_view, self.library, name))
            return

        self.nav_view.push(
            BrowsePage(
                self.ctx,
                self.nav_view,
                self.library,
                folder=name,
                drilldown=bool(folder.get("drilldown")),
            )
        )

    def _open_detail(self, item: dict) -> None:
        # Die gerade gezeigte Liste als Warteschlange mitgeben, damit am Ende
        # eines Titels von selbst der nächste läuft.
        self.nav_view.push(DetailPage(self.ctx, self.nav_view, item, queue=self.shown_items))

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        """Nicht bei jedem Tastendruck laden.

        "Matrix" wären sonst sechs Abfragen hintereinander, jede über die ganze
        Bibliothek inklusive Besetzungsnamen — der Grund, warum sich die Suche
        zäh anfühlte. 300 ms nach dem letzten Zeichen genügen; ein bereits
        wartender Lauf wird verworfen."""
        self.search_text = entry.get_text().strip()
        if self._search_timeout:
            GLib.source_remove(self._search_timeout)
        self._search_timeout = GLib.timeout_add(300, self._run_search)

    def _run_search(self) -> bool:
        self._search_timeout = 0
        self._load(search=self.search_text)
        return False  # einmalig

    def _on_filters_changed(self) -> None:
        """Eine Sortierung, die die Ordnerstruktur übergeht, wird nur dort
        gemerkt, wo ohnehin keine Ordnerkacheln stehen — siehe
        `config.ViewPrefs`. Im Wurzel- oder Drilldown-Fall würde sie die
        Kacheln beim nächsten Öffnen dauerhaft verstecken."""
        # In einer Film-Bibliothek stehen nie Ordnerkacheln, also kann eine
        # flache Sortierung dort auch nichts verstecken.
        shows_folder_tiles = (not self.folder or self.drilldown) and not self._always_flat
        if self.filters.is_flat and shows_folder_tiles:
            self.view_prefs.clear_sort(self.library["id"], self.folder)
        else:
            self.view_prefs.set_sort(
                self.library["id"], self.folder, self.filters.sort, self.filters.ascending
            )
        self._load(search=self.search_text)

    def _play_random(self) -> None:
        """Zufälliges Video aus dem aktuellen Bereich — und zwar **sofort
        abspielen**, nicht bloß die Detailseite öffnen.

        Der Bereich folgt derselben Regel wie im Browser: im Ordner nur dessen
        Inhalt, in der Wurzel die ganze Bibliothek. Die aktiven Filter gelten
        mit, damit "zufällig" zu dem passt, was man gerade sieht.

        Im Player geht es danach mit ⏭ zum nächsten Zufallsvideo weiter und
        mit ⏮ zurück zum vorherigen; dafür bekommt er die Ziehfunktion selbst
        in die Hand (`random_fetch`), samt der Filter von jetzt."""
        f = self.filters

        def draw() -> dict:
            return self.ctx.client.random_item(
                library_id=self.library["id"],
                folder=self.folder,
                search=self.search_text,
            )

        def worker() -> None:
            try:
                item = draw()
            except GoldfishAPIError as exc:
                GLib.idle_add(self._toast, f"Kein Zufallstreffer: {exc}")
                return
            if not item:
                GLib.idle_add(self._toast, "Kein passendes Video gefunden.")
                return
            GLib.idle_add(self._open_random, item, draw)

        threading.Thread(target=worker, daemon=True).start()

    def _open_random(self, item: dict, draw) -> bool:
        # Musik gehört in die Abspielleiste, nicht ins Videofenster — dieselbe
        # Regel wie überall sonst in dieser App.
        if (self.library.get("kind") or "") == "music":
            self.ctx.music.play_queue([item], 0)
            return False
        from .player_window import open_player

        open_player(self.ctx, item, random_fetch=draw)
        return False

    def _load_genres(self) -> list[str]:
        """Wird vom Filtermenü beim ersten Öffnen aufgerufen. Läuft bewusst
        synchron: der Aufruf steckt im Öffnen des Menüs, und die Liste kommt in
        Bruchteilen einer Sekunde."""
        try:
            return self.ctx.client.genres(self.library["id"])
        except GoldfishAPIError:
            return []

    # -- Laden --------------------------------------------------------

    def _load(self, search: str = "") -> None:
        # Ein neuer Ladevorgang (Suche, Filter) hebt den Buchstabenfilter auf —
        # sonst bliebe die neue Liste unerklärlich leer.
        if self.alpha is not None:
            self.alpha.reset()
        self._load_seq += 1
        # **Der Ladekreis tritt bei JEDEM Laden an die Stelle des Rasters —
        # bewusst.** Das Raster stattdessen stehen zu lassen, bis die neuen
        # Kacheln da sind, sähe ruhiger aus, lässt GTK aber über die eigenen
        # Kacheln stolpern: ein Modellwechsel in einem SICHTBAREN Raster, das
        # dabei stark schrumpft (3170 Kacheln auf einen Suchtreffer), erzeugt
        # reproduzierbar acht Meldungen der Art `gtk_widget_measure: assertion
        # 'GTK_IS_WIDGET (widget)' failed` samt `set_child_visible` und
        # `allocate` — geprüft mit splice() UND mit komplett neuem Modell, mit
        # und ohne Poster-Laden. Hängt das Raster während des Wechsels dagegen
        # nicht im Fenster, bleibt es still (je drei Läufe gegengeprüft).
        # Dass die Suche jetzt entprellt ist, nimmt dem Ladekreis ohnehin den
        # Schrecken: er erscheint einmal statt bei jedem Tastendruck.
        self._show_loading()
        threading.Thread(target=self._load_worker, args=(search, self._load_seq), daemon=True).start()

    def _load_worker(self, search: str, seq: int = 0) -> None:
        """Bildet die Navigationsfälle des Browsers nach (grid.js).

        0. **Film-Bibliotheken sind immer flach** (`_always_flat`). Ein Film
           liegt üblicherweise in seinem eigenen Release-Ordner: die
           Filme-Bibliothek dieses Servers bringt 2808 solcher Ordner mit,
           jeder mit genau einer Datei und ohne eigene TMDB-Zuordnung. Als
           Ordnerkacheln wären das 2808 Kacheln mit Release-Dateinamen und
           ohne Poster — genau das war der Grund, warum in "Filme" weder
           Filmcover noch richtige Titel erschienen und die Ansicht sich zäh
           anfühlte. Der Browser macht es ebenso (`grid.js`: `flatView =
           state.flatView || lib.kind === "movies" || isShuffle`).
        1. **Suche**: nie Ordnerkacheln. Der Ordner bleibt als Bereich gesetzt,
           in der Wurzel sucht sie damit über die ganze Bibliothek. Seit
           Server 1.4.22 durchsucht `/api/items?search=` NUR NOCH den Titel
           (FTS5-Wortmatch) — Besetzungstreffer kommen NICHT mehr aus diesem
           Aufruf, sondern separat aus `GET /api/search/people` (siehe unten,
           `_load_people`) und stehen als eigene Reihe über dem Raster
           (aufgegliederte Trefferanzeige, wie im Browser `cards.js`
           `appendSearchResultCards`).
        2. **Flache Sortierung** (`FilterState.is_flat`, z. B. Hinzugefügt oder
           Laufzeit) ODER **nur Favoriten**: ebenfalls ohne Ordnerkacheln, die
           Struktur wird bewusst übergangen. In der Wurzel geht der
           Ordner-Parameter GAR NICHT mit, damit die Liste die ganze Bibliothek
           umfasst; in einem Unterordner bleibt sie auf dessen Inhalt
           beschränkt ("nur nach unten flach"). Dass "nur Favoriten" hier
           dazugehört, ist keine Willkür: der Browser hat dafür einen eigenen
           flachen Zweig (`renderFavoritesFlatBranch`). Ohne ihn blieben in der
           Wurzel alle 2808 Ordnerkacheln neben einer Handvoll Favoriten stehen
           — im ersten Test genau so gesehen.
        3. **Bibliothekswurzel**: Ordnerkacheln plus die Items, die direkt in
           der Wurzel liegen. Dafür MUSS `folder="/"` gesendet werden — ein
           leeres `folder` heißt serverseitig "kein Filter" und liefert alle
           Items rekursiv, also die Dateien jedes Unterordners vermischt mit
           denen der Wurzel.
        4. **Unterordner mit Drilldown** (`folder_nav.drilldown`): seine direkten
           Unterordner plus die Dateien unmittelbar darin. Der Server kennt kein
           "nur direkte Kinder", liefert also rekursiv — deshalb wird hier
           nachgefiltert. Ohne diesen Filter erschienen dieselben Dateien
           doppelt, einmal als Ordnerkachel und einmal als Video (derselbe
           Fehler wie in den Apple-Apps, siehe CLAUDE.md).
        5. **Normaler Unterordner** (der Regelfall): keine Ordnerkacheln,
           sondern alle Dateien darunter rekursiv und flach.
        """
        client = self.ctx.client
        lib_id = self.library["id"]
        f = self.filters
        # Die Filter gelten in jedem Fall gleichermaßen.
        common = {
            "sort": f.sort,
            "sort_dir": f.sort_dir,
            "watched": f.watched,
            "favorite": "yes" if f.favorites_only else "",
            "buckets": sorted(f.buckets),
            "genres": sorted(f.genres),
        }
        stats: dict = {}
        if not self.folder:
            # Nur in der Wurzel: dort kennt niemand die Gesamtzahl, ohne zu
            # fragen. Reine Zähl-Abfrage, kein Item-Laden.
            try:
                stats = client.library_stats(lib_id)
            except Exception:  # noqa: BLE001 — eine fehlende Zahl ist kein Grund,
                # die ganze Ansicht scheitern zu lassen
                stats = {}
        fuzzy_extra_count = 0
        try:
            if search:
                folders = []
                items, fuzzy_extra_count = client.get_items_with_fuzzy_count(
                    lib_id, folder=self.folder, search=search, **common
                )
            elif f.is_flat or f.favorites_only or self._always_flat:
                folders = []
                items = client.items(lib_id, folder=self.folder, **common)
            elif not self.folder:
                folders = client.folders(lib_id)
                items = client.items(lib_id, folder="/", **common)
            elif self.drilldown:
                folders = client.folders(lib_id, parent=self.folder)
                prefix = self.folder + "/"
                items = [
                    it
                    for it in client.items(lib_id, folder=self.folder, **common)
                    if (it.get("relPath") or "").startswith(prefix)
                    and "/" not in it["relPath"][len(prefix) :]
                ]
            else:
                folders = []
                items = client.items(lib_id, folder=self.folder, **common)
        except GoldfishAPIError as exc:
            if seq == self._load_seq:
                GLib.idle_add(self._show_error, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — ein Hintergrund-Thread darf
            # nie stumm sterben, sonst bleibt die Seite für immer auf dem
            # Lade-Spinner hängen ("keine Inhalte", ohne jede Fehlermeldung).
            if seq == self._load_seq:
                GLib.idle_add(self._show_error, f"Unerwarteter Fehler: {exc}")
            return
        if seq != self._load_seq:
            return  # ein neuerer Lauf ist unterwegs
        people: list[dict] = []
        if search:
            # Eigener Aufruf statt Teil des try/except oben: ein Fehler bei
            # der Personensuche darf die eigentliche Trefferliste nicht mit
            # zu Fall bringen — die Reihe bleibt dann einfach leer.
            try:
                people = client.search_people(search, library_id=lib_id, folder=self.folder)
            except GoldfishAPIError:
                people = []
        if seq != self._load_seq:
            return
        GLib.idle_add(self._show_results, folders, items, stats, fuzzy_extra_count, people)


def _folder_label(folder: dict) -> str:
    metadata = folder.get("metadata") or {}
    return metadata.get("title") or (folder.get("name") or "").rsplit("/", 1)[-1]


def _item_label(item: dict) -> str:
    metadata = item.get("metadata") or {}
    return metadata.get("title") or item.get("title") or ""
