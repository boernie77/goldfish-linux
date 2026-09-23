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
from ..formatting import format_date, format_duration, format_resolution  # noqa: E402
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


# Server-generierte Item-Thumbnails (`/api/thumb/{id}`, Fallback für Privat-
# Bibliotheken UND unmatched TV-Episoden ohne TMDB-Poster) sind immer 16:9
# (480×270, siehe Server-CLAUDE.md „Scanner & Metadaten"). Landet so ein Bild
# in einer 2:3-Kartenform (Filme/Serien-Kachelform — u.a. weil private Videos
# in `CardGrid`/`home_page.py` bewusst dieselbe Kachelform wie Filme bekommen,
# siehe `aspect`-Kommentar dort), scaliert `Gtk.Picture`s ContentFit.COVER das
# Bild so, dass die Höhe die Karte füllt — bei den Seitenverhältnissen hier
# bedeutet das FAKTISCH KEIN Hochskalieren des Originalbilds selbst (Cover
# skaliert eher leicht herunter + croppt seitlich). Der eigentliche Bug (User-
# Report 2026-09-13, "die sind immer noch unscharf", nach dem `CARD_WIDTH_WIDE`-
# Fix von 0.1.24, der NIE griff — `CardGrid` erzwingt für alle Bibliotheksarten
# dieselbe Kartenform, umgeht also den in 0.1.24 gebauten Breiten-Umschalter
# komplett) sitzt EINEN Schritt vorher: `load_poster_async(decode_width=…)`
# dekodiert das Bild BEREITS auf `_frame_width` (168px) — bei 16:9 ergibt das
# nur ~94px Höhe. Erst DANACH skaliert ContentFit.COVER dieses bereits stark
# verkleinerte Bild wieder auf die volle Kartenhöhe (252px) hoch, ein Faktor
# von ~2,7× — das ist der tatsächliche Unschärfe-Schritt, nicht der Crop
# selbst. Fix: bei einem Thumbnail-Fallback in einer NICHT-16:9-Kartenform mit
# genug Breite dekodieren, dass eine reine Höhen-Skalierung (kein zusätzliches
# Hochskalieren mehr) zum Füllen der Kartenhöhe reicht — deckt sich in etwa
# mit der nativen Serverauflösung (480×270), kein Qualitätsverlust vor dem
# eigentlichen Zuschneiden.
def _thumb_decode_width(frame_width: int, frame_height: int) -> int:
    """Dekodierbreite für ein 16:9-Thumbnail, das per COVER in eine Karte der
    gegebenen Größe eingepasst wird, OHNE nach dem Dekodieren nochmal
    hochskaliert werden zu müssen."""
    return max(frame_width, round(frame_height * 16 / 9))


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
/* Gruen wie im Browser (.collection-complete, rgba(22,163,74,.92)). Dieses
   Stylesheet ist ein bytes-Literal und darf deshalb nur ASCII enthalten. */
.gf-badge-ok {
  background-color: alpha(#16a34a, 0.92);
  color: #ffffff;
  border-radius: 10px;
  padding: 2px 8px;
  font-size: 0.72rem;
  font-weight: bold;
}
.gf-toggle {
  background-color: alpha(#000000, 0.6);
  border-radius: 50%;
  min-width: 26px;
  min-height: 26px;
  padding: 0;
  color: alpha(#ffffff, 0.55);
}
.gf-toggle:hover { background-color: alpha(#000000, 0.85); }
/* Gruen fuer "gesehen" (User-Wunsch 2026-09-14). Die ganze FLAECHE wird
   gruen, nicht nur der Haken - genau wie im Browser, wo
   `.watched-toggle.is-on` auf rgba(34,197,94,.9) mit weissem Haken steht
   (internal/webassets/web/style.css im Server-Repo). Ein nur eingefaerbter
   Haken war auf einem hellen Standbild kaum von ungesehen zu unterscheiden.
   Der :hover-Fall MUSS mit: `.gf-toggle:hover` weiter oben hat dieselbe
   Spezifitaet und wuerde das Gruen beim Zeigen sonst wieder mit Schwarz
   ueberschreiben.
   ACHTUNG: dieser Block ist Teil eines bytes-Literals, nur ASCII. */
.gf-toggle-on {
  background-color: alpha(#22c55e, 0.9);
  color: #ffffff;
}
.gf-toggle-on:hover { background-color: #22c55e; }
.gf-toggle-fav-on { color: #ff6b6b; }
/* Derselbe Zustand auf der Detailseite: dort ist es ein normaler
   Umschaltknopf, dessen eingedrueckter Zustand in libadwaita nur ein
   minimal dunklerer Grauton ist - als Zustandsanzeige unbrauchbar.
   Deshalb eine echte Flaeche statt nur einer Textfarbe. */
.gf-watched-on {
  background-color: #22c55e;
  color: #ffffff;
}
.gf-watched-on:hover { background-color: #16a34a; }
.gf-card-title {
  font-size: 0.92rem;
  font-weight: 500;
}
.gf-card-sub {
  font-size: 0.8rem;
  opacity: 0.6;
}
.gf-card-watched .gf-card-image { opacity: 0.55; }
/* Serien-/Kanalname-Link auf der Startseite (User-Wunsch 2026-09-13):
   KEIN Unterstrich (ausdruecklich abgewaehlt) - erkennbar wird er
   allein durch die Akzentfarbe beim Zeigen mit der Maus. */
.gf-card-link { opacity: 1; color: alpha(currentColor, 0.9); }
.gf-card-link:hover { color: @accent_color; }
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


def _image_frame(width: int, height: int) -> tuple[Gtk.Overlay, Gtk.Picture]:
    """Ein Bildbereich mit FESTER Sollgröße — und ein Bild darin, das sie nicht
    sprengen kann.

    **Der Grund ist ein Fallstrick von `Gtk.Picture`:** bei vorgegebener Höhe
    meldet es eine Naturbreite nach dem Seitenverhältnis seines Bildes, nicht
    nach seiner Sollbreite. Ein 16:9-Standbild in einer 301 Pixel hohen Kachel
    fordert 536 Pixel Breite an (nachgemessen). Ist im Fenster Platz übrig,
    verteilt die umgebende Box diesen Wunsch — und die Kacheln stehen weit
    auseinander, obwohl jede nur 168 Pixel zeichnet. Genau das war als
    "Abstände ziehen sich auf, wenn ich das Fenster größer mache" gemeldet.

    Die Lösung: die Sollgröße gibt eine leere Box als HAUPTKIND des Overlays
    vor, das Bild liegt als Overlay-Kind darüber und wird von der Messung
    ausgenommen (`set_measure_overlay(..., False)`). Damit ist die Naturbreite
    bei jeder Höhe 168 — geprüft für -1, 252 und 301."""
    frame = Gtk.Box()
    frame.set_size_request(width, height)
    overlay = Gtk.Overlay(child=frame)
    overlay.set_hexpand(False)
    overlay.set_halign(Gtk.Align.START)
    overlay.set_overflow(Gtk.Overflow.HIDDEN)
    picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
    picture.set_hexpand(False)
    picture.add_css_class("gf-card-image")
    overlay.add_overlay(picture)
    overlay.set_measure_overlay(picture, False)
    return overlay, picture


class CardWidget(Gtk.Box):
    """Wiederverwendbare Kachel. `bind()` setzt sie auf ein neues Item um."""

    def __init__(
        self,
        client: GoldfishClient,
        kind: str,
        on_activate: Callable[[dict], None] | None = None,
        on_toggle_watched: Callable[[dict, bool], None] | None = None,
        on_toggle_favorite: Callable[[dict, bool], None] | None = None,
        scroller: Gtk.ScrolledWindow | None = None,
        aspect_kind: str | None = None,
        on_open_folder: Callable[[dict], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.client = client
        self.kind = kind
        # Serien-/Kanalname klickbar (User-Wunsch 2026-09-13, nur Startseite):
        # `None` (Default, jeder andere Aufrufer wie `CardGrid`/Bibliotheks-
        # Browsing) lässt die neue Zeile unsichtbar — dort wäre "zur
        # Serienübersicht springen" innerhalb der schon geöffneten Serie
        # sinnlos bzw. verwirrend neben dem normalen Ordner-Browsing.
        self.on_open_folder = on_open_folder
        # `aspect_kind` trennt die Form der Kachel von der Bibliotheksart:
        # auf der Startseite liegen Filme, Folgen und Privatvideos in einer
        # Reihe und sollen dort gleich groß sein — ein 16:9-Standbild in einem
        # 2:3-Rahmen wird dafür mittig beschnitten (ContentFit.COVER).
        geometry = aspect_kind or kind
        # Private Bibliotheken (YouTube, eigene Videos) bekommen die breite
        # Kachel (240 statt 168 px) — ein 16:9-Bild bei 168 px ist nur 94 px
        # hoch und wirkt dadurch unscharf (Vorschaubild wird zusätzlich hoch-
        # skaliert, siehe CARD_WIDTH_WIDE-Kommentar oben). Auf der Startseite,
        # wo Privatvideos mit Filmen/Folgen in einer Reihe liegen, überschreibt
        # der Aufrufer `aspect_kind` auf "movies" o.ä. — `geometry` weicht dann
        # von "private" ab, die Zeile bleibt also einheitlich breit.
        self._frame_width = CARD_WIDTH_WIDE if geometry == "private" else CARD_WIDTH
        self.on_activate = on_activate
        self.on_toggle_watched = on_toggle_watched
        self.on_toggle_favorite = on_toggle_favorite
        self.item: dict | None = None
        # Die Bildlaufleiste, in der diese Kachel liegt — damit das Poster
        # erst geladen wird, wenn die Kachel in Sicht ist. Der Aufrufer muss
        # sie mitgeben; sie SELBST zu suchen (Elternkette hochlaufen) bringt
        # GTK zum Absturz, siehe Kopf von widgets/poster.py.
        self.scroller = scroller

        self.set_size_request(self._frame_width, -1)
        # halign ist hier der entscheidende Teil: ohne ihn streckt ein
        # Container mit Restplatz die Kachel über ihre Sollbreite. Gemessen:
        # 283 px statt 168 für die erste Kachel eines Streifens auf der
        # Startseite, während die Bildtextur korrekt 168 px breit war.
        self.set_hexpand(False)
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)

        # -- Bildbereich mit Abzeichen --
        self._frame_height = card_height_for(geometry, self._frame_width)
        self.overlay, self.picture = _image_frame(self._frame_width, self._frame_height)

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

        # -- Serien-/Kanalname (nur wenn `on_open_folder` gesetzt) --
        # Steht ÜBER dem Titel, analog zur Mac-App (dort ist der Seriennamen
        # sogar die Hauptzeile bei Folgen). Immer erzeugt, aber standardmäßig
        # unsichtbar — `bind()` zeigt sie nur, wenn `on_open_folder` gesetzt
        # UND sich für dieses Item ein Ordnername ergibt (Serie oder Kanal).
        self.folder_label = Gtk.Label(
            xalign=0,
            ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=1,
            width_request=self._frame_width,
            visible=False,
        )
        self.folder_label.add_css_class("gf-card-sub")
        if self.on_open_folder:
            self.folder_label.add_css_class("gf-card-link")
            self.folder_label.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
            folder_click = Gtk.GestureClick()
            folder_click.connect("released", self._on_folder_clicked)
            self.folder_label.add_controller(folder_click)
        self.append(self.folder_label)

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
            width_request=self._frame_width,
        )
        self.title_label.add_css_class("gf-card-title")
        self.append(self.title_label)

        self.sub_label = Gtk.Label(
            xalign=0,
            ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=1,
            width_request=self._frame_width,
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
        # **Privatvideos: das Erscheinungsdatum gehört dazu** (so wie in der
        # Mac-App). Bei einem YouTube-Kanal ist es die einzige zeitliche
        # Einordnung, die eine Kachel hergibt — und die Voreinstellung sortiert
        # diese Bibliotheken genau danach.
        if self.kind == "private":
            released = format_date(item.get("releasedAt"))
            if released:
                sub_parts.append(released)
        rel = item.get("relPath") or ""
        folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if folder and (self.kind == "private" or not sub_parts):
            # Ohne Metadaten-Titel steht in der ersten Zeile der Dateiname —
            # dann zeigt die zweite den Ordner statt ihn zu wiederholen. Bei
            # Privatvideos steht er zusätzlich zum Datum, weil er dort den
            # Kanal benennt.
            sub_parts.append(folder)
        if not sub_parts:
            sub_parts.append((item.get("container") or "").upper())
        self.sub_label.set_text(" · ".join(p for p in sub_parts if p))

        # Serien-/Kanalname klickbar (User-Wunsch 2026-09-13, nur Startseite,
        # siehe `on_open_folder`): der oberste Ordner ist bei Serien immer die
        # Serie und bei Privatvideos der Kanal — dasselbe Segment, das oben
        # schon als Fallback für `sub_label` diente, hier aber der TOP-Ordner
        # (erstes Pfadsegment), nicht der direkte Elternordner (`rsplit`
        # oben liefert bei tief verschachtelten Pfaden mehr als nur die Serie/
        # den Kanal).
        top_folder = rel.split("/", 1)[0] if "/" in rel else ""
        if self.on_open_folder and top_folder and self.kind in ("tv", "private"):
            self.folder_label.set_text(top_folder)
            self.folder_label.set_tooltip_text(f"Zur Übersicht: {top_folder}")
            self.folder_label.set_visible(True)
        else:
            self.folder_label.set_visible(False)

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

        poster_path = self.client.poster_path_for_item(item)
        # Siehe `_thumb_decode_width`-Kommentar oben: ein `/api/thumb/`-
        # Fallback braucht in einer nicht-16:9-Kartenform mehr Dekodierbreite,
        # sonst wird beim Anzeigen (ContentFit.COVER) unnötig hochskaliert.
        decode_width = (
            _thumb_decode_width(self._frame_width, self._frame_height)
            if poster_path and poster_path.startswith("/api/thumb/")
            else self._frame_width
        )
        load_poster_async(
            self.picture,
            self.client,
            poster_path,
            decode_width=decode_width,
            scroller=self.scroller,
        )

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

    def set_watched(self, watched: bool) -> None:
        """Gesehen-Status von außen setzen (Info-Karte) — Kachel sofort nachziehen.

        Ändert NICHTS am Modell/den Daten des Rasters, nur diese Kachel: ein
        Modellwechsel in einem sichtbaren Raster ist in dieser App eine bekannte
        Fehlerquelle (siehe CLAUDE.md „Ein Modellwechsel in einem SICHTBAREN
        Raster … ist nicht still"). Der Zustand wird zusätzlich im Item-Dict
        hinterlegt, damit ein erneutes Binden (Scrollen) ihn mitbringt."""
        if self.item is not None:
            self.item["watched"] = bool(watched)
        self._apply_watched(bool(watched))

    def set_favorite(self, favorite: bool) -> None:
        """Favoriten-Status von außen setzen — siehe `set_watched`."""
        if self.item is not None:
            self.item["favorite"] = bool(favorite)
        self._apply_favorite(bool(favorite))

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

    def _on_folder_clicked(self, *_args) -> None:
        if self.item and self.on_open_folder:
            self.on_open_folder(self.item)

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
    ein 🔗.

    **Kein Hinweis auf vom Auto-Scan ausgenommene Ordner** (das 🚫 des
    Browsers): der Auto-Scan ist reine Serververwaltung, und die bleibt in
    allen Goldfish-Clients dem Browser überlassen — auf einer Kachel hier
    wäre das Zeichen nur ein unerklärliches Rätsel."""

    def __init__(
        self,
        client: GoldfishClient,
        kind: str,
        on_activate: Callable[[dict], None] | None = None,
        scroller: Gtk.ScrolledWindow | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.client = client
        self.kind = kind
        # Gleicher Grund wie bei CardWidget: 16:9-Ordnerbilder brauchen die
        # breite Kachel, sonst sind sie bei 168 px kaum zu erkennen.
        self._frame_width = CARD_WIDTH_WIDE if kind == "private" else CARD_WIDTH
        self.on_activate = on_activate
        self.folder: dict | None = None
        self.scroller = scroller
        self.set_size_request(self._frame_width, -1)

        self._frame_height = card_height_for(kind, self._frame_width)
        self.overlay, self.picture = _image_frame(self._frame_width, self._frame_height)

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
            width_request=self._frame_width,
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

        merged = bool(folder.get("mergedFolders"))
        self.marker_label.set_text("🔗" if merged else "")
        self.marker_label.set_visible(merged)

        # Ordner-Poster: Serienposter wenn zugeordnet, sonst Vorschaubild
        # eines enthaltenen Videos.
        path = None
        if folder.get("metadataId"):
            path = f"/api/poster/metadata/{folder['metadataId']}"
        elif folder.get("thumbItemId"):
            path = f"/api/thumb/{folder['thumbItemId']}"
        # Siehe `_thumb_decode_width`-Kommentar bei CardWidget.bind: dasselbe
        # Unschärfe-Problem betraf Ordner-Kacheln (Serien ohne Poster,
        # Privat-Ordner) genauso.
        decode_width = (
            _thumb_decode_width(self._frame_width, self._frame_height)
            if path and path.startswith("/api/thumb/")
            else self._frame_width
        )
        load_poster_async(self.picture, self.client, path, decode_width=decode_width, scroller=self.scroller)

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
    rechts, `corner` oben links. `corner_ok` färbt die Ecke grün — für
    "vollständig" im Sinne einer erledigten Sache, nicht als weiteres
    schwarzes Abzeichen.
    """

    def __init__(
        self,
        client: GoldfishClient,
        image_path: str | None,
        title: str,
        subtitle: str = "",
        badge: str = "",
        corner: str = "",
        corner_ok: bool = False,
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

        overlay, picture = _image_frame(width, card_height_for(aspect, width))

        self._corner_label: Gtk.Label | None = None
        if corner:
            label = Gtk.Label(label=corner, halign=Gtk.Align.START, valign=Gtk.Align.START, margin_start=6, margin_top=6)
            label.add_css_class("gf-badge-ok" if corner_ok else "gf-badge")
            overlay.add_overlay(label)
            self._corner_label = label
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

    def set_corner_mark(self, text: str) -> None:
        """Die Ecke oben links nachträglich setzen/entfernen.

        Eine `SimpleCard` wird einmal gebaut und nicht wiederverwendet — ohne
        diesen Weg könnte sie nach einer Wiedergabe nicht nachziehen (der grüne
        Gesehen-Haken entstand sonst erst beim nächsten Aufbau der Ansicht,
        User-Report 2026-09-23). Kacheln, die ohne `corner` gebaut wurden
        (z. B. nicht vorhandene Folgen), haben keine Ecke — dort passiert nichts.
        """
        if self._corner_label is None:
            return
        self._corner_label.set_text(text)
        self._corner_label.set_visible(bool(text))



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


class AlbumCardWidget(Gtk.Box):
    """Wiederverwendbare Album-Kachel für ein Raster mit Recycling.

    Es gibt sie zusätzlich zu `SimpleCard`, weil eine Musikbibliothek sehr
    viele Alben hat: 2717 in der hiesigen Sammlung. Als einzeln gebaute
    `SimpleCard` in einer FlowBox kostet das **4,65 Sekunden blockierten
    Hauptablauf** — nachgemessen. In dieser Zeit stand auch die laufende
    Musikwiedergabe scheinbar still, weil die Anzeige nicht mehr aktualisiert
    wurde. Mit Recycling entstehen nur die sichtbaren Kacheln.
    """

    def __init__(
        self,
        client: GoldfishClient,
        on_activate: Callable[[dict], None] | None = None,
        scroller: Gtk.ScrolledWindow | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        ensure_card_css()
        self.client = client
        self.on_activate = on_activate
        self.album: dict | None = None
        self.scroller = scroller
        self.set_size_request(CARD_WIDTH, -1)
        self.set_hexpand(False)
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)

        self.overlay, self.picture = _image_frame(CARD_WIDTH, card_height_for("music"))

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
        self.picture.set_cursor(Gdk.Cursor.new_from_name("pointer", None))

    def bind(self, album: dict) -> None:
        self.album = album
        title = album.get("album") or ""
        self.title_label.set_text(title)
        self.set_tooltip_text(title)
        parts = [str(p) for p in (album.get("artist"), album.get("year") or "", album.get("genre") or "") if p]
        self.sub_label.set_text(" · ".join(parts))
        count = album.get("trackCount") or 0
        self.count_label.set_text(str(count))
        self.count_label.set_visible(count > 0)
        load_poster_async(
            self.picture,
            self.client,
            self.client.album_cover_path(int(album["id"])),
            decode_width=CARD_WIDTH,
            scroller=self.scroller,
        )

    def unbind(self) -> None:
        self.album = None
        load_poster_async(self.picture, self.client, None)

    def _on_clicked(self, *_args) -> None:
        if self.album and self.on_activate:
            self.on_activate(self.album)


class LocalCardWidget(Gtk.Box):
    """Wiederverwendbare Kachel für ein Video aus einer lokalen Bibliothek.

    Im Breitformat wie Privatvideos, weil es dort keine Poster gibt. Das Bild
    stammt, falls vorhanden, aus dem Zwischenspeicher des Dateimanagers; sonst
    bleibt die Fläche leer. Eigene Vorschaubilder zu erzeugen bräuchte ffmpeg,
    das auf dem Zielsystem nicht vorausgesetzt werden kann.
    """

    def __init__(
        self,
        client: GoldfishClient,
        on_activate: Callable[[dict], None] | None = None,
        scroller: Gtk.ScrolledWindow | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        ensure_card_css()
        self.client = client
        self.on_activate = on_activate
        self.video: dict | None = None
        self.scroller = scroller
        self.set_size_request(CARD_WIDTH_WIDE, -1)
        self.set_hexpand(False)
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)

        self.overlay, self.picture = _image_frame(CARD_WIDTH_WIDE, card_height_for("private", CARD_WIDTH_WIDE))

        self.res_label = Gtk.Label(halign=Gtk.Align.START, valign=Gtk.Align.END, margin_start=6, margin_bottom=6)
        self.res_label.add_css_class("gf-badge")
        self.overlay.add_overlay(self.res_label)

        self.duration_label = Gtk.Label(halign=Gtk.Align.END, valign=Gtk.Align.END, margin_end=6, margin_bottom=6)
        self.duration_label.add_css_class("gf-badge")
        self.overlay.add_overlay(self.duration_label)
        self.append(self.overlay)

        self.title_label = Gtk.Label(
            xalign=0,
            ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=1,
            width_request=CARD_WIDTH_WIDE,
        )
        self.title_label.add_css_class("gf-card-title")
        self.append(self.title_label)

        self.sub_label = Gtk.Label(
            xalign=0,
            ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=1,
            width_request=CARD_WIDTH_WIDE,
        )
        self.sub_label.add_css_class("gf-card-sub")
        self.append(self.sub_label)

        click = Gtk.GestureClick()
        click.connect("released", self._on_clicked)
        self.picture.add_controller(click)
        self.picture.set_cursor(Gdk.Cursor.new_from_name("pointer", None))

    def bind(self, video: dict) -> None:
        from ..formatting import format_duration, format_resolution, format_size
        self.video = video
        title = video.get("title") or ""
        self.title_label.set_text(title)
        self.set_tooltip_text(video.get("path") or title)
        self.sub_label.set_text(format_size(video.get("sizeBytes") or 0))

        res = format_resolution(video.get("width") or 0, video.get("height") or 0)
        self.res_label.set_text(res)
        self.res_label.set_visible(bool(res))
        duration = format_duration(video.get("durationSec") or 0)
        self.duration_label.set_text(duration)
        self.duration_label.set_visible(bool(duration))

        # **Nur den Pfad weitergeben, nichts nachschlagen.** Ob es ein Bild
        # des Dateimanagers gibt oder ob eines erzeugt werden muss, entscheidet
        # der Ladefaden (`gstthumb://`) — die Auskunft des Dateimanagers geht
        # über Gio und kann auf einem langsamen Datenträger dauern; im
        # Hauptablauf wären das bei den rund 385 Kacheln, die GTK anlegt,
        # spürbare Aussetzer.
        path = video.get("path") or ""
        load_poster_async(
            self.picture,
            self.client,
            f"gstthumb://{path}" if path else None,
            decode_width=CARD_WIDTH_WIDE,
            scroller=self.scroller,
        )

    def unbind(self) -> None:
        self.video = None
        load_poster_async(self.picture, self.client, None)

    def _on_clicked(self, *_args) -> None:
        if self.video and self.on_activate:
            self.on_activate(self.video)
