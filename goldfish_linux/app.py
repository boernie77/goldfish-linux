"""Adw.Application — verwaltet Login-Status und wechselt zwischen
Login-Fenster und Hauptfenster."""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib  # noqa: E402

from . import APP_ID, __version__
from .api import GoldfishAPIError, GoldfishClient
from .config import Settings, ViewPrefs, ensure_dirs
from .downloads import DownloadManager
from .theme import apply_color_scheme
from .updater import Release, UpdateError, is_newer, latest_release
from .windows.login_window import LoginWindow
from .windows.main_window import MainWindow
from .windows.update_dialog import offer_update

# Timeout für die Session-Prüfung beim Start. Bewusst kürzer als das
# DEFAULT_TIMEOUT der API (20 s + 1 Retry = bis 40 s): solange die Antwort
# aussteht, ist noch KEIN Fenster auf dem Schirm. Ist der Server nicht
# erreichbar, soll zügig der Login-Dialog erscheinen statt einer App, die
# gefühlt gar nicht startet.
SESSION_RESTORE_TIMEOUT = 6

# Verzögerung der Aktualisierungs-Suche nach dem Start. Der erste Eindruck
# gehört der Bibliothek, nicht einer Anfrage an GitHub — und wer die App nur
# kurz öffnet, soll dafür gar keine auslösen.
UPDATE_CHECK_DELAY_S = 8


class GoldfishApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        ensure_dirs()
        # Vor jedem Fenster (auch dem Login-Fenster) — sonst blitzt beim Start
        # kurz das helle Theme auf, bevor die Einstellungen greifen.
        apply_color_scheme(ViewPrefs().color_scheme())
        self.settings = Settings()
        self.client = GoldfishClient()
        self.downloads: DownloadManager | None = None
        self.main_window: MainWindow | None = None
        self.login_window: LoginWindow | None = None

        # Ergebnis der letzten Suche nach einer neuen Fassung: `None` heisst
        # "nichts Neues bekannt" (auch vor der ersten Suche).
        self.available_update: Release | None = None

        logout_action = Gio.SimpleAction.new("logout", None)
        logout_action.connect("activate", self._on_logout_action)
        self.add_action(logout_action)

        update_action = Gio.SimpleAction.new("check_update", None)
        update_action.connect("activate", self._on_check_update_action)
        self.add_action(update_action)

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
        """Prüft die gespeicherte Anmeldung beim Start.

        **Wichtig — die gespeicherte Anmeldung wird NUR verworfen, wenn der
        Server ausdrücklich sagt, dass sie nicht mehr gilt.** Ein
        Netzwerkfehler, ein Zeitüberschreiten oder ein "502 Bad Gateway" vom
        Reverse-Proxy sagen darüber nichts aus — der Server war schlicht kurz
        nicht erreichbar. Vorher wurde in diesen Fällen der Token gelöscht und
        man musste sich neu anmelden, obwohl die Sitzung gültig war; bei einem
        kurz überlasteten Server ist das real passiert. Jetzt bleibt sie
        erhalten, und es erscheint nur der Anmeldedialog — der nächste Start
        kann sie wieder nutzen."""
        try:
            status = self.client.status(timeout=SESSION_RESTORE_TIMEOUT)
        except GoldfishAPIError:
            # Nicht erreichbar: Anmeldung behalten, nur diesmal nicht nutzen.
            GLib.idle_add(self._finish_restore, None)
            return
        if status.logged_in:
            GLib.idle_add(self._finish_restore, status.username)
            return
        # Der Server hat geantwortet und die Sitzung abgelehnt — erst jetzt weg.
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
        GLib.timeout_add_seconds(UPDATE_CHECK_DELAY_S, self._start_silent_update_check)
        return False

    # -- Aktualisierung ---------------------------------------------------

    def _start_silent_update_check(self) -> bool:
        """Stille Suche kurz nach dem Start — meldet sich nur, wenn es etwas
        gibt, und dann bloss als Hinweis im Menü, ohne Dialog."""
        self._check_for_update(manual=False)
        return False  # einmalig, kein wiederkehrender Zeitgeber

    def _on_check_update_action(self, *_args) -> None:
        # Ist schon etwas bekannt, direkt anbieten statt erneut zu fragen.
        if self.available_update and self.main_window:
            offer_update(self.main_window, self.available_update)
            return
        self._check_for_update(manual=True)

    def _check_for_update(self, manual: bool) -> None:
        if manual and self.main_window:
            self.main_window.show_toast("Suche nach Aktualisierungen …")

        def worker() -> None:
            try:
                release = latest_release()
            except UpdateError as exc:
                if manual:
                    GLib.idle_add(self._update_check_failed, str(exc))
                return  # stille Suche schweigt bei Fehlern (z. B. offline)
            GLib.idle_add(self._update_check_done, release, manual)

        threading.Thread(target=worker, daemon=True).start()

    def _update_check_failed(self, message: str) -> bool:
        if self.main_window:
            self.main_window.show_toast(message)
        return False

    def _update_check_done(self, release: Release, manual: bool) -> bool:
        if not is_newer(release.version):
            self.available_update = None
            if manual and self.main_window:
                self.main_window.show_toast(f"Bereits aktuell ({__version__}).")
            return False
        self.available_update = release
        if self.main_window:
            self.main_window.set_update_available(release.version)
            if manual:
                offer_update(self.main_window, release)
        return False

    def _on_logout_action(self, *_args) -> None:
        self.client.logout()
        self.settings.clear_session()
        if self.main_window:
            self.main_window.close()
            self.main_window = None
        self._show_login()
