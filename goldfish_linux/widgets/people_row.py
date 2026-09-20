"""Schauspieler-Reihe für die aufgegliederte Trefferanzeige.

Browser-Vorbild: `internal/webassets/web/cards.js`
(`appendSearchResultCards`/`renderSearchPersonCard`) — bei jeder Suche (egal
ob global/Startseite oder auf eine Bibliothek eingeschränkt) steht diese Reihe
ÜBER dem eigentlichen Treffer-Raster, wenn die Personensuche
(`GET /api/search/people`) mindestens einen Treffer gebracht hat. Server seit
1.4.22: `/api/items?search=` durchsucht nur noch den Titel, Besetzungstreffer
kommen ausschließlich von hier.

Anders als `CastStrip` lädt diese Reihe NICHT selbst nach — die Personenliste
kommt schon fertig vom Aufrufer (der sie ohnehin im selben Ladevorgang wie die
Item-Suche abfragt), diese Klasse zeigt nur an."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from ..api import GoldfishClient  # noqa: E402
from .poster import load_poster_async  # noqa: E402

_PORTRAIT = 78

_CSS = b"""
.gf-people-photo {
  background-color: alpha(@window_fg_color, 0.1);
  border-radius: 999px;
}
.gf-people-name { font-size: 0.85rem; font-weight: 500; }
.gf-people-label { font-size: 0.78rem; opacity: 0.6; }
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


class PeopleSearchRow(Gtk.Box):
    """Waagerecht scrollbare Reihe anklickbarer Schauspieler-Karten.

    Wird vom Aufrufer nur eingehängt, wenn `people` nicht leer ist — ein
    leerer Abschnitt mit Überschrift wäre irritierend (dieselbe Regel wie bei
    `CastStrip`)."""

    def __init__(
        self,
        client: GoldfishClient,
        people: list[dict],
        on_person: Callable[[dict], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        _ensure_css()
        self.client = client
        self.on_person = on_person

        heading = Gtk.Label(label="Schauspieler", xalign=0)
        heading.add_css_class("heading")
        self.append(heading)

        self.row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14, halign=Gtk.Align.START)
        scroller = Gtk.ScrolledWindow(
            child=self.row,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
        )
        self.append(scroller)

        for person in people:
            self.row.append(self._build_card(person))

    def _build_card(self, person: dict) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, width_request=_PORTRAIT + 18)

        photo = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        photo.set_size_request(_PORTRAIT, _PORTRAIT)
        photo.add_css_class("gf-people-photo")
        # Ohne HIDDEN schneidet der Eckenradius das Bild nicht zu.
        photo.set_overflow(Gtk.Overflow.HIDDEN)
        photo.set_halign(Gtk.Align.CENTER)
        box.append(photo)

        name = Gtk.Label(
            label=person.get("name") or "",
            wrap=True,
            lines=2,
            max_width_chars=12,
            justify=Gtk.Justification.CENTER,
        )
        name.add_css_class("gf-people-name")
        box.append(name)

        label = Gtk.Label(label="Schauspieler")
        label.add_css_class("gf-people-label")
        box.append(label)

        profile_path = person.get("profilePath")
        if profile_path:
            image_url = self.client.tmdb_image_url(profile_path, size="w185")
            load_poster_async(photo, self.client, image_url)

        tmdb_id = person.get("tmdbId")
        if tmdb_id and self.on_person:
            click = Gtk.GestureClick()
            click.connect("released", lambda *_a, p=person: self.on_person(p))
            photo.add_controller(click)
            photo.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
            box.set_tooltip_text(f"Alles mit {person.get('name')}")
        return box
