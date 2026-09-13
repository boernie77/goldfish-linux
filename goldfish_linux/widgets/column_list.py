"""Tabellenansicht mit verschiebbaren, breitenverstellbaren Spalten.

Gebaut für die Musikbibliothek (Wunsch vom 2026-09-13: „so Spalten, wie auch
auf Mac, welche verschiebbar sind"), aber bewusst ohne Musikwissen — die
Ansicht bekommt eine Liste von Wörterbüchern und eine Spaltenbeschreibung.

Warum `Gtk.ColumnView` und keine Liste aus Zeilen-Widgets:

* Nur die sichtbaren Zeilen werden gebaut (Recycling). Die Musikbibliothek
  hier hat über 7000 Titel — als einzeln gebaute Zeilen kostet das mehrere
  Sekunden blockierten Hauptablauf, dieselbe Messung wie beim Album-Raster.
* Spaltenköpfe kann GTK von Haus aus ziehen (Breite) und umsortieren
  (Reihenfolge). Nachgebaut wäre beides deutlich mehr Code und schlechter zu
  bedienen.
* Ein Klick auf den Kopf sortiert, wenn die Spalte einen Sortierschlüssel
  mitbringt.

Was diese Klasse ergänzt, ist das **Merken**: Reihenfolge, Breiten und welche
Spalten überhaupt erscheinen, landen unter einem Namen („Kontext") in den
Ansichtseinstellungen und gelten beim nächsten Öffnen wieder.

Zur Bedienung: geöffnet wird mit einem Doppelklick (wie im Album-Raster);
für den einfachen Klick trägt jede Zeile am Ende eine Aktionsspalte.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gio, GLib, GObject, Gtk, Pango  # noqa: E402


@dataclass
class ColumnSpec:
    """Eine Spalte.

    `text` liefert den Zellinhalt, `sort_key` den Wert zum Sortieren (fehlt
    er, ist die Spalte nicht sortierbar). `build` ersetzt beides für Spalten
    mit Bedienelementen statt Text. `fixed` heißt: nicht abwählbar (die
    Aktionsspalte)."""

    key: str
    title: str
    width: int = 140
    expand: bool = False
    numeric: bool = False
    default: bool = True
    fixed: bool = False
    text: Callable[[dict], str] | None = None
    sort_key: Callable[[dict], Any] | None = None
    build: Callable[[dict], Gtk.Widget] | None = None


class _Row(GObject.Object):
    """Hülle um ein Wörterbuch — `Gio.ListStore` nimmt nur GObjects auf."""

    __gtype_name__ = "GfColumnRow"

    def __init__(self, data: dict) -> None:
        super().__init__()
        self.data = data


class ColumnList(Gtk.ScrolledWindow):
    def __init__(
        self,
        prefs,
        context: str,
        specs: list[ColumnSpec],
        rows: list[dict],
        on_activate: Callable[[list[dict], int], None] | None = None,
    ) -> None:
        super().__init__(vexpand=True, hexpand=True)
        self.prefs = prefs
        self.context = context
        self.specs = {spec.key: spec for spec in specs}
        self.spec_order = [spec.key for spec in specs]
        self.on_activate = on_activate
        self._columns: dict[str, Gtk.ColumnViewColumn] = {}
        self._save_timeout = 0
        # Während des Aufbaus darf nichts gespeichert werden: das Anhängen der
        # Spalten löst dieselben Signale aus wie ein Verschieben durch den
        # Benutzer und würde die gemerkte Reihenfolge mit der gerade erst
        # gelesenen überschreiben.
        self._building = True

        layout = prefs.music_column_layout(context)
        self.order = self._merge_order(layout.get("order") or [])
        self.widths: dict[str, int] = dict(layout.get("widths") or {})
        saved_visible = layout.get("visible")
        if saved_visible is None:
            self.visible = {key for key, spec in self.specs.items() if spec.default and not spec.fixed}
        else:
            self.visible = {key for key in saved_visible if key in self.specs and not self.specs[key].fixed}

        self.store = Gio.ListStore.new(_Row)
        self.view = Gtk.ColumnView(
            show_column_separators=True,
            show_row_separators=True,
            reorderable=True,
            single_click_activate=False,
            vexpand=True,
        )
        self.sort_model = Gtk.SortListModel.new(self.store, self.view.get_sorter())
        self.view.set_model(Gtk.NoSelection.new(self.sort_model))
        self.view.connect("activate", self._on_activate)

        for key in self.order:
            spec = self.specs[key]
            if spec.fixed or key in self.visible:
                self._append_column(spec)

        self.set_child(self.view)
        self.set_rows(rows)

        self._building = False
        self.view.get_columns().connect("items-changed", lambda *_: self._schedule_save())

    # -- Daten -----------------------------------------------------------

    def set_rows(self, rows: list[dict]) -> None:
        self.store.remove_all()
        for row in rows:
            self.store.append(_Row(row))

    def visible_rows(self) -> list[dict]:
        """Die Zeilen in der gerade angezeigten (also ggf. sortierten)
        Reihenfolge — das ist die Warteschlange, wenn man eine Zeile
        startet."""
        return [self.sort_model.get_item(i).data for i in range(self.sort_model.get_n_items())]

    def _on_activate(self, _view: Gtk.ColumnView, position: int) -> None:
        if self.on_activate is not None:
            self.on_activate(self.visible_rows(), position)

    # -- Spalten ---------------------------------------------------------

    def _merge_order(self, saved: list) -> list[str]:
        """Gemerkte Reihenfolge, ergänzt um Spalten, die es beim letzten Mal
        noch nicht gab. Feste Spalten (die Aktionen) stehen immer ganz
        rechts — verschöbe man sie in die Mitte, stünden Knöpfe mitten im
        Text."""
        order = [key for key in saved if key in self.specs and not self.specs[key].fixed]
        order.extend(key for key in self.spec_order if key not in order and not self.specs[key].fixed)
        order.extend(key for key in self.spec_order if self.specs[key].fixed)
        return order

    def _append_column(self, spec: ColumnSpec) -> None:
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup, spec)
        factory.connect("bind", self._on_bind, spec)
        column = Gtk.ColumnViewColumn(title=spec.title, factory=factory, expand=spec.expand)
        column.set_resizable(True)
        # Eine dehnbare Spalte bekommt nur dann eine feste Breite, wenn der
        # Benutzer sie selbst gezogen hat — sonst nähme die Vorgabe ihr das
        # Dehnen, und rechts bliebe im breiten Fenster eine leere Fläche.
        width = self.widths.get(spec.key)
        if width is None and not spec.expand:
            width = spec.width
        if width:
            column.set_fixed_width(int(width))
        if spec.sort_key is not None:
            column.set_sorter(Gtk.CustomSorter.new(_compare, spec))
        column.connect("notify::fixed-width", lambda *_: self._schedule_save())
        self.view.append_column(column)
        self._columns[spec.key] = column

    def set_column_visible(self, key: str, visible: bool) -> None:
        """Spalte ein-/ausblenden, ohne die Liste neu aufzubauen (die
        Bildlaufstelle bleibt dadurch stehen). Eine wieder eingeblendete
        Spalte erscheint hinten — von dort lässt sie sich an ihren Platz
        ziehen."""
        spec = self.specs.get(key)
        if spec is None or spec.fixed:
            return
        column = self._columns.get(key)
        if visible and column is None:
            self.visible.add(key)
            if key in self.order:
                self.order.remove(key)
            self.order.append(key)
            self._append_column(spec)
            self._move_fixed_columns_last()
        elif not visible and column is not None:
            self.visible.discard(key)
            self.view.remove_column(column)
            del self._columns[key]
        self._schedule_save()

    def _move_fixed_columns_last(self) -> None:
        """Aktionsspalte wieder ans Ende holen, nachdem eine Spalte
        nachträglich angehängt wurde."""
        for key, spec in self.specs.items():
            column = self._columns.get(key)
            if spec.fixed and column is not None:
                self.view.remove_column(column)
                self.view.append_column(column)

    # -- Merken ----------------------------------------------------------

    def _schedule_save(self) -> None:
        # Beim Ziehen einer Spaltenbreite feuert das Signal für jedes Pixel —
        # gespeichert wird erst, wenn die Maus einen Moment ruht.
        if self._building:
            return
        if self._save_timeout:
            GLib.source_remove(self._save_timeout)
        self._save_timeout = GLib.timeout_add(400, self._save)

    def _save(self) -> bool:
        self._save_timeout = 0
        columns = self.view.get_columns()
        shown: list[str] = []
        for i in range(columns.get_n_items()):
            column = columns.get_item(i)
            for key, candidate in self._columns.items():
                if candidate is column:
                    shown.append(key)
                    break
        # Ausgeblendete Spalten behalten ihren Platz hinter den sichtbaren,
        # damit ein späteres Einblenden nicht bei null anfängt.
        shown = [key for key in shown if not self.specs[key].fixed]
        self.order = shown + [key for key in self.order if key not in shown and not self.specs[key].fixed]
        for key, column in self._columns.items():
            width = column.get_fixed_width()
            if width > 0:
                self.widths[key] = width
        self.prefs.set_music_column_layout(
            self.context,
            order=self.order,
            widths=self.widths,
            visible=sorted(self.visible),
        )
        return False

    # -- Zellen ----------------------------------------------------------

    def _on_setup(self, _factory, list_item: Gtk.ListItem, spec: ColumnSpec) -> None:
        if spec.build is not None:
            holder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2, valign=Gtk.Align.CENTER)
            list_item.set_child(holder)
            return
        label = Gtk.Label(
            xalign=1.0 if spec.numeric else 0.0,
            ellipsize=Pango.EllipsizeMode.END,
            valign=Gtk.Align.CENTER,
            single_line_mode=True,
        )
        if spec.numeric:
            label.add_css_class("numeric")
        list_item.set_child(label)

    def _on_bind(self, _factory, list_item: Gtk.ListItem, spec: ColumnSpec) -> None:
        data = list_item.get_item().data
        child = list_item.get_child()
        if spec.build is not None:
            while (old := child.get_first_child()) is not None:
                child.remove(old)
            child.append(spec.build(data))
            return
        child.set_label(spec.text(data) if spec.text else "")


def _compare(a: _Row, b: _Row, spec: ColumnSpec) -> int:
    left, right = spec.sort_key(a.data), spec.sort_key(b.data)
    if left == right:
        return 0
    return -1 if left < right else 1
