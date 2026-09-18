"""Hauptfenster: Bibliotheks-Sidebar (Adw.NavigationSplitView) + Content-
Navigationsstapel (Adw.NavigationView, verwaltet Zurück-Navigation
automatisch)."""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import __version__  # noqa: E402
from ..api import GoldfishAPIError, GoldfishClient  # noqa: E402
from ..config import ViewPrefs  # noqa: E402
from ..local_library import LocalLibraryManager  # noqa: E402
from ..music_player import MusicPlayer  # noqa: E402
from ..widgets.mini_player import MiniPlayer  # noqa: E402
from ..widgets.poster import load_poster_async  # noqa: E402
from .browse_page import BrowsePage  # noqa: E402
from .collections_page import CollectionsPage  # noqa: E402
from .downloads_page import DownloadsPage  # noqa: E402
from .home_page import HomePage  # noqa: E402
from .local_page import LocalLibrariesPage  # noqa: E402
from .music_page import MusicLibraryPage  # noqa: E402
from .playlists_page import PlaylistsPage  # noqa: E402
from .settings_page import SettingsPage  # noqa: E402

_KIND_ICON = {"movies": "🎬", "tv": "📺", "music": "🎵", "private": "📁"}

# Runde Vorschaubild-Kachel vor jeder Bibliothek in der Seitenleiste (analog
# zur Mac/iOS-App), statt nur eines Emoji-Icons. Solange kein Vorschaubild
# geladen ist (oder die Bibliothek leer ist), bleibt das Art-Kürzel als
# Fallback SICHTBAR darunter — die Bild-Kachel liegt als Overlay-Kind darüber
# und deckt es erst ab, sobald tatsächlich etwas geladen wurde.
_AVATAR_SIZE = 28
_AVATAR_CSS = b"""
.gf-sidebar-avatar-bg {
  background-color: alpha(@window_fg_color, 0.08);
  border-radius: 9999px;
  font-size: 0.85rem;
}
.gf-sidebar-avatar {
  border-radius: 9999px;
}
"""
_avatar_css_loaded = False


def _ensure_avatar_css() -> None:
    global _avatar_css_loaded
    if _avatar_css_loaded:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_AVATAR_CSS)
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _avatar_css_loaded = True


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
        # Es gibt immer nur EIN Wiedergabefenster; hier steht das aktuelle
        # (siehe `windows.player_window.open_player`).
        self.player_window = None

    def dialog_parent(self):
        """Das Fenster, über dem ein Dialog erscheinen soll: das gerade aktive,
        sonst das Hauptfenster.

        Wichtig, weil es mehrere Fenster gibt: ein Dialog, der am Hauptfenster
        hängt, kann hinter dem Wiedergabefenster verschwinden und dort
        unsichtbar auf eine Antwort warten — die App wirkt dann eingefroren."""
        active = self.application.get_active_window() if self.application else None
        return active or self.window

    def library_kind(self, library_id) -> str:
        try:
            return self.library_kinds.get(int(library_id), "")
        except (TypeError, ValueError):
            return ""


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, client: GoldfishClient, downloads, username: str):
        super().__init__(application=app, title="Goldfish")
        _ensure_avatar_css()
        self.set_default_size(1120, 720)
        self.client = client
        self.downloads = downloads
        self.ctx = AppContext(app, self, client, downloads)
        # Bibliotheks-ID → Vorschaubild-Pfad, einmal pro Bibliothek ermittelt
        # (kostet einen Zufalls-Item-Aufruf) und über Seitenleisten-Neuaufbauten
        # hinweg behalten, damit ein Wechsel in den Einstellungen nicht jedes
        # Mal alle Vorschaubilder neu anfordert.
        self._library_previews: dict[int, str | None] = {}

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

        self.menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self.menu = Gio.Menu()
        # Position 0: der Eintrag wechselt seine Beschriftung, sobald eine
        # neue Fassung bekannt ist (siehe set_update_available). Gio.Menu
        # kennt kein Umbenennen — ein Eintrag wird entfernt und neu gesetzt,
        # deshalb muss seine Position bekannt bleiben.
        self.menu.append("Nach Aktualisierungen suchen", "app.check_update")
        self.menu.append("Abmelden", "app.logout")
        self.menu_button.set_menu_model(self.menu)
        sidebar_header.pack_end(self.menu_button)
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

        # Feste, einzeilige Breite mit Auslassung: `set_update_available`
        # ersetzt den Text durch „Goldfish Linux 0.1.51 · 0.1.52 verfügbar".
        # Ohne Begrenzung wächst die natürliche Breite des Labels, die
        # Seitenleiste wird breiter und die ganze Zeile verschiebt sich
        # (User-Report 2026-09-18: „die Zeile springt"). Der vollständige Text
        # steht im Tooltip, damit nichts verloren geht.
        self.version_label = version_label = Gtk.Label(
            label=f"Goldfish Linux {__version__}",
            xalign=0,
            margin_top=6,
            margin_bottom=6,
            margin_start=12,
            margin_end=12,
            single_line_mode=True,
            ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=24,
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

    def reload_libraries(self) -> None:
        """Seitenleiste neu aufbauen. Wird auch von den Einstellungen gerufen,
        wenn sich die Auswahl für die Reiterleiste geändert hat — sonst müsste
        man die App neu starten, damit eine abgewählte Bibliothek verschwindet
        (genau das ist vor 0.1.18 passiert)."""
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
        # Welche Bibliotheken in der Leiste erscheinen und in welcher
        # Reihenfolge, entscheidet der Benutzer in den Einstellungen
        # ("Startseite & Reiter"). Der Server führt das pro Benutzer in einer
        # eigenen Tabelle; ohne diese Abfrage stünden auch abgewählte
        # Bibliotheken in der Leiste. Schlägt sie fehl, bleibt es bei allen —
        # eine leere Leiste wäre das schlechtere Ergebnis.
        prefs: dict[int, tuple[bool, int]] = {}
        try:
            for row in (self.client.nav_preferences().get("libraries") or []):
                lib_id = int(row.get("libraryId") or 0)
                if lib_id:
                    prefs[lib_id] = (bool(row.get("onNav", True)), int(row.get("order") or 0))
        except Exception:  # noqa: BLE001 — Sichtbarkeit ist Komfort, nicht Kern
            prefs = {}
        if not libraries:
            GLib.idle_add(
                self.show_toast,
                "Keine Bibliotheken sichtbar — hat dein Benutzer Zugriff auf mindestens eine Bibliothek?",
            )
        GLib.idle_add(self._populate_sidebar, libraries, prefs)

    def _populate_sidebar(self, libraries: list[dict], prefs: dict[int, tuple[bool, int]] | None = None) -> None:
        prefs = prefs or {}
        # Die Arten ALLER Bibliotheken merken, auch der ausgeblendeten: andere
        # Seiten kennen oft nur eine Bibliotheks-ID und brauchen die Art (etwa
        # die Detailansicht für die Wahl der Playlist-Art).
        self.ctx.library_kinds = {int(lib["id"]): (lib.get("kind") or "") for lib in libraries if lib.get("id")}
        # Beim Neuaufbau erst leeren — sonst hängen die alten Zeilen darunter.
        while (row := self.sidebar_list.get_first_child()) is not None:
            self.sidebar_list.remove(row)

        visible = [lib for lib in libraries if prefs.get(int(lib.get("id") or 0), (True, 0))[0]]
        visible.sort(key=lambda lib: prefs.get(int(lib.get("id") or 0), (True, 0))[1])
        libraries = visible
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
            row = self._build_library_row(library)
            row.library = library
            row.is_downloads = False
            row.special = ""
            self.sidebar_list.append(row)

        # Eigene Datenträger stehen wie Bibliotheken in der Leiste — jede
        # Sammel-Bibliothek als EIN Eintrag, die übrigen einzeln. Welche davon
        # erscheinen, steht in den Einstellungen ("Startseite und
        # Reiterleiste"); gemerkt wird das lokal, weil der Server diese
        # Bibliotheken nicht kennt.
        for local in self.ctx.local.visible_libraries():
            # Fehlende Vorschaubilder im Hintergrund erzeugen, auch ohne dass
            # man die Bibliothek öffnet — sonst entstünde ein Bild erst beim
            # Vorbeiscrollen. Bereits vorhandene werden übersprungen.
            if local.available:
                self.ctx.local.prefetch_thumbnails_async(local)
            if not self.ctx.view_prefs.local_in_sidebar(local.nav_key):
                continue
            row = self._build_sidebar_row(f"💾  {local.name}")
            row.library = None
            row.is_downloads = False
            row.special = ""
            row.local_library = local
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

    def _build_library_row(self, library: dict) -> Gtk.ListBoxRow:
        """Zeile mit rundem Vorschaubild statt nur einem Art-Emoji (analog zur
        Mac/iOS-App). Das Emoji bleibt als Fallback SICHTBAR unter dem Bild
        (Gtk.Overlay) — bis ein Vorschaubild geladen ist (oder wenn die
        Bibliothek leer ist und keins existiert), sieht man weiterhin die
        Art auf einen Blick."""
        row = Gtk.ListBoxRow()
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
            margin_top=8,
            margin_bottom=8,
            margin_start=12,
            margin_end=12,
        )
        emoji = _KIND_ICON.get(library.get("kind"), "📁")
        avatar_bg = Gtk.Label(label=emoji, width_request=_AVATAR_SIZE, height_request=_AVATAR_SIZE)
        avatar_bg.add_css_class("gf-sidebar-avatar-bg")
        overlay = Gtk.Overlay(child=avatar_bg)
        picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER)
        picture.set_overflow(Gtk.Overflow.HIDDEN)
        picture.add_css_class("gf-sidebar-avatar")
        overlay.add_overlay(picture)
        overlay.set_measure_overlay(picture, False)
        box.append(overlay)

        label = Gtk.Label(label=library.get("name") or "", xalign=0, hexpand=True)
        box.append(label)
        row.set_child(box)

        self._load_library_preview(library, picture)
        return row

    def _load_library_preview(self, library: dict, picture: Gtk.Picture) -> None:
        try:
            lib_id = int(library.get("id") or 0)
        except (TypeError, ValueError):
            return
        if not lib_id:
            return
        cached = self._library_previews.get(lib_id)
        if cached is not None:
            if cached:
                load_poster_async(picture, self.client, cached, decode_width=_AVATAR_SIZE * 3)
            return
        threading.Thread(
            target=self._fetch_library_preview_worker, args=(lib_id, picture), daemon=True
        ).start()

    def _fetch_library_preview_worker(self, lib_id: int, picture: Gtk.Picture) -> None:
        try:
            path = self.client.library_preview_path(lib_id) or ""
        except Exception:  # noqa: BLE001 — ein Netzwerk-/Serverfehler ist KEIN
            # "kein Vorschaubild vorhanden" — nichts merken, sonst bleibt eine
            # Bibliothek nach einem einmaligen Aussetzer für den Rest der
            # Sitzung ohne Bild stehen. Der nächste Seitenleisten-Neuaufbau
            # (z. B. nach den Einstellungen) versucht es einfach erneut.
            return
        # "" gespeichert = wirklich versucht UND nichts gefunden (leere
        # Bibliothek oder alle Stichproben ohne Poster/Thumbnail) —
        # unterscheidet sich von "noch nie versucht" (Schlüssel fehlt ganz).
        self._library_previews[lib_id] = path
        if path:
            GLib.idle_add(load_poster_async, picture, self.client, path, _AVATAR_SIZE * 3, None)

    def _on_sidebar_row_activated(self, _listbox, row: Gtk.ListBoxRow) -> None:
        if not hasattr(row, "is_downloads"):
            return  # z. B. die nicht-aktivierbare Trennlinien-Zeile
        # Content-Stack auf den Root zurücksetzen, bevor eine neue Ansicht
        # gepusht wird — sonst stapeln sich beim Wechsel zwischen Bibliotheken
        # immer mehr Ebenen übereinander.
        while self.nav_view.get_navigation_stack().get_n_items() > 1:
            self.nav_view.pop()

        local_library = getattr(row, "local_library", None)
        if local_library is not None:
            from .local_page import LocalVideosPage

            self.nav_view.push(LocalVideosPage(self.ctx, self.nav_view, local_library))
            return

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

    def set_update_available(self, version: str) -> None:
        """Meldet eine verfuegbare neue Fassung — im Menue und unter der
        Seitenleiste, aber ohne Dialog.

        Bewusst zurueckhaltend: die stille Suche laeuft kurz nach dem Start,
        und ein Dialog, der ungefragt ueber der Bibliothek aufgeht, waere
        genau die Sorte Unterbrechung, die man von einer Medien-App nicht
        will. Wer den Hinweis sieht, klickt ihn an."""
        self.menu.remove(0)
        self.menu.insert(0, f"Aktualisierung auf {version} einspielen", "app.check_update")
        # Der Menue-Knopf selbst bekommt die Akzentfarbe, sonst faende man den
        # Hinweis nur, wenn man das Menue ohnehin oeffnet.
        self.menu_button.add_css_class("suggested-action")
        self.menu_button.set_tooltip_text(f"Aktualisierung auf {version} verfügbar")
        text = f"Goldfish Linux {__version__} · {version} verfügbar"
        self.version_label.set_label(text)
        # Vollständiger Text im Tooltip — die Zeile selbst bleibt in ihrer
        # Breite fest (siehe Kommentar am Label).
        self.version_label.set_tooltip_text(text)
        self.version_label.remove_css_class("dim-label")

    def show_toast(self, message: str) -> bool:
        """Öffentlich, weil auch die Unterseiten (BrowsePage & Co.) darüber
        melden — sie erreichen das Fenster per `get_root()`."""
        self.toast_overlay.add_toast(Adw.Toast(title=message))
        return False
