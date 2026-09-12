"""Buchstabenleiste am rechten Rand.

Filtert die gezeigten Kacheln auf einen Anfangsbuchstaben — genau wie im
Browser, wo die Leiste ebenfalls filtert und nicht bloß scrollt. Ein zweiter
Klick auf denselben Buchstaben hebt den Filter auf.
"""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

_LETTERS = ["#"] + [chr(c) for c in range(ord("A"), ord("Z") + 1)]

_CSS = b"""
.gf-alpha-bar { padding: 2px 4px; }
.gf-alpha-bar button {
  min-width: 22px;
  min-height: 17px;
  padding: 0;
  font-size: 0.72rem;
  opacity: 0.55;
}
.gf-alpha-bar button:hover { opacity: 1; }
.gf-alpha-active { opacity: 1; font-weight: bold; }
"""

_css_loaded = False


def _ensure_css() -> None:
    global _css_loaded
    if _css_loaded:
        return
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk

    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_loaded = True


def first_letter(text: str) -> str:
    """Der Buchstabe, unter dem ein Titel einsortiert wird. Alles, was nicht
    mit einem Buchstaben beginnt (Zahlen, Sonderzeichen), landet unter '#' —
    dieselbe Einteilung wie im Browser."""
    for char in (text or "").strip():
        upper = char.upper()
        if "A" <= upper <= "Z":
            return upper
        return "#"
    return "#"


class AlphaSidebar(Gtk.Box):
    def __init__(self, on_select: Callable[[str | None], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0, valign=Gtk.Align.CENTER)
        _ensure_css()
        self.add_css_class("gf-alpha-bar")
        self.on_select = on_select
        self.active: str | None = None
        self._buttons: dict[str, Gtk.Button] = {}

        for letter in _LETTERS:
            button = Gtk.Button(label=letter, has_frame=False)
            button.connect("clicked", lambda _b, l=letter: self._clicked(l))
            self._buttons[letter] = button
            self.append(button)

    def _clicked(self, letter: str) -> None:
        # Zweiter Klick auf denselben Buchstaben hebt den Filter auf.
        self.active = None if self.active == letter else letter
        for key, button in self._buttons.items():
            if key == self.active:
                button.add_css_class("gf-alpha-active")
            else:
                button.remove_css_class("gf-alpha-active")
        self.on_select(self.active)

    def reset(self) -> None:
        if self.active is None:
            return
        self.active = None
        for button in self._buttons.values():
            button.remove_css_class("gf-alpha-active")
