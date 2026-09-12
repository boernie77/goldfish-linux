"""Einstellungen: eigenes Konto, Gesehen-Sync, Startseite und Reiterleiste.

Bewusst nur Dinge, die den angemeldeten Benutzer selbst betreffen. Die
Serververwaltung — Benutzer anlegen, Bibliotheken einrichten, Scans auslösen —
bleibt dem Browser überlassen, wie in allen anderen Clients auch.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..api import GoldfishAPIError  # noqa: E402
from ..theme import apply_color_scheme  # noqa: E402


class SettingsPage(Adw.NavigationPage):
    def __init__(self, ctx, nav_view: Adw.NavigationView):
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())

        super().__init__(title="Einstellungen", tag="settings", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.toolbar_view = toolbar_view

        page = Adw.PreferencesPage()
        page.add(self._account_group())
        page.add(self._sync_group())
        page.add(self._display_group())
        toolbar_view.set_content(page)

        threading.Thread(target=self._load_links, daemon=True).start()

    # -- Konto -----------------------------------------------------------

    def _account_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Mein Konto")

        row = Adw.ActionRow(title="Passwort ändern", subtitle="Gilt für die Anmeldung an diesem Server", activatable=True)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda *_: self._ask_password())
        group.add(row)

        logout = Adw.ActionRow(title="Abmelden", subtitle="Die gespeicherte Anmeldung wird verworfen", activatable=True)
        logout.add_suffix(Gtk.Image(icon_name="system-log-out-symbolic"))
        logout.connect("activated", lambda *_: self.ctx.application.activate_action("logout", None))
        group.add(logout)
        return group

    def _ask_password(self) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self.ctx.dialog_parent(),
            heading="Passwort ändern",
            body="Das neue Passwort muss mindestens sechs Zeichen haben.",
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        old = Gtk.PasswordEntry(placeholder_text="Bisheriges Passwort", show_peek_icon=True)
        new = Gtk.PasswordEntry(placeholder_text="Neues Passwort", show_peek_icon=True)
        again = Gtk.PasswordEntry(placeholder_text="Neues Passwort wiederholen", show_peek_icon=True)
        for entry in (old, new, again):
            box.append(entry)
        hint = Gtk.Label(xalign=0, wrap=True, visible=False)
        hint.add_css_class("error")
        box.append(hint)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Abbrechen")
        dialog.add_response("save", "Ändern")
        dialog.set_default_response("save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

        def on_response(_d, response: str) -> None:
            if response != "save":
                return
            if new.get_text() != again.get_text():
                # Nicht stillschweigend abbrechen: Der Dialog ist zu, also
                # sagt eine Meldung, was schiefging.
                self._toast("Die beiden neuen Passwörter stimmen nicht überein.")
                return
            if len(new.get_text()) < 6:
                self._toast("Das neue Passwort ist zu kurz (mindestens sechs Zeichen).")
                return
            self._change_password(old.get_text(), new.get_text())

        dialog.connect("response", on_response)
        dialog.present()

    def _change_password(self, old: str, new: str) -> None:
        def worker() -> None:
            try:
                self.ctx.client.change_password(old, new)
            except GoldfishAPIError as exc:
                # Der Server liefert brauchbare Texte: falsches Passwort (403)
                # oder zu kurz (400) — direkt anzeigen.
                GLib.idle_add(self._toast, f"Passwort nicht geändert: {exc}")
                return
            GLib.idle_add(self._toast, "Passwort geändert.")

        threading.Thread(target=worker, daemon=True).start()

    # -- Gesehen-Sync ----------------------------------------------------

    def _sync_group(self) -> Adw.PreferencesGroup:
        self.sync_group = Adw.PreferencesGroup(
            title="Gesehen-Sync",
            description=(
                "Verknüpft den Gesehen-Status mit einem anderen Benutzer. Beide müssen zustimmen. "
                "Übertragen wird nur, was der jeweils andere selbst sehen darf."
            ),
        )
        self.sync_placeholder = Adw.ActionRow(title="Wird geladen …")
        self.sync_group.add(self.sync_placeholder)
        return self.sync_group

    def _load_links(self) -> None:
        try:
            links = self.ctx.client.watch_links()
            others = self.ctx.client.other_users()
        except GoldfishAPIError as exc:
            GLib.idle_add(self._show_sync_error, str(exc))
            return
        GLib.idle_add(self._show_links, links, others)

    def _show_sync_error(self, message: str) -> bool:
        self.sync_placeholder.set_title("Nicht abrufbar")
        self.sync_placeholder.set_subtitle(message)
        return False

    def _show_links(self, links: list[dict], others: list[dict]) -> bool:
        self.sync_group.remove(self.sync_placeholder)
        self._sync_rows: list[Gtk.Widget] = []

        for link in links:
            status = link.get("status")
            partner = link.get("partnerName") or f"Benutzer {link.get('partnerId')}"
            if status == "accepted":
                subtitle = "verknüpft"
            elif link.get("requesterId") and int(link["requesterId"]) == int(link.get("partnerId") or 0):
                subtitle = "möchte sich mit dir verknüpfen"
            else:
                subtitle = "warten auf Bestätigung"
            row = Adw.ActionRow(title=partner, subtitle=subtitle)

            if status != "accepted" and subtitle.startswith("möchte"):
                confirm = Gtk.Button(label="Bestätigen", valign=Gtk.Align.CENTER)
                confirm.add_css_class("suggested-action")
                confirm.connect("clicked", lambda _b, p=link: self._link_action("confirm", p))
                row.add_suffix(confirm)

            remove = Gtk.Button(
                icon_name="user-trash-symbolic",
                valign=Gtk.Align.CENTER,
                tooltip_text="Verknüpfung trennen" if status == "accepted" else "Anfrage ablehnen",
            )
            remove.connect("clicked", lambda _b, p=link: self._link_action("remove", p))
            row.add_suffix(remove)
            self.sync_group.add(row)
            self._sync_rows.append(row)

        linked = {int(l.get("partnerId") or 0) for l in links}
        candidates = [u for u in others if int(u.get("id") or 0) not in linked]
        if candidates:
            combo = Adw.ComboRow(title="Verknüpfung anfragen")
            model = Gtk.StringList()
            for user in candidates:
                model.append(user.get("username") or "")
            combo.set_model(model)
            ask = Gtk.Button(label="Anfragen", valign=Gtk.Align.CENTER)
            ask.connect(
                "clicked",
                lambda *_: self._request_link(candidates[combo.get_selected()].get("username") or ""),
            )
            combo.add_suffix(ask)
            self.sync_group.add(combo)
            self._sync_rows.append(combo)
        elif not links:
            self.sync_group.add(Adw.ActionRow(title="Keine anderen Benutzer auf diesem Server"))
        return False

    def _request_link(self, username: str) -> None:
        if not username:
            return

        def worker() -> None:
            try:
                self.ctx.client.request_watch_link(username)
            except GoldfishAPIError as exc:
                GLib.idle_add(self._toast, f"Anfrage nicht möglich: {exc}")
                return
            GLib.idle_add(self._toast, f"Anfrage an {username} gesendet.")
            GLib.idle_add(self._reload_links)

        threading.Thread(target=worker, daemon=True).start()

    def _link_action(self, action: str, link: dict) -> None:
        partner_id = int(link.get("partnerId") or 0)

        def worker() -> None:
            try:
                if action == "confirm":
                    self.ctx.client.confirm_watch_link(partner_id)
                else:
                    self.ctx.client.unlink_watch_link(partner_id)
            except GoldfishAPIError as exc:
                GLib.idle_add(self._toast, str(exc))
                return
            GLib.idle_add(self._reload_links)

        threading.Thread(target=worker, daemon=True).start()

    def _reload_links(self) -> bool:
        for row in getattr(self, "_sync_rows", []):
            self.sync_group.remove(row)
        self._sync_rows = []
        self.sync_group.add(self.sync_placeholder)
        self.sync_placeholder.set_title("Wird geladen …")
        self.sync_placeholder.set_subtitle("")
        threading.Thread(target=self._load_links, daemon=True).start()
        return False

    # -- Anzeige ---------------------------------------------------------

    def _display_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Anzeige",
            description="Gilt nur für diesen Benutzer und wird auf dem Server gespeichert.",
        )

        row = Adw.ActionRow(
            title="Startseite und Reiterleiste",
            subtitle="Welche Bibliotheken erscheinen und in welcher Reihenfolge",
            activatable=True,
        )
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda *_: self.nav_view.push(HomePrefsPage(self.ctx, self.nav_view)))
        group.add(row)

        self.alpha_switch = Adw.SwitchRow(
            title="Buchstabenleiste anzeigen",
            subtitle="Springt in langen Listen zum Anfangsbuchstaben",
            active=self.ctx.view_prefs.alpha_sidebar(),
        )
        self.alpha_switch.connect(
            "notify::active",
            lambda row, _p: self.ctx.view_prefs.set_alpha_sidebar(row.get_active()),
        )
        group.add(self.alpha_switch)

        # Rein lokal (Gerät), nicht auf dem Server — manche Desktops (z. B.
        # Cinnamon auf Linux Mint) melden ihre Dunkelmodus-Einstellung nicht
        # über das Portal, das libadwaita sonst automatisch abfragt; ohne
        # diesen Schalter bliebe die App dort dauerhaft hell.
        scheme_options = ["system", "light", "dark"]
        scheme_labels = Gtk.StringList.new(["Systemeinstellung", "Hell", "Dunkel"])
        self.scheme_row = Adw.ComboRow(title="Erscheinungsbild", subtitle="Nur auf diesem Gerät", model=scheme_labels)
        self.scheme_row.set_selected(scheme_options.index(self.ctx.view_prefs.color_scheme()))

        def _on_scheme_changed(row: Adw.ComboRow, _param) -> None:
            value = scheme_options[row.get_selected()]
            self.ctx.view_prefs.set_color_scheme(value)
            apply_color_scheme(value)

        self.scheme_row.connect("notify::selected", _on_scheme_changed)
        group.add(self.scheme_row)
        return group

    def _toast(self, message: str) -> bool:
        root = self.get_root()
        if hasattr(root, "show_toast"):
            root.show_toast(message)
        return False


class HomePrefsPage(Adw.NavigationPage):
    """Welche Bibliotheken auf der Startseite und in der Reiterleiste
    erscheinen — zwei getrennte Listen.

    Die Trennung kommt vom Server und ist Absicht: wer eine Bibliothek nur in
    der Leiste will, soll sie nicht zwangsläufig auch auf der Startseite
    haben. Ein früherer Versuch mit einer gemeinsamen Einstellung hatte genau
    diesen Nebeneffekt.
    """

    def __init__(self, ctx, nav_view: Adw.NavigationView):
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        super().__init__(title="Startseite & Reiter", tag="home-prefs", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.toolbar_view = toolbar_view

        spinner = Gtk.Spinner(width_request=48, height_request=48)
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        wrap.append(spinner)
        toolbar_view.set_content(wrap)
        spinner.start()

        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            home = self.ctx.client.home_preferences()
            nav = self.ctx.client.nav_preferences()
        except GoldfishAPIError as exc:
            GLib.idle_add(
                lambda: (
                    self.toolbar_view.set_content(
                        Adw.StatusPage(icon_name="dialog-error-symbolic", title="Fehler", description=str(exc))
                    ),
                    False,
                )[1]
            )
            return
        GLib.idle_add(self._apply, home, nav)

    def _apply(self, home: dict, nav: dict) -> bool:
        page = Adw.PreferencesPage()

        strips = Adw.PreferencesGroup(title="Streifen auf der Startseite")
        cont = Adw.SwitchRow(title="▶ Fortsetzen", active=bool(home.get("showContinue", True)))
        cont.connect("notify::active", lambda r, _p: self._set_strips(show_continue=r.get_active()))
        strips.add(cont)
        nextup = Adw.SwitchRow(title="📺 Als nächstes", active=bool(home.get("showNextUp", True)))
        nextup.connect("notify::active", lambda r, _p: self._set_strips(show_next_up=r.get_active()))
        strips.add(nextup)
        page.add(strips)

        page.add(self._library_group("Auf der Startseite", home.get("libraries") or [], "onHome", self._set_home))
        page.add(self._library_group("In der Reiterleiste", nav.get("libraries") or [], "onNav", self._set_nav))
        local_group = self._local_group()
        if local_group is not None:
            page.add(local_group)

        self.toolbar_view.set_content(page)
        return False

    def _local_group(self) -> Adw.PreferencesGroup | None:
        """Eigene Datenträger in der Seitenleiste ein- und ausblenden.

        Eigene Gruppe, weil diese Bibliotheken nicht vom Server kommen: ihre
        Sichtbarkeit wird lokal gemerkt (`ViewPrefs`), nicht in
        `user_nav_prefs`. Ohne eingerichtete Datenträger entfällt die Gruppe
        ganz."""
        libraries = self.ctx.local.visible_libraries()
        if not libraries:
            return None
        group = Adw.PreferencesGroup(
            title="Eigene Datenträger in der Seitenleiste",
            description="Gilt nur auf diesem Rechner — der Server kennt diese Bibliotheken nicht.",
        )
        for library in libraries:
            row = Adw.SwitchRow(
                title=library.name,
                subtitle=(
                    f"Sammlung aus {len(library.merged_from)} Datenträgern"
                    if library.is_merged
                    else library.root
                ),
                active=self.ctx.view_prefs.local_in_sidebar(library.nav_key),
            )
            row.connect(
                "notify::active",
                lambda r, _p, key=library.nav_key: self._set_local(key, r.get_active()),
            )
            group.add(row)
        return group

    def _set_local(self, key: str, visible: bool) -> None:
        self.ctx.view_prefs.set_local_in_sidebar(key, visible)
        self._refresh_sidebar()

    def _library_group(self, title: str, libraries: list[dict], key: str, setter) -> Adw.PreferencesGroup:
        """Eine Schaltergruppe pro Achse (Startseite bzw. Reiterleiste).

        **Der Schlüssel heißt `libraryId`, nicht `id`** — diese beiden
        Endpunkte liefern eine eigene Zeilenform (`internal/api/nav.go`,
        `home.go`), nicht das übliche Bibliotheks-Objekt. Mit `lib["id"]`
        stirbt der Signal-Handler an einem KeyError, und weil GTK den Fehler
        nur auf die Konsole schreibt, wirkt das Umschalten schlicht wirkungslos
        — genau so gemeldet worden."""
        group = Adw.PreferencesGroup(title=title)
        if not libraries:
            group.add(Adw.ActionRow(title="Keine Bibliotheken sichtbar"))
            return group
        for library in libraries:
            library_id = int(library.get("libraryId") or library.get("id") or 0)
            if not library_id:
                continue
            row = Adw.SwitchRow(title=library.get("name") or "", active=bool(library.get(key, True)))
            row.connect("notify::active", lambda r, _p, lid=library_id: setter(lid, r.get_active()))
            group.add(row)
        return group

    def _set_strips(self, show_continue: bool | None = None, show_next_up: bool | None = None) -> None:
        self._background(lambda: self.ctx.client.set_home_strips(show_continue, show_next_up))

    def _set_home(self, library_id: int, state: bool) -> None:
        # Die Startseite liest ihre Streifen bei jedem Öffnen neu — hier ist
        # nach dem Speichern nichts weiter zu tun.
        self._background(lambda: self.ctx.client.set_home_preference(library_id, state))

    def _set_nav(self, library_id: int, state: bool) -> None:
        # Die Seitenleiste steht dauerhaft und muss aktiv nachgezogen werden,
        # sonst bliebe eine gerade abgewählte Bibliothek bis zum Neustart
        # sichtbar.
        self._background(
            lambda: self.ctx.client.set_nav_preference(library_id, state),
            after=self._refresh_sidebar,
        )

    def _refresh_sidebar(self) -> None:
        window = self.ctx.window
        if hasattr(window, "reload_libraries"):
            window.reload_libraries()

    def _background(self, call, after=None) -> None:
        def worker() -> None:
            try:
                call()
            except GoldfishAPIError as exc:
                GLib.idle_add(self._toast, f"Nicht gespeichert: {exc}")
                return
            if after is not None:
                GLib.idle_add(lambda: (after(), False)[1])

        threading.Thread(target=worker, daemon=True).start()

    def _toast(self, message: str) -> bool:
        root = self.get_root()
        if hasattr(root, "show_toast"):
            root.show_toast(message)
        return False
