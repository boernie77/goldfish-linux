"""Kachelraster auf Basis von Gtk.GridView.

Bewusst GridView und nicht Gtk.FlowBox: FlowBox erzeugt für JEDES Element ein
Widget, GridView nur für die sichtbaren und verwendet sie beim Scrollen wieder.
Bei den Größenordnungen dieser Bibliotheken ist das der Unterschied zwischen
flüssig und unbenutzbar — eine Musikbibliothek bringt 2717 Alben mit, ein
Serienordner 267 Einträge, und jede Kachel würde sonst sofort ein Poster
nachladen wollen.

Ordner und Items liegen im selben Modell und damit im selben Raster, genau wie
im Browser: die Bibliothekswurzel zeigt Ordnerkacheln, ein Unterordner die
Videos, und beides kann gemischt auftreten. Weil `Gtk.SignalListItemFactory`
beim Bauen eines Widgets noch nicht weiß, welche Zeile es später zeigt, hält
jede Kachelposition beide Varianten in einem Gtk.Stack und schaltet beim
Belegen um.
"""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GObject, Gtk  # noqa: E402

from ..api import GoldfishClient  # noqa: E402
from .card import CARD_WIDTH, CardWidget, FolderCardWidget, ensure_card_css  # noqa: E402


class GridRow(GObject.Object):
    """Eine Zeile im Modell — entweder ein Item oder ein Ordner."""

    __gtype_name__ = "GoldfishGridRow"

    def __init__(self, payload: dict, is_folder: bool) -> None:
        super().__init__()
        self.payload = payload
        self.is_folder = is_folder


class CardGrid(Gtk.ScrolledWindow):
    """Scrollbares Kachelraster. Nach dem Erzeugen mit `set_content()` füllen."""

    def __init__(
        self,
        client: GoldfishClient,
        kind: str,
        on_item: Callable[[dict], None] | None = None,
        on_folder: Callable[[dict], None] | None = None,
        on_toggle_watched: Callable[[dict, bool], None] | None = None,
        on_toggle_favorite: Callable[[dict, bool], None] | None = None,
    ) -> None:
        super().__init__(vexpand=True, hexpand=True)
        ensure_card_css()
        self.client = client
        self.kind = kind
        self.on_item = on_item
        self.on_folder = on_folder
        self.on_toggle_watched = on_toggle_watched
        self.on_toggle_favorite = on_toggle_favorite

        self.store = Gio.ListStore.new(GridRow)
        # Kein Auswahlmodell im Wortsinn: die Kacheln reagieren selbst auf
        # Klicks. Gtk.NoSelection verhindert den Auswahlrahmen, der sonst bei
        # jedem Klick auf einer Kachel hängen bliebe.
        selection = Gtk.NoSelection.new(self.store)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)
        factory.connect("unbind", self._on_unbind)

        self.grid = Gtk.GridView(
            model=selection,
            factory=factory,
            max_columns=12,
            min_columns=1,
            vexpand=True,
            single_click_activate=False,
        )
        self.grid.add_css_class("navigation-sidebar")  # entfernt den Zellrahmen
        self.grid.set_margin_start(12)
        self.grid.set_margin_end(12)
        self.grid.set_margin_top(12)
        self.grid.set_margin_bottom(24)
        self.set_child(self.grid)

    # -- Inhalt ----------------------------------------------------------

    def set_content(self, folders: list[dict], items: list[dict]) -> None:
        """Ersetzt den Rasterinhalt. Ordner zuerst, dann Videos — dieselbe
        Reihenfolge wie im Browser."""
        rows = [GridRow(f, True) for f in folders] + [GridRow(i, False) for i in items]
        # splice() statt remove_all()+append(): ein einziger Modellwechsel,
        # damit der GridView nur einmal neu aufbaut statt pro Element.
        self.store.splice(0, self.store.get_n_items(), rows)
        # Nach oben scrollen: bei einer neuen Suche im selben Raster würde die
        # alte Scrollposition sonst mitten in der neuen Trefferliste stehen.
        # Über das Vadjustment statt grid.scroll_to() — das ist über
        # GTK-4.x-Versionen hinweg stabil.
        adj = self.get_vadjustment()
        if adj is not None:
            adj.set_value(0)

    # -- Factory ---------------------------------------------------------

    def _on_setup(self, _factory, list_item: Gtk.ListItem) -> None:
        stack = Gtk.Stack(hhomogeneous=False, vhomogeneous=False)
        stack.set_size_request(CARD_WIDTH, -1)
        card = CardWidget(
            self.client,
            self.kind,
            on_activate=self._activate_item,
            on_toggle_watched=self.on_toggle_watched,
            on_toggle_favorite=self.on_toggle_favorite,
        )
        folder_card = FolderCardWidget(self.client, self.kind, on_activate=self._activate_folder)
        stack.add_named(card, "item")
        stack.add_named(folder_card, "folder")
        list_item.set_child(stack)

    def _on_bind(self, _factory, list_item: Gtk.ListItem) -> None:
        row: GridRow = list_item.get_item()
        stack: Gtk.Stack = list_item.get_child()
        if row.is_folder:
            stack.set_visible_child_name("folder")
            stack.get_child_by_name("folder").bind(row.payload)
        else:
            stack.set_visible_child_name("item")
            stack.get_child_by_name("item").bind(row.payload)

    def _on_unbind(self, _factory, list_item: Gtk.ListItem) -> None:
        # Wichtig beim Recycling: ein noch laufender Poster-Request wird hier
        # entwertet, damit er nicht in der nächsten Belegung landet.
        stack: Gtk.Stack = list_item.get_child()
        stack.get_child_by_name("item").unbind()
        stack.get_child_by_name("folder").unbind()

    # -- Klicks weiterreichen --------------------------------------------

    def _activate_item(self, item: dict) -> None:
        if self.on_item:
            self.on_item(item)

    def _activate_folder(self, folder: dict) -> None:
        if self.on_folder:
            self.on_folder(folder)
