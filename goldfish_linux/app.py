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
            threading.Thread(target=self._try_restore_session, daemon=True).start()
        else:
            self._show_login()

    def _try_restore_session(self) -> None:
        try:
            status = self.client.status()
        except GoldfishAPIError:
            status = None
        if status and status.logged_in:
            GLib.idle_add(self._show_main, status.username)
        else:
            self.settings.clear_session()
            GLib.idle_add(self._show_login)

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
