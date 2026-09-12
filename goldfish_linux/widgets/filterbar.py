"""Sortier- und Filterleiste für die Kopfzeile einer Bibliotheksansicht.

Enthält drei Bedienelemente: ein Menü für das Sortierfeld, einen Schalter für
die Richtung und ein Menü mit den Filtern (Gesehen, Favoriten, Auflösung,
Genre). Welche davon sichtbar sind, hängt an der Bibliotheksart — genau wie im
Browser, wo jede Option ein `data-kinds`-Attribut trägt: eine Auflösung ist bei
Musik bedeutungslos, eine Bewertung gibt es nur mit TMDB-Zuordnung, und
Künstler oder Album sind Musikbegriffe.

Die Leiste kennt den Server nicht; sie meldet über `on_change` nur, dass sich
etwas geändert hat. Was daraus an Abfragen wird, entscheidet die Ansicht.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

# (Schlüssel, Beschriftung, für welche Bibliotheksarten). Schlüssel und
# Standardrichtung entsprechen dem Server (internal/store/items.go, switch
# f.Sort) und der Mac-App (ItemSort), damit beide Clients gleich sortieren.
SORTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("title", "Titel", ("movies", "tv", "private", "music")),
    ("filename", "Dateiname", ("movies", "tv", "private", "music")),
    ("released", "Veröffentlicht", ("movies", "tv", "private", "music")),
    ("added", "Hinzugefügt", ("movies", "tv", "private", "music")),
    ("played", "Zuletzt abgespielt", ("movies", "tv", "private", "music")),
    ("duration", "Laufzeit", ("movies", "tv", "private", "music")),
    ("size", "Dateigröße", ("movies", "tv", "private", "music")),
    ("rating", "Bewertung", ("movies", "tv")),
    ("resolution", "Auflösung", ("movies", "tv", "private")),
    ("artist", "Künstler", ("music",)),
    ("album", "Album", ("music",)),
)

# Nur diese beiden Felder werden aufsteigend voreingestellt (grid.js
# `sortDefaultDir`, ItemSort.defaultAscending) — bei allen anderen will man
# zuerst das Neueste, Längste oder Bestbewertete sehen.
_ASCENDING_BY_DEFAULT = {"title", "filename", "artist", "album"}

# Sortierungen, die die Ordnerstruktur übergehen und flach über alles gehen
# (grid.js FLAT_SORTS). "title" gehört bewusst NICHT dazu: es ist die
# Voreinstellung, und flach geschaltet würde jede Ordneransicht dauerhaft ihre
# Kacheln verlieren.
FLAT_SORTS = frozenset({"played", "added", "duration", "size", "released", "filename"})

WATCHED_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "Alle"),
    ("no", "Nur ungesehene"),
    ("yes", "Nur gesehene"),
)

# Stufen wie im Server (internal/store/items.go), von hoch nach niedrig.
BUCKETS: tuple[tuple[str, str], ...] = (
    ("4k", "4K"),
    ("2k", "2K"),
    ("1080p", "1080p"),
    ("720p", "720p"),
    ("576p", "576p"),
    ("540p", "540p"),
    ("480p", "480p"),
    ("360p", "360p und kleiner"),
)


@dataclass
class FilterState:
    """Was gerade eingestellt ist. `sort_dir` ist "" für die Voreinstellung
    des jeweiligen Feldes."""

    sort: str = "title"
    ascending: bool | None = None
    watched: str = ""
    favorites_only: bool = False
    buckets: set[str] = field(default_factory=set)
    genres: set[str] = field(default_factory=set)

    def effective_ascending(self) -> bool:
        if self.ascending is not None:
            return self.ascending
        return self.sort in _ASCENDING_BY_DEFAULT

    @property
    def sort_dir(self) -> str:
        return "asc" if self.effective_ascending() else "desc"

    @property
    def is_flat(self) -> bool:
        """Ob diese Sortierung die Ordnerkacheln übergeht."""
        return self.sort in FLAT_SORTS

    def active_filter_count(self) -> int:
        return sum(
            (
                1 if self.watched else 0,
                1 if self.favorites_only else 0,
                len(self.buckets),
                len(self.genres),
            )
        )


def sorts_for_kind(kind: str) -> list[tuple[str, str]]:
    return [(key, label) for key, label, kinds in SORTS if kind in kinds]


class FilterBar(Gtk.Box):
    """Die drei Bedienelemente als eine Einheit.

    `load_genres` wird erst beim ersten Öffnen des Filtermenüs aufgerufen — die
    Liste kostet eine eigene Server-Abfrage und wird beim reinen Durchblättern
    nie gebraucht."""

    def __init__(
        self,
        kind: str,
        state: FilterState,
        on_change: Callable[[], None],
        load_genres: Callable[[], list[str]] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.kind = kind
        self.state = state
        self.on_change = on_change
        self.load_genres = load_genres
        self._genres_loaded = False
        self._genre_checks: dict[str, Gtk.CheckButton] = {}

        self.sort_button = Gtk.MenuButton(tooltip_text="Sortierung")
        self.sort_button.set_popover(self._build_sort_popover())
        self._update_sort_label()
        self.append(self.sort_button)

        self.dir_button = Gtk.Button()
        self.dir_button.connect("clicked", self._on_direction_clicked)
        self._update_direction_icon()
        self.append(self.dir_button)

        self.filter_button = Gtk.MenuButton(icon_name="funnel-symbolic", tooltip_text="Filter")
        # funnel-symbolic fehlt in älteren Icon-Themes — sichtbarer Ersatz,
        # damit der Knopf nicht als leere Fläche erscheint.
        if not _icon_exists("funnel-symbolic"):
            self.filter_button.set_icon_name("view-filter-symbolic")
        self.filter_popover = self._build_filter_popover()
        self.filter_button.set_popover(self.filter_popover)
        self.filter_popover.connect("show", self._on_filter_popover_shown)
        self.append(self.filter_button)
        self._update_filter_badge()

    # -- Sortierung ------------------------------------------------------

    def _build_sort_popover(self) -> Gtk.Popover:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        group: Gtk.CheckButton | None = None
        for key, label in sorts_for_kind(self.kind):
            check = Gtk.CheckButton(label=label)
            if group is None:
                group = check
            else:
                check.set_group(group)
            check.set_active(key == self.state.sort)
            check.connect("toggled", self._on_sort_toggled, key)
            box.append(check)
        popover = Gtk.Popover(child=box)
        return popover

    def _on_sort_toggled(self, check: Gtk.CheckButton, key: str) -> None:
        if not check.get_active() or key == self.state.sort:
            return
        self.state.sort = key
        # Richtung auf die Voreinstellung des neuen Feldes zurücksetzen: von
        # "Titel A–Z" auf "Hinzugefügt" zu wechseln soll das Neueste zeigen,
        # nicht das Älteste.
        self.state.ascending = None
        self._update_sort_label()
        self._update_direction_icon()
        self.sort_button.get_popover().popdown()
        self.on_change()

    def _update_sort_label(self) -> None:
        label = dict((k, lbl) for k, lbl, _ in SORTS).get(self.state.sort, "Titel")
        self.sort_button.set_label(label)

    def _on_direction_clicked(self, *_args) -> None:
        self.state.ascending = not self.state.effective_ascending()
        self._update_direction_icon()
        self.on_change()

    def _update_direction_icon(self) -> None:
        ascending = self.state.effective_ascending()
        self.dir_button.set_icon_name("view-sort-ascending-symbolic" if ascending else "view-sort-descending-symbolic")
        self.dir_button.set_tooltip_text("Aufsteigend" if ascending else "Absteigend")

    # -- Filter ----------------------------------------------------------

    def _build_filter_popover(self) -> Gtk.Popover:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        outer.set_size_request(260, -1)

        # Gesehen und Favoriten sind Video-Begriffe; für Musik führt der Server
        # keinen Gesehen-Status.
        if self.kind != "music":
            self.watched_drop = Gtk.DropDown.new_from_strings([label for _, label in WATCHED_CHOICES])
            self.watched_drop.set_selected(next(i for i, (key, _) in enumerate(WATCHED_CHOICES) if key == self.state.watched))
            self.watched_drop.connect("notify::selected", self._on_watched_changed)
            outer.append(_labelled("Gesehen", self.watched_drop))

        self.fav_switch = Gtk.Switch(active=self.state.favorites_only, halign=Gtk.Align.END)
        self.fav_switch.connect("notify::active", self._on_favorites_changed)
        outer.append(_labelled("Nur Favoriten", self.fav_switch))

        if self.kind != "music":
            outer.append(Gtk.Separator())
            outer.append(_section_label("Auflösung"))
            self._bucket_checks: dict[str, Gtk.CheckButton] = {}
            bucket_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            for key, label in BUCKETS:
                check = Gtk.CheckButton(label=label, active=key in self.state.buckets)
                check.connect("toggled", self._on_bucket_toggled, key)
                self._bucket_checks[key] = check
                bucket_box.append(check)
            outer.append(bucket_box)

        # Privatvideos haben kein Genre — der Server liefert dort immer eine
        # leere Liste, also gar keinen Abschnitt zeigen.
        if self.kind != "private" and self.load_genres is not None:
            outer.append(Gtk.Separator())
            outer.append(_section_label("Genre"))
            self.genre_search = Gtk.SearchEntry(placeholder_text="Genre suchen…")
            self.genre_search.connect("search-changed", self._on_genre_search)
            outer.append(self.genre_search)
            self.genre_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            genre_scroll = Gtk.ScrolledWindow(
                child=self.genre_box,
                propagate_natural_height=True,
                # Mindesthöhe, damit immer mindestens vier Genres zu sehen
                # sind: vorher blieben bei vielen Filtern über der Liste nur
                # zwei übrig, weil sich das Fenster den Platz von unten nahm.
                min_content_height=150,
                max_content_height=300,
                hscrollbar_policy=Gtk.PolicyType.NEVER,
            )
            outer.append(genre_scroll)

        outer.append(Gtk.Separator())
        reset = Gtk.Button(label="Filter zurücksetzen")
        reset.connect("clicked", self._on_reset)
        outer.append(reset)

        scroll = Gtk.ScrolledWindow(
            child=outer,
            propagate_natural_height=True,
            propagate_natural_width=True,
            max_content_height=760,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        return Gtk.Popover(child=scroll)

    def _on_filter_popover_shown(self, *_args) -> None:
        if self._genres_loaded or self.load_genres is None or not hasattr(self, "genre_box"):
            return
        self._genres_loaded = True
        for name in self.load_genres():
            check = Gtk.CheckButton(label=name, active=name in self.state.genres)
            check.connect("toggled", self._on_genre_toggled, name)
            self._genre_checks[name] = check
            self.genre_box.append(check)
        if not self._genre_checks:
            self.genre_box.append(Gtk.Label(label="Keine Genres hinterlegt", xalign=0, sensitive=False))

    def _on_genre_search(self, entry: Gtk.SearchEntry) -> None:
        needle = entry.get_text().strip().lower()
        for name, check in self._genre_checks.items():
            check.set_visible(needle in name.lower() if needle else True)

    def _on_watched_changed(self, drop: Gtk.DropDown, _param) -> None:
        self.state.watched = WATCHED_CHOICES[drop.get_selected()][0]
        self._update_filter_badge()
        self.on_change()

    def _on_favorites_changed(self, switch: Gtk.Switch, _param) -> None:
        self.state.favorites_only = switch.get_active()
        self._update_filter_badge()
        self.on_change()

    def _on_bucket_toggled(self, check: Gtk.CheckButton, key: str) -> None:
        if check.get_active():
            self.state.buckets.add(key)
        else:
            self.state.buckets.discard(key)
        self._update_filter_badge()
        self.on_change()

    def _on_genre_toggled(self, check: Gtk.CheckButton, name: str) -> None:
        if check.get_active():
            self.state.genres.add(name)
        else:
            self.state.genres.discard(name)
        self._update_filter_badge()
        self.on_change()

    def _on_reset(self, *_args) -> None:
        self.state.watched = ""
        self.state.favorites_only = False
        self.state.buckets.clear()
        self.state.genres.clear()
        if hasattr(self, "watched_drop"):
            self.watched_drop.set_selected(0)
        self.fav_switch.set_active(False)
        for check in getattr(self, "_bucket_checks", {}).values():
            check.set_active(False)
        for check in self._genre_checks.values():
            check.set_active(False)
        self._update_filter_badge()
        self.filter_popover.popdown()
        self.on_change()

    def _update_filter_badge(self) -> None:
        count = self.state.active_filter_count()
        self.filter_button.set_tooltip_text(f"Filter ({count} aktiv)" if count else "Filter")
        if count:
            self.filter_button.add_css_class("suggested-action")
        else:
            self.filter_button.remove_css_class("suggested-action")


def _labelled(text: str, widget: Gtk.Widget) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    box.append(Gtk.Label(label=text, xalign=0, hexpand=True))
    box.append(widget)
    return box


def _section_label(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.add_css_class("heading")
    return label


def _icon_exists(name: str) -> bool:
    display = Gdk.Display.get_default()
    if display is None:
        return True  # ohne Display nicht prüfbar — beim Standardnamen bleiben
    return Gtk.IconTheme.get_for_display(display).has_icon(name)
