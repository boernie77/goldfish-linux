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

    def _show_results(self, folders: list[dict], items: list[dict], stats: dict | None = None) -> None:
        if stats:
            self.stats = stats
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
            self.count_label = None
            self.alpha = None
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
            self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            self.content_box.append(self.count_label)
            self.content_box.append(grid_row)
        self._update_count()
        if self.toolbar_view.get_content() is not self.content_box:
            self.toolbar_view.set_content(self.content_box)

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
           in der Wurzel sucht sie damit über die ganze Bibliothek. Der Server
           durchsucht dabei auch Besetzungsnamen, Künstler und Album — dafür ist
           hier nichts zu tun.
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
        try:
            if search:
                folders = []
                items = client.items(lib_id, folder=self.folder, search=search, **common)
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
        GLib.idle_add(self._show_results, folders, items, stats)


def _folder_label(folder: dict) -> str:
    metadata = folder.get("metadata") or {}
    return metadata.get("title") or (folder.get("name") or "").rsplit("/", 1)[-1]


def _item_label(item: dict) -> str:
    metadata = item.get("metadata") or {}
    return metadata.get("title") or item.get("title") or ""
