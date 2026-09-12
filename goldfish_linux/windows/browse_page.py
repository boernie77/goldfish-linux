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
        if not folders and not items:
            status = Adw.StatusPage(
                icon_name="folder-open-symbolic",
                title="Keine Inhalte",
                description="Dieser Ordner enthält nichts Sichtbares.",
            )
            status.set_vexpand(True)
            self.grid = None
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
        self.grid.set_content(folders, items)
        if self.toolbar_view.get_content() is not self.grid:
            self.toolbar_view.set_content(self.grid)

    # -- Kachel-Abzeichen ------------------------------------------------

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
        page = BrowsePage(
            self.ctx,
            self.nav_view,
            self.library,
            folder=folder["name"],
            drilldown=bool(folder.get("drilldown")),
        )
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
        """Bildet die drei Navigationsfälle des Browsers nach (grid.js).

        1. **Bibliothekswurzel** (`folder == ""`): Ordnerkacheln plus die Items,
           die direkt in der Wurzel liegen. Dafür MUSS `folder="/"` gesendet
           werden — ein leeres `folder` heißt serverseitig "kein Filter" und
           liefert alle Items der Bibliothek rekursiv, also die Dateien aus
           jedem Unterordner vermischt mit denen der Wurzel.
        2. **Unterordner mit Drilldown** (`folder_nav.drilldown`): seine direkten
           Unterordner plus die Dateien, die unmittelbar darin liegen. Der Server
           kennt kein "nur direkte Kinder", liefert also rekursiv — deshalb wird
           hier auf echte direkte Kinder nachgefiltert. Ohne diesen Filter
           erschienen dieselben Dateien doppelt: einmal als Ordnerkachel, einmal
           als Video. Genau dieser Fehler ist in den Apple-Apps schon einmal
           aufgetreten (CLAUDE.md, "Serien-Ordner zeigte Ordner-Kacheln UND
           rekursiv alle Folgen gleichzeitig") und beim ersten Rastertest hier
           wieder aufgeschlagen: eine Serie zeigte 31 Release-Ordner mit je
           "1 Titel" neben ihren 75 Folgen.
        3. **Normaler Unterordner** (der Regelfall): KEINE Ordnerkacheln,
           sondern alle Dateien darunter rekursiv und flach.

        Bei einer Suche gibt es nie Ordnerkacheln; der Ordner bleibt als Scope
        gesetzt, in der Wurzel sucht sie damit über die ganze Bibliothek.
        """
        client = self.ctx.client
        lib_id = self.library["id"]
        try:
            if search:
                folders = []
                items = client.items(lib_id, folder=self.folder, search=search, sort="title")
            elif not self.folder:
                folders = client.folders(lib_id)
                items = client.items(lib_id, folder="/", sort="title")
            elif self.drilldown:
                folders = client.folders(lib_id, parent=self.folder)
                prefix = self.folder + "/"
                items = [
                    it
                    for it in client.items(lib_id, folder=self.folder, sort="title")
                    if (it.get("relPath") or "").startswith(prefix)
                    and "/" not in (it["relPath"][len(prefix) :])
                ]
            else:
                folders = []
                items = client.items(lib_id, folder=self.folder, sort="title")
        except GoldfishAPIError as exc:
            GLib.idle_add(self._show_error, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — ein Hintergrund-Thread darf
            # nie stumm sterben, sonst bleibt die Seite für immer auf dem
            # Lade-Spinner hängen ("keine Inhalte", ohne jede Fehlermeldung).
            GLib.idle_add(self._show_error, f"Unerwarteter Fehler: {exc}")
            return
        GLib.idle_add(self._show_results, folders, items)
