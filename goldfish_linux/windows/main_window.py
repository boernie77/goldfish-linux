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
from .browse_page import BrowsePage  # noqa: E402
from .downloads_page import DownloadsPage  # noqa: E402

_KIND_ICON = {"movies": "🎬", "tv": "📺", "music": "🎵", "private": "📁"}


class AppContext:
    """Gemeinsam genutzte Referenzen (Client/Downloads/Fenster), damit jede
    NavigationPage nicht jedes Mal einzeln durchgereicht werden muss."""

    def __init__(self, application, window: "MainWindow", client: GoldfishClient, downloads):
        self.application = application
        self.window = window
        self.client = client
        self.downloads = downloads


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, client: GoldfishClient, downloads, username: str):
        super().__init__(application=app, title="Goldfish")
        self.set_default_size(1120, 720)
        self.client = client
        self.downloads = downloads
        self.ctx = AppContext(app, self, client, downloads)

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        split_view = Adw.NavigationSplitView()
        self.toast_overlay.set_child(split_view)

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
        placeholder = Adw.StatusPage(
            icon_name="io.github.boernie77.GoldfishLinux",
            title="Goldfish",
            description="Wähle links eine Bibliothek aus.",
        )
        self.nav_view.push(Adw.NavigationPage(title="Goldfish", child=placeholder, tag="placeholder"))
        content_page = Adw.NavigationPage(title="Goldfish", child=self.nav_view, tag="content-root")
        split_view.set_content(content_page)

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
        for library in libraries:
            row = self._build_sidebar_row(f"{_KIND_ICON.get(library.get('kind'), '📁')}  {library['name']}")
            row.library = library
            row.is_downloads = False
            self.sidebar_list.append(row)

        separator_row = Gtk.ListBoxRow(selectable=False, activatable=False)
        separator_row.set_child(Gtk.Separator(margin_top=6, margin_bottom=6))
        self.sidebar_list.append(separator_row)

        downloads_row = self._build_sidebar_row("⬇  Downloads")
        downloads_row.library = None
        downloads_row.is_downloads = True
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

        if row.is_downloads:
            page = DownloadsPage(self.ctx, self.nav_view)
        else:
            page = BrowsePage(self.ctx, self.nav_view, row.library)
        self.nav_view.push(page)

    def show_toast(self, message: str) -> bool:
        """Öffentlich, weil auch die Unterseiten (BrowsePage & Co.) darüber
        melden — sie erreichen das Fenster per `get_root()`."""
        self.toast_overlay.add_toast(Adw.Toast(title=message))
        return False
