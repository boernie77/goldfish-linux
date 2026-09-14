"""Diagnose-Anzeige im Wiedergabefenster (Vorbild: Plex' Wiedergabe-Info).

Zeigt, was beim Abspielen gerade wirklich passiert: ob der Server umwandelt,
wie weit die Umwandlung der Wiedergabe vorausläuft, in welcher Auflösung die
Quelle vorliegt und was davon tatsächlich ankommt. Gedacht für genau die
Frage "warum stockt das".

**Ein Aufklappfenster, kein Dialog.** Ein modaler Dialog legt sich hinter das
Wiedergabefenster und lässt die App eingefroren wirken — in 0.1.21 genau so
passiert und dort behoben.

**Was hier bewusst FEHLT und warum:** der Puffer des Abspielers, ausgelassene
Bilder und die laufende Netzwerkrate. Der Player benutzt `Gtk.MediaFile`,
das GStreamer vollständig kapselt: `Gtk.MediaStream` kennt ausschliesslich
`timestamp`, `duration`, `seekable`, `playing`, `volume` und die Auflösung
über das Paintable — keine Puffer-, Bild- oder Bitratenwerte, und an die
Pipeline dahinter kommt man von außen nicht heran. Diese drei Werte gäbe es
erst mit einem Umbau auf eine eigene GStreamer-Pipeline (`playbin3` + eigene
Bus-Überwachung), also mit einem Austausch des kompletten Abspielkerns.
Ersatz bis dahin: der Server-Vorlauf sagt bei serverseitiger Umwandlung
dasselbe aus (läuft er gegen null, stockt es gleich), und die Netzwerkrate
gibt es als Messung auf Knopfdruck.
"""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

# Schwellen für die Einfärbung des Server-Vorlaufs, in Sekunden. Unterhalb von
# WARN wird es knapp, unterhalb von BAD stockt die Wiedergabe gleich.
_AHEAD_WARN = 20.0
_AHEAD_BAD = 5.0

_CSS = b"""
.gf-stats-section {
  font-weight: bold;
  font-size: 0.85rem;
  opacity: 0.6;
  margin-top: 6px;
}
.gf-stats-key { opacity: 0.7; }
.gf-stats-value { font-family: monospace; }
.gf-stats-good { color: #57d16c; }
.gf-stats-warn { color: #f5c211; }
.gf-stats-bad { color: #ff6b6b; }
"""

_css_loaded = False


def _ensure_css() -> None:
    """Lädt das Stylesheet einmal pro Prozess (Muster aus widgets/card.py)."""
    global _css_loaded
    if _css_loaded:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return  # kein Display (z. B. im Test) — die Anzeige geht auch ungestylt
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_loaded = True


# Aufbau der Anzeige: (Schlüssel, Beschriftung) je Zeile, None trennt einen
# Abschnitt ab. Der Player füttert die Schlüssel, diese Datei bestimmt die
# Reihenfolge.
_LAYOUT: list[tuple[str, str] | tuple[None, str]] = [
    (None, "Wiedergabe"),
    ("mode", "Modus"),
    ("ahead", "Server-Vorlauf"),
    ("position", "Position"),
    (None, "Bild"),
    ("source", "Quelle"),
    ("delivered", "Ausgeliefert"),
    (None, "Ton"),
    ("audio", "Tonspur"),
    (None, "Netzwerk"),
    ("throughput", "Messung"),
]


class PlaybackStatsPopover(Gtk.Popover):
    """Aufklappfenster mit den Wiedergabe-Kennwerten.

    `on_measure` wird beim Druck auf "Messen" gerufen; der Aufrufer misst im
    Hintergrund und meldet das Ergebnis über `set_value("throughput", …)`
    zurück — hier wird bewusst nicht selbst auf das Netz zugegriffen, damit
    dieses Widget ohne API-Zugang testbar bleibt."""

    def __init__(self, on_measure: Callable[[], None] | None = None) -> None:
        super().__init__()
        _ensure_css()
        self._values: dict[str, Gtk.Label] = {}
        self._on_measure = on_measure

        grid = Gtk.Grid(column_spacing=14, row_spacing=4, margin_top=10,
                        margin_bottom=10, margin_start=12, margin_end=12)
        row = 0
        for key, label in _LAYOUT:
            if key is None:
                heading = Gtk.Label(label=label, xalign=0.0)
                heading.add_css_class("gf-stats-section")
                grid.attach(heading, 0, row, 2, 1)
                row += 1
                continue
            key_label = Gtk.Label(label=label, xalign=0.0)
            key_label.add_css_class("gf-stats-key")
            grid.attach(key_label, 0, row, 1, 1)

            value = Gtk.Label(label="—", xalign=0.0, selectable=True)
            value.add_css_class("gf-stats-value")
            self._values[key] = value

            if key == "throughput":
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                self.measure_button = Gtk.Button(label="Messen")
                self.measure_button.connect("clicked", self._on_measure_clicked)
                box.append(value)
                box.append(self.measure_button)
                grid.attach(box, 1, row, 1, 1)
            else:
                grid.attach(value, 1, row, 1, 1)
            row += 1

        self.set_child(grid)

    def _on_measure_clicked(self, _button: Gtk.Button) -> None:
        if self._on_measure is None:
            return
        self.measure_button.set_sensitive(False)
        self.set_value("throughput", "misst …")
        self._on_measure()

    def measurement_done(self, text: str) -> None:
        """Ergebnis einer Durchsatzmessung eintragen und den Knopf freigeben."""
        self.set_value("throughput", text)
        self.measure_button.set_sensitive(True)

    def set_value(self, key: str, text: str, level: str | None = None) -> None:
        """Einen Wert setzen. `level` ∈ {good, warn, bad} färbt ihn ein."""
        label = self._values.get(key)
        if label is None:
            return
        label.set_label(text)
        for cls in ("gf-stats-good", "gf-stats-warn", "gf-stats-bad"):
            label.remove_css_class(cls)
        if level:
            label.add_css_class(f"gf-stats-{level}")

    def update(self, values: dict[str, str]) -> None:
        """Mehrere Werte auf einmal setzen (ohne Einfärbung)."""
        for key, text in values.items():
            self.set_value(key, text)


def format_ahead(seconds: float | None, done: bool, transcoding: bool) -> tuple[str, str | None]:
    """Beschriftung und Warnstufe für den Server-Vorlauf.

    Bei direkter Wiedergabe gibt es keine Umwandlung und damit keinen
    Vorlauf — das ist kein Mangel, sondern der bessere Fall, deshalb ein
    klarer Text statt eines leeren Werts."""
    if not transcoding:
        return "entfällt (direkte Wiedergabe)", None
    if seconds is None:
        # Der Stand kommt aus einem Abruf beim Server, der im Hintergrund
        # läuft — beim Aufklappen ist er noch nicht da. Ein "—" laese sich
        # nicht von "es gibt keinen Vorlauf" unterscheiden.
        return "wird ermittelt …", None
    if done:
        return f"+{seconds:.0f} s (Umwandlung fertig)", "good"
    if seconds < _AHEAD_BAD:
        return f"+{seconds:.0f} s", "bad"
    if seconds < _AHEAD_WARN:
        return f"+{seconds:.0f} s", "warn"
    return f"+{seconds:.0f} s", "good"


def format_bitrate(bits_per_second: float) -> str:
    """Bit/s menschenlesbar — Netzwerkraten zählen in Zehnerpotenzen."""
    if bits_per_second >= 1e9:
        return f"{bits_per_second / 1e9:.2f} Gbit/s"
    if bits_per_second >= 1e6:
        return f"{bits_per_second / 1e6:.1f} Mbit/s"
    if bits_per_second >= 1e3:
        return f"{bits_per_second / 1e3:.0f} kbit/s"
    return f"{bits_per_second:.0f} bit/s"
