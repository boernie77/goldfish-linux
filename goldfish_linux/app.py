"""Adw.Application — verwaltet Login-Status und wechselt zwischen
Login-Fenster und Hauptfenster."""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib  # noqa: E402

from . import APP_ID
from .api import GoldfishAPIError, GoldfishClient
from .config import Settings, ensure_dirs
from .downloads import DownloadManager
from .windows.login_window import LoginWindow
from .windows.main_window import MainWindow

# Timeout für die Session-Prüfung beim Start. Bewusst kürzer als das
# DEFAULT_TIMEOUT der API (20 s + 1 Retry = bis 40 s): solange die Antwort
# aussteht, ist noch KEIN Fenster auf dem Schirm. Ist der Server nicht
# erreichbar, soll zügig der Login-Dialog erscheinen statt einer App, die
# gefühlt gar nicht startet.
SESSION_RESTORE_TIMEOUT = 6


class GoldfishApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        ensure_dirs()
        self.settings = Settings()
        self.client = GoldfishClient()
        self.downloads: DownloadManager | None = None
        self.main_window: MainWindow | None = None
        self.login_window: LoginWindow | None = None

        logout_action = Gio.SimpleAction.new("logout", None)
        logout_action.connect("activate", self._on_logout_action)
        self.add_action(logout_action)

    def do_activate(self) -> None:  # noqa: N802 — GObject-Override-Konvention
        if self.main_window:
            self.main_window.present()
            return
        if self.settings.server_url and self.settings.session_token:
            self.client.set_server(self.settings.server_url, self.settings.session_token)
            # WICHTIG: Gtk.Application beendet sich, sobald seine Fensterliste leer
            # ist. Die Session-Prüfung läuft in einem Hintergrund-Thread — zwischen
            # hier und _finish_restore() existiert also noch KEIN Fenster. Ohne
            # hold() kehrt do_activate() mit leerer Fensterliste zurück und die App
            # quittet sofort mit Exit-Code 0, bevor der Thread sein idle_add
            # abfeuern kann. Genau das war der "startet gar nicht mehr"-Bug bis
            # 0.1.6: er trat NUR mit gespeicherter Session auf (ein leeres
            # Config-Verzeichnis nahm den _show_login-Zweig und startete normal),
            # weshalb er erst auffiel, als der Login durch 0.1.2-0.1.4 endlich
            # funktionierte und zum ersten Mal ein Token gespeichert wurde.
            self.hold()
            threading.Thread(target=self._try_restore_session, daemon=True).start()
        else:
            self._show_login()

    def _try_restore_session(self) -> None:
        try:
            status = self.client.status(timeout=SESSION_RESTORE_TIMEOUT)
        except GoldfishAPIError:
            status = None
        if status and status.logged_in:
            GLib.idle_add(self._finish_restore, status.username)
        else:
            self.settings.clear_session()
            GLib.idle_add(self._finish_restore, None)

    def _finish_restore(self, username: str | None) -> bool:
        """Läuft im GTK-Mainloop: erst Fenster zeigen, DANN den hold() aus
        do_activate() freigeben. Die Reihenfolge ist zwingend — ein release()
        vor dem present() würde die Fensterliste kurzzeitig leer sehen und die
        App beenden."""
        if username:
            self._show_main(username)
        else:
            self._show_login()
        self.release()
        return False

    def _show_login(self) -> bool:
        self.login_window = LoginWindow(
            self,
            self.client,
            self._on_login_success,
            default_server=self.settings.server_url,
            default_user=self.settings.username,
        )
        self.login_window.present()
        return False

    def _on_login_success(self, server_url: str, username: str) -> None:
        self.settings.server_url = server_url
        self.settings.session_token = self.client.session_token
        self.settings.username = username
        self.settings.save()
        if self.login_window:
            self.login_window.close()
            self.login_window = None
        self._show_main(username)

    def _show_main(self, username: str) -> bool:
        self.downloads = DownloadManager(self.client)
        self.main_window = MainWindow(self, self.client, self.downloads, username)
        self.main_window.present()
        return False

    def _on_logout_action(self, *_args) -> None:
        self.client.logout()
        self.settings.clear_session()
        if self.main_window:
            self.main_window.close()
            self.main_window = None
        self._show_login()
