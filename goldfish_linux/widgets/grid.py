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
from .card import (  # noqa: E402
    CARD_WIDTH,
    AlbumCardWidget,
    CardWidget,
    FolderCardWidget,
    LocalCardWidget,
    ensure_card_css,
)


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
        # Welche Kachel zeigt gerade welches Item? Damit lässt sich der
        # Gesehen-/Favoriten-Zustand einer EINZELNEN Kachel nachziehen, wenn er
        # woanders geändert wurde (Info-Karte) — ohne das Modell anzufassen
        # (Modellwechsel in einem sichtbaren Raster sind in dieser App eine
        # bekannte Fehlerquelle, siehe CLAUDE.md).
        self._card_by_item_id: dict[int, object] = {}

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
            # Großzügig: der GridView teilt seine Breite auf GENAU so viele
            # Spalten, wie er anlegt — bei zwölf Spalten in einem 2600 Pixel
            # breiten Fenster wären das 216 Pixel je Spalte und damit 48 Pixel
            # Luft je Kachel. Mit einer hohen Grenze bestimmt die Kachelbreite
            # die Spaltenzahl, und die Abstände bleiben klein.
            max_columns=36,
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

    def apply_item_state(self, item_id: int, watched=None, favorite=None) -> None:
        """Zustand EINES Items in dieser Ansicht nachziehen.

        Gerufen, wenn „gesehen"/„Favorit" außerhalb der Kachel umgeschaltet
        wurde (Info-Karte). Aktualisiert die gerade gebundene Kachel und — über
        dieselben Dicts, die das Modell hält — auch den Datenstand, damit ein
        späteres Neubinden (Scrollen) den neuen Zustand mitbringt.
        """
        for row in self.store:
            if row.is_folder:
                continue
            if row.payload.get("id") != item_id:
                continue
            if watched is not None:
                row.payload["watched"] = bool(watched)
            if favorite is not None:
                row.payload["favorite"] = bool(favorite)
        card = self._card_by_item_id.get(item_id)
        if card is None:
            return
        if watched is not None:
            card.set_watched(bool(watched))
        if favorite is not None:
            card.set_favorite(bool(favorite))

    # -- Factory ---------------------------------------------------------

    def _on_setup(self, _factory, list_item: Gtk.ListItem) -> None:
        stack = Gtk.Stack(hhomogeneous=False, vhomogeneous=False)
        stack.set_size_request(CARD_WIDTH, -1)
        # `scroller=self`: die Kachel soll ihr Poster erst laden, wenn sie in
        # Sicht ist. Die Leiste wird ausdrücklich mitgegeben — eine Kachel darf
        # sie nicht selbst suchen (Kopf von widgets/poster.py).
        # Privatvideos bekommen dieselbe Kachelform wie Filme und Serien
        # (User-Wunsch: "auch in der Bibliothek Youtube sollen die Kacheln die
        # gleiche Größe haben"). Der Browser zeigt dort 16:9-Kacheln — in einer
        # App, in der man zwischen den Bibliotheken hin und her springt, wirkt
        # die wechselnde Kachelgröße aber wie ein Fehler. Musik bleibt
        # quadratisch, ein Cover ist nun einmal quadratisch.
        aspect = "movies" if self.kind == "private" else self.kind
        card = CardWidget(
            self.client,
            self.kind,
            on_activate=self._activate_item,
            on_toggle_watched=self.on_toggle_watched,
            on_toggle_favorite=self.on_toggle_favorite,
            scroller=self,
            aspect_kind=aspect,
        )
        folder_card = FolderCardWidget(
            self.client, aspect, on_activate=self._activate_folder, scroller=self
        )
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
            card = stack.get_child_by_name("item")
            card.bind(row.payload)
            item_id = row.payload.get("id")
            if item_id is not None:
                self._card_by_item_id[item_id] = card

    def _on_unbind(self, _factory, list_item: Gtk.ListItem) -> None:
        # Wichtig beim Recycling: ein noch laufender Poster-Request wird hier
        # entwertet, damit er nicht in der nächsten Belegung landet.
        stack: Gtk.Stack = list_item.get_child()
        card = stack.get_child_by_name("item")
        row = list_item.get_item()
        if row is not None and not row.is_folder:
            item_id = row.payload.get("id")
            # Nur ausräumen, wenn wirklich DIESE Kachel eingetragen ist — beim
            # Recycling kann inzwischen schon die nächste Bindung stehen.
            if self._card_by_item_id.get(item_id) is card:
                self._card_by_item_id.pop(item_id, None)
        card.unbind()
        stack.get_child_by_name("folder").unbind()

    # -- Klicks weiterreichen --------------------------------------------

    def _activate_item(self, item: dict) -> None:
        if self.on_item:
            self.on_item(item)

    def _activate_folder(self, folder: dict) -> None:
        if self.on_folder:
            self.on_folder(folder)


class AlbumRow(GObject.Object):
    """Eine Zeile im Album-Modell."""

    __gtype_name__ = "GoldfishAlbumRow"

    def __init__(self, album: dict) -> None:
        super().__init__()
        self.album = album


class AlbumGrid(Gtk.ScrolledWindow):
    """Scrollbares Album-Raster mit Recycling.

    Aus demselben Grund wie `CardGrid` kein FlowBox: 2717 Alben würden sonst
    2717 Widgets samt Coveranfragen erzeugen und den Hauptablauf mehrere
    Sekunden blockieren (nachgemessen: 4,65 Sekunden)."""

    def __init__(self, client: GoldfishClient, on_album: Callable[[dict], None] | None = None) -> None:
        super().__init__(vexpand=True, hexpand=True)
        ensure_card_css()
        self.client = client
        self.on_album = on_album

        self.store = Gio.ListStore.new(AlbumRow)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)
        factory.connect("unbind", self._on_unbind)

        self.grid = Gtk.GridView(
            model=Gtk.NoSelection.new(self.store),
            factory=factory,
            # Großzügig: der GridView teilt seine Breite auf GENAU so viele
            # Spalten, wie er anlegt — bei zwölf Spalten in einem 2600 Pixel
            # breiten Fenster wären das 216 Pixel je Spalte und damit 48 Pixel
            # Luft je Kachel. Mit einer hohen Grenze bestimmt die Kachelbreite
            # die Spaltenzahl, und die Abstände bleiben klein.
            max_columns=36,
            min_columns=1,
            vexpand=True,
            single_click_activate=False,
        )
        self.grid.add_css_class("navigation-sidebar")
        for setter in (self.grid.set_margin_start, self.grid.set_margin_end, self.grid.set_margin_top):
            setter(12)
        self.grid.set_margin_bottom(24)
        self.set_child(self.grid)

    def set_albums(self, albums: list[dict]) -> None:
        self.store.splice(0, self.store.get_n_items(), [AlbumRow(a) for a in albums])
        adjustment = self.get_vadjustment()
        if adjustment is not None:
            adjustment.set_value(0)

    def _on_setup(self, _factory, list_item: Gtk.ListItem) -> None:
        list_item.set_child(AlbumCardWidget(self.client, on_activate=self._activate, scroller=self))

    def _on_bind(self, _factory, list_item: Gtk.ListItem) -> None:
        list_item.get_child().bind(list_item.get_item().album)

    def _on_unbind(self, _factory, list_item: Gtk.ListItem) -> None:
        list_item.get_child().unbind()

    def _activate(self, album: dict) -> None:
        if self.on_album:
            self.on_album(album)


class LocalRow(GObject.Object):
    """Eine Zeile im Modell einer lokalen Bibliothek."""

    __gtype_name__ = "GoldfishLocalRow"

    def __init__(self, video: dict) -> None:
        super().__init__()
        self.video = video


class LocalGrid(Gtk.ScrolledWindow):
    """Raster für lokale Videos, mit Recycling wie die übrigen Raster — eine
    externe Platte kann viele tausend Dateien enthalten."""

    def __init__(self, client: GoldfishClient, on_video: Callable[[dict], None] | None = None) -> None:
        super().__init__(vexpand=True, hexpand=True)
        ensure_card_css()
        self.client = client
        self.on_video = on_video

        self.store = Gio.ListStore.new(LocalRow)
        factory = Gtk.SignalListItemFactory()
        factory.connect(
            "setup",
            lambda _f, li: li.set_child(LocalCardWidget(self.client, on_activate=self._activate, scroller=self)),
        )
        factory.connect("bind", lambda _f, li: li.get_child().bind(li.get_item().video))
        factory.connect("unbind", lambda _f, li: li.get_child().unbind())

        self.grid = Gtk.GridView(
            model=Gtk.NoSelection.new(self.store),
            factory=factory,
            max_columns=36,
            min_columns=1,
            vexpand=True,
            single_click_activate=False,
        )
        self.grid.add_css_class("navigation-sidebar")
        for setter in (self.grid.set_margin_start, self.grid.set_margin_end, self.grid.set_margin_top):
            setter(12)
        self.grid.set_margin_bottom(24)
        self.set_child(self.grid)

    def set_videos(self, videos: list[dict]) -> None:
        self.store.splice(0, self.store.get_n_items(), [LocalRow(v) for v in videos])
        adjustment = self.get_vadjustment()
        if adjustment is not None:
            adjustment.set_value(0)

    def _activate(self, video: dict) -> None:
        if self.on_video:
            self.on_video(video)
