"""Verwaltungsseiten für Administratoren: Scan starten/beobachten und das
Aktivitätsprotokoll des Servers lesen."""

from __future__ import annotations

import threading
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..api import GoldfishAPIError  # noqa: E402

_CATEGORIES = [("Alle", ""), ("Anmeldung", "auth"), ("Wiedergabe", "playback"), ("Verwaltung", "admin"), ("Jobs", "job")]
_PAGE = 100


def _format_time(raw: str) -> str:
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone()
        return stamp.strftime("%d.%m.%Y %H:%M:%S")
    except (ValueError, AttributeError):
        return raw or ""


class ScanPage(Adw.NavigationPage):
    """Bibliotheken scannen und den Fortschritt verfolgen."""

    def __init__(self, ctx, nav_view: Adw.NavigationView):
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        super().__init__(title="Scan", tag="admin-scan", child=toolbar)
        self.ctx = ctx
        self._alive = True
        self.connect("hiding", lambda *_: setattr(self, "_alive", False))

        page = Adw.PreferencesPage()
        self.status_group = Adw.PreferencesGroup(title="Status")
        self.status_row = Adw.ActionRow(title="Kein Scan aktiv")
        self.status_group.add(self.status_row)
        self.cancel_button = Gtk.Button(label="Abbrechen", valign=Gtk.Align.CENTER, visible=False)
        self.cancel_button.connect("clicked", lambda *_: self._cancel())
        self.status_row.add_suffix(self.cancel_button)
        page.add(self.status_group)

        libs = Adw.PreferencesGroup(title="Bibliotheken scannen")
        all_row = Adw.ActionRow(title="Alle Bibliotheken", subtitle="Nacheinander, nur Neues und Geändertes", activatable=True)
        all_row.add_suffix(Gtk.Image(icon_name="view-refresh-symbolic"))
        all_row.connect("activated", lambda *_: self._start(None))
        libs.add(all_row)
        for lib in sorted(ctx.libraries_by_id.values(), key=lambda x: (x.get("name") or "").lower()):
            row = Adw.ActionRow(title=lib.get("name") or "?", activatable=True)
            row.add_suffix(Gtk.Image(icon_name="view-refresh-symbolic"))
            row.connect("activated", lambda _r, lid=int(lib["id"]): self._start(lid))
            libs.add(row)
        page.add(libs)
        toolbar.set_content(page)
        self._start_polling()

    def _toast(self, message: str) -> bool:
        root = self.get_root()
        if hasattr(root, "show_toast"):
            root.show_toast(message)
        return False

    def _start(self, library_id: int | None) -> None:
        def worker() -> None:
            try:
                self.ctx.client.start_scan(library_id)
            except GoldfishAPIError as exc:
                GLib.idle_add(self._toast, f"Scan nicht gestartet: {exc}")
                return
            GLib.idle_add(self._toast, "Scan gestartet")

        threading.Thread(target=worker, daemon=True).start()

    def _cancel(self) -> None:
        threading.Thread(target=lambda: self._safe(self.ctx.client.cancel_scan), daemon=True).start()

    @staticmethod
    def _safe(call) -> None:
        try:
            call()
        except GoldfishAPIError:
            pass

    def _start_polling(self) -> None:
        self._alive = True

        def tick() -> bool:
            if not self._alive:
                return False

            def worker() -> None:
                try:
                    status = self.ctx.client.scan_status()
                except GoldfishAPIError:
                    return
                GLib.idle_add(self._apply_status, status)

            threading.Thread(target=worker, daemon=True).start()
            return True

        tick()
        GLib.timeout_add_seconds(2, tick)

    def _apply_status(self, st: dict) -> bool:
        running = bool(st.get("running") or st.get("batchActive"))
        self.cancel_button.set_visible(running)
        if running:
            total = st.get("total") or 0
            done = st.get("done") or 0
            self.status_row.set_title(f"Scan läuft: {done}/{total}" if total else "Scan läuft …")
            self.status_row.set_subtitle(st.get("current") or "")
        else:
            summary = st.get("lastSummary") or {}
            new, removed = st.get("new") or 0, st.get("removed") or 0
            error = st.get("lastError") or ""
            self.status_row.set_title("Kein Scan aktiv")
            if error:
                self.status_row.set_subtitle(f"Letzter Fehler: {error}")
            elif summary or new or removed:
                self.status_row.set_subtitle(f"Zuletzt: {new} neu, {removed} entfernt")
            else:
                self.status_row.set_subtitle("")
        return False


class ActivityLogPage(Adw.NavigationPage):
    """Das Protokoll des Servers (Anmeldungen, Wiedergaben, Verwaltung, Jobs)."""

    def __init__(self, ctx, nav_view: Adw.NavigationView):
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        super().__init__(title="Protokoll", tag="admin-log", child=toolbar)
        self.ctx = ctx
        self.category = ""
        self.last_id = 0
        self.exhausted = False

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
        self.filter = Gtk.DropDown.new_from_strings([label for label, _ in _CATEGORIES])
        self.filter.connect("notify::selected", lambda *_: self._on_filter())
        outer.append(self.filter)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        self.more = Gtk.Button(label="Ältere laden")
        self.more.connect("clicked", lambda *_: self._load())
        scroll = Gtk.ScrolledWindow(vexpand=True)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        inner.append(self.listbox)
        inner.append(self.more)
        scroll.set_child(inner)
        outer.append(scroll)
        toolbar.set_content(outer)
        self._load()

    def _on_filter(self) -> None:
        self.category = _CATEGORIES[self.filter.get_selected()][1]
        self.last_id = 0
        self.exhausted = False
        child = self.listbox.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.listbox.remove(child)
            child = nxt
        self._load()

    def _load(self) -> None:
        category, before = self.category, self.last_id

        def worker() -> None:
            try:
                entries = self.ctx.client.activity_log(category, before, _PAGE)
            except GoldfishAPIError as exc:
                GLib.idle_add(self._error, str(exc))
                return
            GLib.idle_add(self._append, category, entries)

        threading.Thread(target=worker, daemon=True).start()

    def _error(self, message: str) -> bool:
        self.listbox.append(Adw.ActionRow(title="Protokoll nicht abrufbar", subtitle=message))
        return False

    def _append(self, category: str, entries: list[dict]) -> bool:
        if category != self.category:
            return False  # Filter wurde inzwischen gewechselt
        for entry in entries:
            who = entry.get("username") or "System"
            device = entry.get("device") or ""
            row = Adw.ActionRow(
                title=f"{entry.get('action') or ''}: {entry.get('detail') or ''}".strip(": "),
                subtitle=f"{_format_time(entry.get('at') or '')} · {who}" + (f" · {device}" if device else ""),
            )
            row.set_title_lines(3)
            self.listbox.append(row)
            self.last_id = int(entry.get("id") or self.last_id)
        self.exhausted = len(entries) < _PAGE
        self.more.set_visible(not self.exhausted)
        return False
