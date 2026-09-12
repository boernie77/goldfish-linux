"""Hauptfenster: Bibliotheks-Sidebar (Adw.NavigationSplitView) + Content-
Navigationsstapel (Adw.NavigationView, verwaltet Zurück-Navigation
automatisch)."""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import __version__  # noqa: E402
from ..api import GoldfishAPIError, GoldfishClient  # noqa: E402
from ..config import ViewPrefs  # noqa: E402
from ..local_library import LocalLibraryManager  # noqa: E402
from ..music_player import MusicPlayer  # noqa: E402
from ..widgets.mini_player import MiniPlayer  # noqa: E402
from .browse_page import BrowsePage  # noqa: E402
from .collections_page import CollectionsPage  # noqa: E402
from .downloads_page import DownloadsPage  # noqa: E402
from .home_page import HomePage  # noqa: E402
from .local_page import LocalLibrariesPage  # noqa: E402
from .music_page import MusicLibraryPage  # noqa: E402
from .playlists_page import PlaylistsPage  # noqa: E402
from .settings_page import SettingsPage  # noqa: E402

_KIND_ICON = {"movies": "🎬", "tv": "📺", "music": "🎵", "private": "📁"}


class AppContext:
    """Gemeinsam genutzte Referenzen (Client/Downloads/Fenster), damit jede
    NavigationPage nicht jedes Mal einzeln durchgereicht werden muss."""

    def __init__(self, application, window: "MainWindow", client: GoldfishClient, downloads):
        self.application = application
        self.window = window
        self.client = client
        self.downloads = downloads
        # Eine gemeinsame Instanz: die Seiten schreiben dieselbe Datei, und
        # zwei eigene Instanzen würden sich gegenseitig überschreiben.
        self.view_prefs = ViewPrefs()
        # Art je Bibliothek, einmal beim Laden gefüllt — Seiten, die nur eine
        # Bibliotheks-ID kennen (etwa die Detailansicht), brauchen sie für
        # Entscheidungen wie Video- oder Musik-Playlist.
        self.library_kinds: dict[int, str] = {}
        # Ein Spieler pro Fenster, unabhängig vom Videofenster — die Musik
        # läuft weiter, während man durch die Bibliotheken blättert.
        self.music = MusicPlayer(client)
        # Lokale Bibliotheken kennen den Server nicht — sie liegen neben den
        # Server-Bibliotheken und funktionieren auch ohne Verbindung.
        self.local = LocalLibraryManager()

    def library_kind(self, library_id) -> str:
        try:
            return self.library_kinds.get(int(library_id), "")
        except (TypeError, ValueError):
            return ""


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, client: GoldfishClient, downloads, username: str):
        super().__init__(application=app, title="Goldfish")
        self.set_default_size(1120, 720)
        self.client = client
        self.downloads = downloads
        self.ctx = AppContext(app, self, client, downloads)

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        # Die Abspielleiste sitzt als Geschwister NEBEN der geteilten Ansicht,
        # nicht darin: nur so übersteht sie jeden Wechsel von Bibliothek und
        # Ansicht. Läge sie im Navigationsstapel, verschwände sie mit dem
        # Seiteninhalt.
        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(root_box)

        split_view = Adw.NavigationSplitView(vexpand=True)
        root_box.append(split_view)

        sidebar_toolbar = Adw.ToolbarView()
        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_title_widget(Adw.WindowTitle(title="Goldfish", subtitle=username))

        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu = Gio.Menu()
        menu.append("Abmelden", "app.logout")
        menu_button.set_menu_model(menu)
        sidebar_header.pack_end(menu_button)
        sidebar_toolbar.add_top_bar(sidebar_header)

        self.sidebar_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.sidebar_list.add_css_class("navigation-sidebar")
        self.sidebar_list.connect("row-activated", self._on_sidebar_row_activated)
        sidebar_scrolled = Gtk.ScrolledWindow(vexpand=True)
        sidebar_scrolled.set_child(self.sidebar_list)

        # Version unten links als normaler Box-Sibling unter der Liste —
        # BEWUSST NICHT über Adw.ToolbarView.add_bottom_bar(): diese API ist
        # laut libadwaita-Doku für "Bar"-Widgets (AdwHeaderBar/GtkActionBar/
        # AdwTabBar) gedacht, ein einfaches Gtk.Label dort ist nicht
        # ausdrücklich als unterstützt dokumentiert. Ein normaler Gtk.Box mit
        # zwei Kindern (Liste + Label) ist garantiert unproblematisch.
        sidebar_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_content.append(sidebar_scrolled)

        version_label = Gtk.Label(
            label=f"Goldfish Linux {__version__}",
            xalign=0,
            margin_top=6,
            margin_bottom=6,
            margin_start=12,
            margin_end=12,
        )
        version_label.add_css_class("dim-label")
        version_label.add_css_class("caption")
        sidebar_content.append(version_label)

        sidebar_toolbar.set_content(sidebar_content)

        sidebar_page = Adw.NavigationPage(title="Goldfish", child=sidebar_toolbar)
        split_view.set_sidebar(sidebar_page)

        self.nav_view = Adw.NavigationView()
        # Die Startseite ist der Einstieg, wie in allen anderen Clients — ein
        # leeres "Wähle links etwas aus" verschenkt den ersten Blick.
        self.nav_view.push(HomePage(self.ctx, self.nav_view))
        content_page = Adw.NavigationPage(title="Goldfish", child=self.nav_view, tag="content-root")
        split_view.set_content(content_page)

        self.mini_player = MiniPlayer(self.ctx.music)
        root_box.append(self.mini_player)

        self._load_libraries()

    def _load_libraries(self) -> None:
        threading.Thread(target=self._load_libraries_worker, daemon=True).start()

    def _load_libraries_worker(self) -> None:
        try:
            libraries = self.client.libraries()
        except GoldfishAPIError as exc:
            GLib.idle_add(self.show_toast, f"Bibliotheken konnten nicht geladen werden: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 — sonst bleibt die Seitenleiste
            # stumm leer, ohne jede Fehlermeldung, wenn hier etwas Unerwartetes
            # passiert (z. B. unerwartete Antwortform des Servers).
            GLib.idle_add(self.show_toast, f"Bibliotheken konnten nicht geladen werden: {exc}")
            return
        if not libraries:
            GLib.idle_add(
                self.show_toast,
                "Keine Bibliotheken sichtbar — hat dein Benutzer Zugriff auf mindestens eine Bibliothek?",
            )
        GLib.idle_add(self._populate_sidebar, libraries)

    def _populate_sidebar(self, libraries: list[dict]) -> None:
        self.ctx.library_kinds = {int(lib["id"]): (lib.get("kind") or "") for lib in libraries if lib.get("id")}
        # Zuerst die übergreifenden Ansichten, dann die Bibliotheken — dieselbe
        # Ordnung wie im Browser, wo Startseite, Sammlungen und Playlists eigene
        # Knöpfe in der Kopfzeile haben.
        for label, key in (("🏠  Startseite", "home"), ("📚  Sammlungen", "collections"), ("📋  Playlists", "playlists")):
            row = self._build_sidebar_row(label)
            row.library = None
            row.is_downloads = False
            row.special = key
            self.sidebar_list.append(row)

        divider = Gtk.ListBoxRow(selectable=False, activatable=False)
        divider.set_child(Gtk.Separator(margin_top=6, margin_bottom=6))
        self.sidebar_list.append(divider)

        for library in libraries:
            row = self._build_sidebar_row(f"{_KIND_ICON.get(library.get('kind'), '📁')}  {library['name']}")
            row.library = library
            row.is_downloads = False
            row.special = ""
            self.sidebar_list.append(row)

        separator_row = Gtk.ListBoxRow(selectable=False, activatable=False)
        separator_row.set_child(Gtk.Separator(margin_top=6, margin_bottom=6))
        self.sidebar_list.append(separator_row)

        settings_row = self._build_sidebar_row("⚙  Einstellungen")
        settings_row.library = None
        settings_row.is_downloads = False
        settings_row.special = "settings"
        self.sidebar_list.append(settings_row)

        local_row = self._build_sidebar_row("💾  Eigene Datenträger")
        local_row.library = None
        local_row.is_downloads = False
        local_row.special = "local"
        self.sidebar_list.append(local_row)

        downloads_row = self._build_sidebar_row("⬇  Downloads")
        downloads_row.library = None
        downloads_row.is_downloads = True
        downloads_row.special = ""
        self.sidebar_list.append(downloads_row)

    @staticmethod
    def _build_sidebar_row(label_text: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        label = Gtk.Label(label=label_text, xalign=0, margin_top=10, margin_bottom=10, margin_start=12, margin_end=12)
        row.set_child(label)
        return row

    def _on_sidebar_row_activated(self, _listbox, row: Gtk.ListBoxRow) -> None:
        if not hasattr(row, "is_downloads"):
            return  # z. B. die nicht-aktivierbare Trennlinien-Zeile
        # Content-Stack auf den Root zurücksetzen, bevor eine neue Ansicht
        # gepusht wird — sonst stapeln sich beim Wechsel zwischen Bibliotheken
        # immer mehr Ebenen übereinander.
        while self.nav_view.get_navigation_stack().get_n_items() > 1:
            self.nav_view.pop()

        special = getattr(row, "special", "")
        if special == "home":
            page = HomePage(self.ctx, self.nav_view)
        elif special == "collections":
            page = CollectionsPage(self.ctx, self.nav_view)
        elif special == "playlists":
            page = PlaylistsPage(self.ctx, self.nav_view)
        elif special == "local":
            page = LocalLibrariesPage(self.ctx, self.nav_view)
        elif special == "settings":
            page = SettingsPage(self.ctx, self.nav_view)
        elif row.is_downloads:
            page = DownloadsPage(self.ctx, self.nav_view)
        elif (row.library or {}).get("kind") == "music":
            # Musik über die Albenübersicht, nicht über die Ordner: die Alben
            # entstehen serverseitig aus Tags und gemeinsamen Ordnern und
            # entsprechen nicht der Ordnerstruktur. Der Ordner-Browser bleibt
            # von dort aus erreichbar.
            page = MusicLibraryPage(self.ctx, self.nav_view, row.library)
        else:
            page = BrowsePage(self.ctx, self.nav_view, row.library)
        self.nav_view.push(page)

    def show_toast(self, message: str) -> bool:
        """Öffentlich, weil auch die Unterseiten (BrowsePage & Co.) darüber
        melden — sie erreichen das Fenster per `get_root()`."""
        self.toast_overlay.add_toast(Adw.Toast(title=message))
        return False
