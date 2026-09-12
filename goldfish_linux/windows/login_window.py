"""Login-Fenster: Server-URL + Benutzername/Passwort.

Ruft `GoldfishClient.login()` in einem Hintergrund-Thread auf (Netzwerk-I/O
darf den GTK-Main-Loop nicht blockieren) und meldet das Ergebnis über
GLib.idle_add zurück ans Fenster.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import __version__  # noqa: E402
from ..api import GoldfishAPIError, GoldfishClient  # noqa: E402


class LoginWindow(Adw.ApplicationWindow):
    def __init__(self, app, client: GoldfishClient, on_success, default_server: str = "", default_user: str = ""):
        super().__init__(application=app, title="Goldfish – Anmelden")
        self.client = client
        self.on_success = on_success
        self.set_default_size(420, 460)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())

        clamp = Adw.Clamp(maximum_size=380, margin_top=32, margin_bottom=32, margin_start=24, margin_end=24)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        clamp.set_child(box)

        icon = Gtk.Image(icon_name="io.github.boernie77.GoldfishLinux")
        icon.set_pixel_size(64)
        box.append(icon)

        title = Gtk.Label(label="Bei Goldfish anmelden")
        title.add_css_class("title-2")
        box.append(title)

        group = Adw.PreferencesGroup()
        box.append(group)

        self.server_row = Adw.EntryRow(title="Server-Adresse")
        self.server_row.set_text("https://")
        group.add(self.server_row)

        self.user_row = Adw.EntryRow(title="Benutzername")
        group.add(self.user_row)

        self.pass_row = Adw.PasswordEntryRow(title="Passwort")
        group.add(self.pass_row)

        if default_server:
            self.server_row.set_text(default_server)
        if default_user:
            self.user_row.set_text(default_user)

        self.status_label = Gtk.Label(label="")
        self.status_label.add_css_class("error")
        self.status_label.set_wrap(True)
        self.status_label.set_visible(False)
        box.append(self.status_label)

        self.login_button = Gtk.Button(label="Anmelden")
        self.login_button.add_css_class("suggested-action")
        self.login_button.add_css_class("pill")
        self.login_button.connect("clicked", self._on_login_clicked)
        box.append(self.login_button)

        # Single-Sign-on nur anbieten, wenn ein eingebetteter Browser
        # vorhanden ist (WebKit ist eine Empfehlung, keine Abhängigkeit) —
        # sonst führte der Knopf ins Leere.
        from .sso_dialog import webkit_available

        if webkit_available():
            self.sso_button = Gtk.Button(label="Mit Single-Sign-on anmelden")
            self.sso_button.add_css_class("pill")
            self.sso_button.connect("clicked", self._on_sso_clicked)
            box.append(self.sso_button)

        self.spinner = Gtk.Spinner()
        box.append(self.spinner)

        version_label = Gtk.Label(label=f"Goldfish Linux {__version__}")
        version_label.add_css_class("dim-label")
        version_label.add_css_class("caption")
        version_label.set_margin_top(12)
        box.append(version_label)

        self.pass_row.connect("entry-activated", self._on_login_clicked)

        toolbar_view.set_content(clamp)
        self.set_content(toolbar_view)

    def _set_busy(self, busy: bool) -> None:
        self.login_button.set_sensitive(not busy)
        self.server_row.set_sensitive(not busy)
        self.user_row.set_sensitive(not busy)
        self.pass_row.set_sensitive(not busy)
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()

    def _on_login_clicked(self, *_args) -> None:
        server = self.server_row.get_text().strip()
        username = self.user_row.get_text().strip()
        password = self.pass_row.get_text()
        self.status_label.set_visible(False)

        if not server or not username or not password:
            self._show_error("Bitte Server, Benutzername und Passwort ausfüllen.")
            return

        self._set_busy(True)
        threading.Thread(
            target=self._login_worker, args=(server, username, password), daemon=True
        ).start()

    def _login_worker(self, server: str, username: str, password: str) -> None:
        try:
            status = self.client.login(server, username, password)
        except GoldfishAPIError as exc:
            GLib.idle_add(self._on_login_failed, str(exc))
            return
        if not status.logged_in:
            GLib.idle_add(self._on_login_failed, "Anmeldung fehlgeschlagen.")
            return
        GLib.idle_add(self._on_login_ok, server, username)

    def _on_sso_clicked(self, *_args) -> None:
        """Öffnet die Anmeldeseite des Anmeldedienstes in einem eigenen Fenster.

        Der Server antwortet auf `/api/auth/oidc/login` mit 503, wenn kein
        Anmeldedienst eingerichtet ist — das wird hier nicht vorab geprüft,
        sondern im Fenster sichtbar, samt der Begründung des Servers."""
        server = self.server_row.get_text().strip().rstrip("/")
        if not server:
            self._show_error("Bitte zuerst die Server-Adresse eintragen.")
            return
        from .sso_dialog import SSOLoginWindow

        def on_token(token: str) -> None:
            # Der Server hat den Sitzungs-Cookie gesetzt; ab hier ist es eine
            # gewöhnliche Anmeldung.
            self.client.set_server(server, token)
            try:
                status = self.client.status()
            except GoldfishAPIError as exc:
                self._show_error(str(exc))
                return
            if not status.logged_in:
                self._show_error("Die Anmeldung kam nicht durch.")
                return
            self._on_login_ok(server, status.username)

        window = SSOLoginWindow(self, server, on_token)
        window.present()

    def _on_login_failed(self, message: str) -> bool:
        self._set_busy(False)
        self._show_error(message)
        return False

    def _on_login_ok(self, server: str, username: str) -> bool:
        self._set_busy(False)
        self.on_success(server, username)
        return False

    def _show_error(self, message: str) -> None:
        self.status_label.set_label(message)
        self.status_label.set_visible(True)
