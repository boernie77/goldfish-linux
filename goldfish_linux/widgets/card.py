"""Kachel für ein Item oder einen Ordner im Raster.

Eine Kachel besteht aus einem Bildbereich mit aufliegenden Abzeichen und
darunter ein bis zwei Textzeilen. Sie wird im Gtk.GridView wiederverwendet:
`CardWidget` baut das Widget einmal (`setup`) und bekommt per `bind()` ein
neues Item gesetzt. Deshalb müssen in `bind()` ALLE Zustände ausdrücklich
gesetzt oder versteckt werden — ein vergessenes Abzeichen bliebe sonst vom
vorherigen Item stehen.

Seitenverhältnis und Titelzeilen richten sich nach der Bibliotheksart, wie im
Browser (`cards.js`): Filme und Serien tragen ein 2:3-Poster, Privatvideos ein
16:9-Vorschaubild, Musik ein quadratisches Cover.
"""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gtk, Pango  # noqa: E402

from ..api import GoldfishClient  # noqa: E402
from ..formatting import format_duration, format_resolution  # noqa: E402
from .poster import load_poster_async  # noqa: E402

CARD_WIDTH = 168
# Breitformatige Kacheln (Standbilder von Folgen, Privatvideos) dürfen mehr
# Fläche haben — bei 168 px wären sie nur 94 px hoch und kaum zu erkennen.
CARD_WIDTH_WIDE = 240

# Seitenverhältnis je Bibliotheksart — dieselbe Aufteilung wie im Browser.
_ASPECT = {
    "movies": 3 / 2,
    "tv": 3 / 2,
    "music": 1.0,
    "private": 9 / 16,
}


def card_height_for(kind: str, width: int = CARD_WIDTH) -> int:
    """Bildhöhe einer Kachel dieser Bibliotheksart."""
    return int(width * _ASPECT.get(kind, 3 / 2))


_CSS = b"""
.gf-card-image {
  background-color: alpha(@window_fg_color, 0.08);
  border-radius: 8px;
}
.gf-badge {
  background-color: alpha(#000000, 0.72);
  color: #ffffff;
  border-radius: 5px;
  padding: 1px 5px;
  font-size: 0.76rem;
  font-weight: bold;
}
.gf-badge-rating { background-color: alpha(#1c1c1c, 0.8); }
.gf-toggle {
  background-color: alpha(#000000, 0.6);
  border-radius: 50%;
  min-width: 26px;
  min-height: 26px;
  padding: 0;
  color: alpha(#ffffff, 0.55);
}
.gf-toggle:hover { background-color: alpha(#000000, 0.85); }
.gf-toggle-on { color: #ffffff; }
.gf-toggle-fav-on { color: #ff6b6b; }
.gf-card-title {
  font-size: 0.92rem;
  font-weight: 500;
}
.gf-card-sub {
  font-size: 0.8rem;
  opacity: 0.6;
}
.gf-card-watched .gf-card-image { opacity: 0.55; }
"""

_css_loaded = False


def ensure_card_css() -> None:
    """Lädt das Kachel-Stylesheet einmal pro Prozess."""
    global _css_loaded
    if _css_loaded:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return  # kein Display (z. B. im Test) — Kacheln funktionieren auch ungestylt
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_loaded = True


class CardWidget(Gtk.Box):
    """Wiederverwendbare Kachel. `bind()` setzt sie auf ein neues Item um."""

    def __init__(
        self,
        client: GoldfishClient,
        kind: str,
        on_activate: Callable[[dict], None] | None = None,
        on_toggle_watched: Callable[[dict, bool], None] | None = None,
        on_toggle_favorite: Callable[[dict, bool], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.client = client
        self.kind = kind
        self.on_activate = on_activate
        self.on_toggle_watched = on_toggle_watched
        self.on_toggle_favorite = on_toggle_favorite
        self.item: dict | None = None

        self.set_size_request(CARD_WIDTH, -1)
        # halign ist hier der entscheidende Teil: ohne ihn streckt ein
        # Container mit Restplatz die Kachel über ihre Sollbreite. Gemessen:
        # 283 px statt 168 für die erste Kachel eines Streifens auf der
        # Startseite, während die Bildtextur korrekt 168 px breit war.
        self.set_hexpand(False)
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)

        # -- Bildbereich mit Abzeichen --
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        self.picture.set_size_request(CARD_WIDTH, card_height_for(kind))
        self.picture.set_hexpand(False)
        self.picture.add_css_class("gf-card-image")

        self.overlay = Gtk.Overlay(child=self.picture)
        self.overlay.set_hexpand(False)
        self.overlay.set_halign(Gtk.Align.START)
        self.overlay.set_overflow(Gtk.Overflow.HIDDEN)

        self.watched_btn = Gtk.Button(
            icon_name="object-select-symbolic",
            halign=Gtk.Align.START,
            valign=Gtk.Align.START,
            margin_start=6,
            margin_top=6,
            has_frame=False,
            tooltip_text="Als gesehen markieren",
        )
        self.watched_btn.add_css_class("gf-toggle")
        self.watched_btn.connect("clicked", self._on_watched_clicked)
        self.overlay.add_overlay(self.watched_btn)

        self.rating_label = Gtk.Label(halign=Gtk.Align.END, valign=Gtk.Align.START, margin_end=6, margin_top=6)
        self.rating_label.add_css_class("gf-badge")
        self.rating_label.add_css_class("gf-badge-rating")
        self.overlay.add_overlay(self.rating_label)

        # Varianten-Anzahl sitzt UNTER der Bewertung, nicht daneben — beide
        # oben rechts würden sich sonst überdecken (gleiche Stapelung wie im
        # Browser, siehe Kachel-Overlay-Tabelle in der Server-CLAUDE.md).
        self.variant_label = Gtk.Label(halign=Gtk.Align.END, valign=Gtk.Align.START, margin_end=6, margin_top=36)
        self.variant_label.add_css_class("gf-badge")
        self.overlay.add_overlay(self.variant_label)

        bottom = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=5,
            halign=Gtk.Align.FILL,
            valign=Gtk.Align.END,
            margin_start=6,
            margin_end=6,
            margin_bottom=6,
        )
        self.res_label = Gtk.Label()
        self.res_label.add_css_class("gf-badge")
        bottom.append(self.res_label)

        self.favorite_btn = Gtk.Button(
            icon_name="emblem-favorite-symbolic",
            has_frame=False,
            tooltip_text="Als Favorit merken",
        )
        self.favorite_btn.add_css_class("gf-toggle")
        self.favorite_btn.connect("clicked", self._on_favorite_clicked)
        bottom.append(self.favorite_btn)

        spacer = Gtk.Box(hexpand=True)
        bottom.append(spacer)

        self.duration_label = Gtk.Label()
        self.duration_label.add_css_class("gf-badge")
        bottom.append(self.duration_label)
        self.overlay.add_overlay(bottom)

        self.append(self.overlay)

        # -- Textzeilen --
        # **Titel bewusst einzeilig mit Auslassung.** Ein umbrechendes Label
        # mit `lines=2` fordert bei vorgegebener Höhe die volle Textbreite an,
        # damit der Text ungekürzt in zwei Zeilen passt — nachgemessen 297
        # statt 168 Pixel bei einem langen Filmtitel. Die Kachel bekam dadurch
        # mehr Platz zugeteilt, zeichnete nur 168 davon und hinterließ eine
        # sichtbare Lücke zum Nachbarn (vom Benutzer auf der Startseite
        # bemerkt). Weder `max-width-chars`, `width-request` noch `halign`
        # ändern daran etwas, und eine überschriebene Breitenmessung greift
        # bei einer Gtk.Box-Unterklasse in PyGObject nicht (nachgemessen: die
        # Überschreibung wurde nie aufgerufen). Einzeilig ist die einzige
        # Einstellung mit stabiler Breitenanforderung; der vollständige Titel
        # steht im Tooltip.
        self.title_label = Gtk.Label(
            xalign=0,
            ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=1,
            width_request=CARD_WIDTH,
        )
        self.title_label.add_css_class("gf-card-title")
        self.append(self.title_label)

        self.sub_label = Gtk.Label(
            xalign=0,
            ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=1,
            width_request=CARD_WIDTH,
        )
        self.sub_label.add_css_class("gf-card-sub")
        self.append(self.sub_label)

        click = Gtk.GestureClick()
        click.connect("released", self._on_clicked)
        self.picture.add_controller(click)


    # -- Belegen ---------------------------------------------------------

    def bind(self, item: dict) -> None:
        self.item = item
        metadata = item.get("metadata") or {}
        raw_title = item.get("title") or "Unbenannt"
        title = metadata.get("title") or raw_title

        self.title_label.set_text(title)
        # Der vollständige Titel bleibt über den Tooltip erreichbar, weil die
        # Zeile ihn bei Bedarf abschneidet.
        self.set_tooltip_text(title)

        # Zweite Zeile: bei einem echten Metadaten-Titel Jahr und Genres,
        # sonst der Ordnerpfad — nie derselbe Text zweimal (gleiche Regel wie
        # `renderCard` im Browser).
        sub_parts: list[str] = []
        # Staffel und Folge stehen in `metadata`, NICHT auf der Item-Ebene
        # (dort liegt nur `episodeEnd` für Doppelfolgen) — beim ersten Test
        # blieb die Zeile deshalb bei einem nackten Jahr stehen.
        season, episode = metadata.get("season"), metadata.get("episode")
        if season and episode:
            end = item.get("episodeEnd") or 0
            label = f"S{season:02d}E{episode:02d}"
            sub_parts.append(f"{label}-{end:02d}" if end > episode else label)
        elif metadata.get("title") and metadata.get("year"):
            sub_parts.append(str(metadata["year"]))
        if item.get("artist"):
            sub_parts.append(item["artist"])
        if not sub_parts:
            # Kein Metadaten-Titel: dann steht in der ersten Zeile der
            # Dateiname — hier den Ordnerpfad zeigen statt ihn zu wiederholen.
            rel = item.get("relPath") or ""
            folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
            sub_parts.append(folder or (item.get("container") or "").upper())
        self.sub_label.set_text(" · ".join(p for p in sub_parts if p))

        rating = metadata.get("rating") or 0
        self._set_badge(self.rating_label, f"★ {rating:.1f}" if rating else "")

        variants = item.get("variantCount") or 0
        self._set_badge(self.variant_label, f"×{variants}" if variants >= 2 else "")

        # Auflösung ist ein Video-Begriff — bei Musik irreführend, deshalb weg.
        res = "" if self.kind == "music" else format_resolution(item.get("width") or 0, item.get("height") or 0)
        self._set_badge(self.res_label, res)
        self._set_badge(self.duration_label, format_duration(item.get("durationSec") or 0))

        self._apply_watched(bool(item.get("watched")))
        self._apply_favorite(bool(item.get("favorite")))

        load_poster_async(self.picture, self.client, self.client.poster_path_for_item(item), decode_width=CARD_WIDTH)

    def unbind(self) -> None:
        """Vom GridView beim Recycling gerufen: laufendes Poster-Laden
        entwerten, damit es nicht in die nächste Belegung fällt."""
        self.item = None
        load_poster_async(self.picture, self.client, None)

    # -- Abzeichen und Zustände ------------------------------------------

    @staticmethod
    def _set_badge(label: Gtk.Label, text: str) -> None:
        label.set_text(text)
        label.set_visible(bool(text))

    def _apply_watched(self, watched: bool) -> None:
        self.watched_btn.set_tooltip_text("Als ungesehen markieren" if watched else "Als gesehen markieren")
        if watched:
            self.watched_btn.add_css_class("gf-toggle-on")
            self.add_css_class("gf-card-watched")
        else:
            self.watched_btn.remove_css_class("gf-toggle-on")
            self.remove_css_class("gf-card-watched")

    def _apply_favorite(self, favorite: bool) -> None:
        self.favorite_btn.set_tooltip_text("Favorit entfernen" if favorite else "Als Favorit merken")
        if favorite:
            self.favorite_btn.add_css_class("gf-toggle-fav-on")
        else:
            self.favorite_btn.remove_css_class("gf-toggle-fav-on")

    # -- Klicks ----------------------------------------------------------

    def _on_clicked(self, *_args) -> None:
        if self.item and self.on_activate:
            self.on_activate(self.item)

    def _on_watched_clicked(self, *_args) -> None:
        if not self.item:
            return
        new_state = not bool(self.item.get("watched"))
        self.item["watched"] = new_state
        self._apply_watched(new_state)  # sofort umschalten, Server folgt
        if self.on_toggle_watched:
            self.on_toggle_watched(self.item, new_state)

    def _on_favorite_clicked(self, *_args) -> None:
        if not self.item:
            return
        new_state = not bool(self.item.get("favorite"))
        self.item["favorite"] = new_state
        self._apply_favorite(new_state)
        if self.on_toggle_favorite:
            self.on_toggle_favorite(self.item, new_state)


class FolderCardWidget(Gtk.Box):
    """Kachel für einen Ordner: Vorschaubild eines enthaltenen Videos oder das
    Serienposter, Name und Anzahl. Trägt bei zusammengeführten Serienordnern
    ein 🔗 und bei vom Auto-Scan ausgenommenen Ordnern ein 🚫."""

    def __init__(self, client: GoldfishClient, kind: str, on_activate: Callable[[dict], None] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.client = client
        self.kind = kind
        self.on_activate = on_activate
        self.folder: dict | None = None
        self.set_size_request(CARD_WIDTH, -1)

        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        self.picture.set_size_request(CARD_WIDTH, card_height_for(kind))
        self.picture.add_css_class("gf-card-image")
        self.overlay = Gtk.Overlay(child=self.picture)

        self.marker_label = Gtk.Label(halign=Gtk.Align.START, valign=Gtk.Align.START, margin_start=6, margin_top=6)
        self.marker_label.add_css_class("gf-badge")
        self.overlay.add_overlay(self.marker_label)

        self.count_label = Gtk.Label(halign=Gtk.Align.END, valign=Gtk.Align.END, margin_end=6, margin_bottom=6)
        self.count_label.add_css_class("gf-badge")
        self.overlay.add_overlay(self.count_label)
        self.append(self.overlay)

        self.title_label = Gtk.Label(
            xalign=0,
            ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=1,
            width_request=CARD_WIDTH,
        )
        self.title_label.add_css_class("gf-card-title")
        self.append(self.title_label)

        click = Gtk.GestureClick()
        click.connect("released", self._on_clicked)
        self.picture.add_controller(click)


    def bind(self, folder: dict) -> None:
        self.folder = folder
        name = (folder.get("name") or "").rsplit("/", 1)[-1]
        metadata = folder.get("metadata") or {}
        label = metadata.get("title") or name
        self.title_label.set_text(label)
        self.set_tooltip_text(label)

        # Eine nackte "1" unten rechts sah im ersten Test wie eine Laufzeit
        # aus — mit Einheit ist klar, dass es die Anzahl ist.
        count = folder.get("itemCount") or 0
        self.count_label.set_text(f"{count} Titel" if count != 1 else "1 Titel")
        self.count_label.set_visible(count > 0)

        markers = []
        if folder.get("mergedFolders"):
            markers.append("🔗")
        if folder.get("excluded"):
            markers.append("🚫")
        self.marker_label.set_text(" ".join(markers))
        self.marker_label.set_visible(bool(markers))

        # Ordner-Poster: Serienposter wenn zugeordnet, sonst Vorschaubild
        # eines enthaltenen Videos.
        path = None
        if folder.get("metadataId"):
            path = f"/api/poster/metadata/{folder['metadataId']}"
        elif folder.get("thumbItemId"):
            path = f"/api/thumb/{folder['thumbItemId']}"
        load_poster_async(self.picture, self.client, path, decode_width=CARD_WIDTH)

    def unbind(self) -> None:
        self.folder = None
        load_poster_async(self.picture, self.client, None)

    def _on_clicked(self, *_args) -> None:
        if self.folder and self.on_activate:
            self.on_activate(self.folder)


class SimpleCard(Gtk.Box):
    """Kachel für alles, was kein Item und kein Ordner ist: Staffeln,
    Sammlungen, Playlists, Episoden aus der Staffelübersicht und Filme, die
    zu einer Sammlung gehören, aber nicht vorhanden sind.

    Anders als `CardWidget` wird sie nicht wiederverwendet, sondern für ihren
    Inhalt gebaut — diese Listen sind kurz (Staffeln einer Serie, Teile einer
    Sammlung), da lohnt die Umschaltmechanik nicht.

    `dimmed` blendet zurück, was nicht vorhanden ist; `badge` steht unten
    rechts, `corner` oben links.
    """

    def __init__(
        self,
        client: GoldfishClient,
        image_path: str | None,
        title: str,
        subtitle: str = "",
        badge: str = "",
        corner: str = "",
        aspect: str = "movies",
        dimmed: bool = False,
        on_click: Callable[[], None] | None = None,
        tooltip: str = "",
    ) -> None:
        width = CARD_WIDTH_WIDE if aspect == "private" else CARD_WIDTH
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._fixed_width = width
        ensure_card_css()
        self.set_size_request(width, -1)
        # Nicht mitwachsen: sonst gibt die FlowBox der Kachel die natürliche
        # Breite des geladenen Bildes (320 px) statt der Sollbreite.
        self.set_hexpand(False)
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)
        self.on_click = on_click

        picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        picture.set_size_request(width, card_height_for(aspect, width))
        picture.set_hexpand(False)
        picture.add_css_class("gf-card-image")
        overlay = Gtk.Overlay(child=picture)
        overlay.set_hexpand(False)
        # Zuschneiden statt überlaufen lassen, damit ein breiteres Bild die
        # Kachelbreite nicht sprengt.
        overlay.set_overflow(Gtk.Overflow.HIDDEN)

        if corner:
            label = Gtk.Label(label=corner, halign=Gtk.Align.START, valign=Gtk.Align.START, margin_start=6, margin_top=6)
            label.add_css_class("gf-badge")
            overlay.add_overlay(label)
        if badge:
            label = Gtk.Label(label=badge, halign=Gtk.Align.END, valign=Gtk.Align.END, margin_end=6, margin_bottom=6)
            label.add_css_class("gf-badge")
            overlay.add_overlay(label)
        self.append(overlay)

        title_label = Gtk.Label(
            label=title,
            xalign=0,
            ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=1,
            width_request=width,
        )
        title_label.add_css_class("gf-card-title")
        self.append(title_label)
        if not tooltip:
            self.set_tooltip_text(title)

        if subtitle:
            sub_label = Gtk.Label(label=subtitle, xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1)
            sub_label.add_css_class("gf-card-sub")
            self.append(sub_label)

        if dimmed:
            self.set_opacity(0.45)
        if tooltip:
            self.set_tooltip_text(tooltip)
        if on_click is not None:
            click = Gtk.GestureClick()
            click.connect("released", lambda *_: self.on_click and self.on_click())
            picture.add_controller(click)
            picture.set_cursor(Gdk.Cursor.new_from_name("pointer", None))

        load_poster_async(picture, client, image_path, decode_width=width)



def card_flow(spacing: int = 16) -> Gtk.FlowBox:
    """Fließendes Raster für kurze Listen aus `SimpleCard`.

    Hier ist FlowBox richtig, anders als bei den Bibliotheken: eine Serie hat
    eine Handvoll Staffeln, eine Sammlung ein paar Teile. Die Umschaltmechanik
    eines GridView bräuchte man erst bei hunderten Kacheln."""
    flow = Gtk.FlowBox(
        selection_mode=Gtk.SelectionMode.NONE,
        homogeneous=False,
        column_spacing=spacing,
        row_spacing=spacing,
        max_children_per_line=12,
        min_children_per_line=1,
        margin_start=16,
        margin_end=16,
        margin_top=12,
        margin_bottom=24,
        valign=Gtk.Align.START,
    )
    return flow
