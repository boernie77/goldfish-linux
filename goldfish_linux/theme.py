"""Hell/Dunkel-Umschaltung — eigenes, winziges Modul statt in `app.py` oder
`settings_page.py`, weil beide sonst zirkulär voneinander importieren müssten.

Lokale, geräteseitige Einstellung (siehe `config.ViewPrefs.color_scheme`):
manche Desktops (z. B. Cinnamon auf Linux Mint) melden ihre
Dunkelmodus-Einstellung nicht über das Portal, das libadwaita sonst
automatisch abfragt — ohne diesen expliziten Schalter bliebe die App dort
für immer hell, unabhängig vom System-Theme.
"""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

_SCHEME_MAP = {
    "system": Adw.ColorScheme.DEFAULT,
    "light": Adw.ColorScheme.FORCE_LIGHT,
    "dark": Adw.ColorScheme.FORCE_DARK,
}


def apply_color_scheme(value: str) -> None:
    Adw.StyleManager.get_default().set_color_scheme(_SCHEME_MAP.get(value, Adw.ColorScheme.DEFAULT))
