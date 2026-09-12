"""Anmeldung über Authentik (SSO).

Der Server bietet den üblichen OIDC-Ablauf an: `/api/auth/oidc/login` leitet
zum Anmeldedienst weiter, und nach erfolgreicher Anmeldung landet man über
`/api/auth/oidc/callback` wieder beim Server, der dann den Sitzungs-Cookie
setzt. Weil dabei eine fremde Anmeldeseite angezeigt werden muss, läuft das
in einem eingebetteten Browser.

**WebKit ist eine weiche Abhängigkeit.** Ist es nicht installiert, fehlt
lediglich dieser Knopf — Anmeldung mit Benutzername und Passwort funktioniert
weiterhin. Deshalb steht das Paket in den Empfehlungen und nicht in den
Abhängigkeiten: es zieht eine vollständige Browser-Engine nach sich, die für
den normalen Betrieb nicht gebraucht wird.
"""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

# Der Sitzungs-Cookie erscheint erst kurz NACH dem letzten Seitenwechsel.
# Deshalb wird nach jedem Laden eine Weile nachgesehen, statt einmalig zu
# prüfen — die Mac-App hat denselben Zeitversatz und pollt ebenso.
_POLL_MS = 250
_POLL_ATTEMPTS = 20


def webkit_available() -> bool:
    try:
        gi.require_version("WebKit", "6.0")
        from gi.repository import WebKit  # noqa: F401
    except (ValueError, ImportError):
        return False
    return True


class SSOLoginWindow(Adw.Window):
    """Fenster mit der Anmeldeseite des Anmeldedienstes.

    `on_success` bekommt den Sitzungs-Cookie, sobald der Server ihn gesetzt
    hat."""

    def __init__(self, parent: Gtk.Window, server_url: str, on_success: Callable[[str], None]) -> None:
        gi.require_version("WebKit", "6.0")
        from gi.repository import WebKit

        super().__init__(title="Anmelden über Single-Sign-on", transient_for=parent, modal=True)
        self.set_default_size(720, 780)
        self.server_url = server_url.rstrip("/")
        self.on_success = on_success
        self._attempts = 0
        self._done = False

        header = Adw.HeaderBar()
        fresh = Gtk.Button(icon_name="user-available-symbolic", tooltip_text="Mit anderem Konto anmelden")
        fresh.connect("clicked", lambda *_: self._clear_and_reload())
        header.pack_end(fresh)

        # Eigener, nicht dauerhafter Datenspeicher: so bleibt keine fremde
        # Anmeldung im Programm zurück, und ein Kontowechsel ist möglich,
        # ohne Spuren der vorherigen Sitzung.
        self.web_view = WebKit.WebView(
            network_session=WebKit.NetworkSession.new_ephemeral(),
            vexpand=True,
        )
        self.web_view.connect("load-changed", self._on_load_changed)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(self.web_view)
        self.set_content(toolbar_view)

        self.web_view.load_uri(f"{self.server_url}/api/auth/oidc/login")

    def _clear_and_reload(self) -> None:
        from gi.repository import WebKit

        self.web_view.set_network_session(WebKit.NetworkSession.new_ephemeral()) if hasattr(
            self.web_view, "set_network_session"
        ) else None
        self.web_view.load_uri(f"{self.server_url}/api/auth/oidc/login")

    def _on_load_changed(self, _view, event) -> None:
        from gi.repository import WebKit

        if event != WebKit.LoadEvent.FINISHED or self._done:
            return
        self._attempts = 0
        GLib.timeout_add(_POLL_MS, self._check_cookie)

    def _check_cookie(self) -> bool:
        if self._done:
            return False
        self._attempts += 1
        manager = self.web_view.get_network_session().get_cookie_manager()
        manager.get_cookies(self.server_url, None, self._on_cookies, None)
        return self._attempts < _POLL_ATTEMPTS

    def _on_cookies(self, manager, result, _data) -> None:
        try:
            cookies = manager.get_cookies_finish(result)
        except GLib.Error:
            return
        for cookie in cookies or []:
            if cookie.get_name() == "goldfish_session" and cookie.get_value():
                self._done = True
                self.on_success(cookie.get_value())
                self.close()
                return
