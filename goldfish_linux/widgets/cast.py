"""Besetzungsleiste: waagerecht scrollbare Reihe runder Portraits.

Anklickbar — ein Klick führt zu allem, worin diese Person mitspielt. Die Daten
kommen von `GET /api/metadata/{id}/cast`, das auf der **Metadaten-ID** arbeitet
und nicht auf der Item-ID (dieselbe Konvention kennt die Android-App). Bei
Episoden liefert der Server automatisch den Hauptcast der Serie plus die Gäste
der Folge.
"""

from __future__ import annotations

import threading
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from ..api import GoldfishAPIError, GoldfishClient  # noqa: E402
from .poster import load_poster_async  # noqa: E402

_PORTRAIT = 78

_CSS = b"""
.gf-cast-photo {
  background-color: alpha(@window_fg_color, 0.1);
  border-radius: 999px;
}
.gf-cast-name { font-size: 0.85rem; font-weight: 500; }
.gf-cast-role { font-size: 0.78rem; opacity: 0.6; }
"""

_css_loaded = False


def _ensure_css() -> None:
    global _css_loaded
    if _css_loaded:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_loaded = True


class CastStrip(Gtk.Box):
    """Lädt die Besetzung selbst nach und blendet sich aus, wenn es keine gibt
    — ein leerer Abschnitt mit Überschrift wäre irritierend."""

    def __init__(
        self,
        client: GoldfishClient,
        metadata_id: int,
        on_person: Callable[[dict], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        _ensure_css()
        self.client = client
        self.metadata_id = metadata_id
        self.on_person = on_person
        self.set_visible(False)

        heading = Gtk.Label(label="Besetzung", xalign=0)
        heading.add_css_class("heading")
        self.append(heading)

        # Wie bei den Streifen der Startseite: ohne halign verteilt die Box
        # ihren Restplatz zwischen den Portraits und reißt sie auseinander.
        self.row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14, halign=Gtk.Align.START)
        scroller = Gtk.ScrolledWindow(
            child=self.row,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
        )
        self.append(scroller)

        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            cast = self.client.cast(self.metadata_id)
        except GoldfishAPIError:
            cast = []
        GLib.idle_add(self._apply, cast)

    def _apply(self, cast: list[dict]) -> bool:
        if not cast:
            return False
        for member in cast:
            self.row.append(self._build_member(member))
        self.set_visible(True)
        return False

    def _build_member(self, member: dict) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, width_request=_PORTRAIT + 18)

        photo = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        photo.set_size_request(_PORTRAIT, _PORTRAIT)
        photo.add_css_class("gf-cast-photo")
        # Ohne HIDDEN schneidet der Eckenradius das Bild nicht zu, das Portrait
        # bliebe eckig.
        photo.set_overflow(Gtk.Overflow.HIDDEN)
        photo.set_halign(Gtk.Align.CENTER)
        box.append(photo)

        name = Gtk.Label(
            label=member.get("name") or "",
            wrap=True,
            lines=2,
            max_width_chars=12,
            justify=Gtk.Justification.CENTER,
        )
        name.add_css_class("gf-cast-name")
        box.append(name)

        role = member.get("character") or ""
        if role:
            role_label = Gtk.Label(
                label=role,
                wrap=True,
                lines=2,
                max_width_chars=12,
                justify=Gtk.Justification.CENTER,
            )
            role_label.add_css_class("gf-cast-role")
            box.append(role_label)

        tmdb_id = member.get("tmdbId")
        if tmdb_id:
            load_poster_async(photo, self.client, self.client.person_profile_path(int(tmdb_id)))
            if self.on_person:
                click = Gtk.GestureClick()
                click.connect("released", lambda *_a, m=member: self.on_person(m))
                photo.add_controller(click)
                # Zeigefinger als Hinweis, dass das Portrait anklickbar ist.
                photo.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
                box.set_tooltip_text(f"Alles mit {member.get('name')}")
        return box
