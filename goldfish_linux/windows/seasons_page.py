"""Staffelansicht einer Serie.

Zwei Ebenen: erst die Staffeln als Kacheln mit "x/y Folgen", nach einem Klick
die Folgen dieser Staffel. Vorhandene Folgen tragen ihr Standbild und öffnen
die Detailansicht; fehlende erscheinen zurückgeblendet mit dem Hinweis "Fehlt",
damit man sieht, was der Sammlung noch abgeht.

**Rückfall ohne Staffelstruktur:** liefert der Server ein leeres
`seasons`-Feld, ist das kein Fehler — der Ordner ist entweder nicht zugeordnet
oder seine Struktur passt nicht zu TMDB-Staffeln (Tatort etwa hat Unterordner
je Ermittlerduo, keine Sendejahre). Dann übernimmt die gewöhnliche
Ordneransicht, wie es auch der Browser tut. Der Merker dafür wird pro Ordner
gespeichert und beim nächsten Öffnen berücksichtigt.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from ..api import GoldfishAPIError  # noqa: E402
from ..formatting import format_duration  # noqa: E402
from ..widgets.card import SimpleCard, card_flow  # noqa: E402
from ..widgets.cast import CastStrip  # noqa: E402
from ..widgets.poster import load_poster_async  # noqa: E402


class SeasonsPage(Adw.NavigationPage):
    def __init__(self, ctx, nav_view: Adw.NavigationView, library: dict, folder: str):
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())

        super().__init__(title=folder.rsplit("/", 1)[-1], tag=f"seasons-{library['id']}-{folder}", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.library = library
        self.folder = folder
        self.toolbar_view = toolbar_view
        self.show: dict = {}

        spinner = Gtk.Spinner(width_request=48, height_request=48)
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        wrap.append(spinner)
        toolbar_view.set_content(wrap)
        spinner.start()

        threading.Thread(target=self._load, daemon=True).start()

    # -- Laden -----------------------------------------------------------

    def _load(self) -> None:
        try:
            data = self.ctx.client.seasons(self.library["id"], self.folder)
        except GoldfishAPIError as exc:
            GLib.idle_add(self._show_error, str(exc))
            return
        GLib.idle_add(self._apply, data)

    def _apply(self, data: dict) -> bool:
        self.show = data.get("show") or {}
        seasons = data.get("seasons") or []
        if self.show.get("title"):
            self.set_title(self.show["title"])

        if not seasons:
            # Ohne Staffelstruktur zurück zur Ordneransicht — der Aufrufer hat
            # den Merker schon gesetzt, hier wird nur ersetzt.
            self._fall_back_to_folder()
            return False

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.append(self._build_header())

        flow = card_flow()
        for season in seasons:
            flow.append(self._season_card(season))
        box.append(flow)

        scrolled = Gtk.ScrolledWindow(vexpand=True, child=box)
        self.toolbar_view.set_content(scrolled)
        return False

    def _fall_back_to_folder(self) -> None:
        from .browse_page import BrowsePage

        self.ctx.view_prefs.set_season_view(self.library["id"], self.folder, False)
        page = BrowsePage(self.ctx, self.nav_view, self.library, folder=self.folder)
        # Diese Seite durch die Ordneransicht ersetzen, damit der
        # Zurück-Schritt nicht in einer leeren Staffelansicht landet.
        self.nav_view.replace([*self._stack_without_self(), page])
        root = self.get_root()
        if hasattr(root, "show_toast"):
            root.show_toast("Keine Staffelstruktur erkannt – zeige die Ordneransicht.")

    def _stack_without_self(self) -> list[Adw.NavigationPage]:
        stack = self.nav_view.get_navigation_stack()
        return [stack.get_item(i) for i in range(stack.get_n_items()) if stack.get_item(i) is not self]

    # -- Kopfbereich -----------------------------------------------------

    def _build_header(self) -> Gtk.Widget:
        show = self.show
        row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=18,
            margin_top=16,
            margin_start=16,
            margin_end=16,
            margin_bottom=8,
        )

        poster = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        poster.set_size_request(150, 225)
        poster.set_valign(Gtk.Align.START)
        poster.add_css_class("gf-card-image")
        if show.get("metadataId"):
            # Über den eigenen Bildweg des Servers, nicht direkt von TMDB: nur
            # so erscheint auch ein selbst hochgeladenes Poster.
            load_poster_async(poster, self.ctx.client, f"/api/poster/metadata/{show['metadataId']}")
        row.append(poster)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        title = Gtk.Label(label=show.get("title") or self.folder, xalign=0, wrap=True)
        title.add_css_class("title-2")
        text.append(title)

        facts = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for value in (
            _year_range(show),
            _STATUS.get(show.get("status") or "", show.get("status") or ""),
            _counts(show),
            f"★ {show['rating']:.1f}" if show.get("rating") else "",
        ):
            if value:
                chip = Gtk.Label(label=value)
                chip.add_css_class("gf-badge")
                facts.append(chip)
        text.append(facts)

        genres = show.get("genres") or []
        if genres:
            label = Gtk.Label(label=" · ".join(genres), xalign=0, wrap=True)
            label.add_css_class("dim-label")
            text.append(label)

        overview = show.get("overview")
        if overview:
            text.append(Gtk.Label(label=overview, xalign=0, wrap=True, lines=6, ellipsize=Pango.EllipsizeMode.END))
        row.append(text)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.append(row)
        if show.get("metadataId"):
            strip = CastStrip(self.ctx.client, int(show["metadataId"]), on_person=self._open_person)
            strip.set_margin_start(16)
            strip.set_margin_end(16)
            outer.append(strip)
        return outer

    # -- Kacheln ---------------------------------------------------------

    def _season_card(self, season: dict) -> Gtk.Widget:
        number = season.get("seasonNumber")
        owned, total = season.get("ownedCount") or 0, season.get("total") or 0
        watched = season.get("watchedCount") or 0
        name = season.get("name") or (f"Staffel {number}" if number else "Extras")
        badge = f"{owned}/{total}" if total else str(owned)
        return SimpleCard(
            self.ctx.client,
            self.ctx.client.tmdb_image_url(season.get("posterPath")),
            name,
            subtitle=f"{watched} gesehen" if watched else "",
            badge=badge,
            corner="✓" if total and owned >= total else "",
            aspect="tv",
            on_click=lambda s=season: self._open_season(s),
            tooltip=f"{owned} von {total} Folgen vorhanden" if total else "",
        )

    def _open_season(self, season: dict) -> None:
        self.nav_view.push(SeasonEpisodesPage(self.ctx, self.nav_view, self.library, self.folder, self.show, season))

    def _open_person(self, member: dict) -> None:
        from .person_page import PersonPage

        if member.get("tmdbId"):
            self.nav_view.push(PersonPage(self.ctx, self.nav_view, int(member["tmdbId"]), member.get("name") or ""))

    def _show_error(self, message: str) -> bool:
        self.toolbar_view.set_content(
            Adw.StatusPage(icon_name="dialog-error-symbolic", title="Fehler", description=message)
        )
        return False


class SeasonEpisodesPage(Adw.NavigationPage):
    """Die Folgen einer Staffel.

    Alle Anzeigedaten stecken schon in der Antwort der Staffelabfrage — Titel,
    Beschreibung, Standbild, Laufzeit, Auflösung und ob die Folge vorhanden
    ist. Deshalb wird hier nichts nachgeladen; erst der Klick auf eine
    vorhandene Folge holt das Item für die Detailansicht.
    """

    def __init__(self, ctx, nav_view: Adw.NavigationView, library: dict, folder: str, show: dict, season: dict):
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        number = season.get("seasonNumber")
        title = season.get("name") or (f"Staffel {number}" if number else "Extras")

        super().__init__(title=title, tag=f"season-{library['id']}-{folder}-{number}", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.library = library
        self.folder = folder
        self.show = show
        self.season = season

        flow = card_flow()
        episodes = season.get("episodes") or []
        for episode in episodes:
            flow.append(self._episode_card(episode))
        if not episodes:
            toolbar_view.set_content(
                Adw.StatusPage(icon_name="folder-open-symbolic", title="Keine Folgen", description="Für diese Staffel liegen keine Angaben vor.")
            )
            return
        toolbar_view.set_content(Gtk.ScrolledWindow(vexpand=True, child=flow))

    def _episode_card(self, episode: dict) -> Gtk.Widget:
        number = episode.get("episode")
        owned = bool(episode.get("owned"))
        label = f"E{number:02d}" if isinstance(number, int) else ""
        title = f"{label} {episode.get('title') or ''}".strip()

        # Vorhandene Folgen zeigen das eigene Vorschaubild, fehlende das
        # Standbild von TMDB — sonst bliebe die Kachel leer.
        image = (
            f"/api/thumb/{episode['itemId']}"
            if owned and episode.get("itemId")
            else self.ctx.client.tmdb_image_url(episode.get("stillPath"))
        )
        subtitle = format_duration(episode.get("durationSec") or 0) if owned else (episode.get("airDate") or "")

        return SimpleCard(
            self.ctx.client,
            image,
            title or "Folge",
            subtitle=subtitle,
            badge="" if owned else "Fehlt",
            corner="✓" if episode.get("watched") else "",
            aspect="private",  # Standbilder sind im Breitformat
            dimmed=not owned,
            on_click=(lambda e=episode: self._open_episode(e)) if owned else None,
            tooltip=episode.get("overview") or "",
        )

    def _open_episode(self, episode: dict) -> None:
        item_id = episode.get("itemId")
        if not item_id:
            return
        # Die Folgen dieser Staffel als Warteschlange, damit danach von selbst
        # weitergespielt wird.
        queue_ids = [e.get("itemId") for e in (self.season.get("episodes") or []) if e.get("owned") and e.get("itemId")]

        def worker() -> None:
            try:
                items = [self.ctx.client.item(int(i)) for i in queue_ids] if len(queue_ids) <= 30 else []
                item = next((i for i in items if int(i["id"]) == int(item_id)), None) or self.ctx.client.item(int(item_id))
            except GoldfishAPIError as exc:
                GLib.idle_add(self._toast, str(exc))
                return
            GLib.idle_add(self._push_detail, item, items)

        threading.Thread(target=worker, daemon=True).start()

    def _push_detail(self, item: dict, queue: list[dict]) -> bool:
        from .detail_page import DetailPage

        self.nav_view.push(DetailPage(self.ctx, self.nav_view, item, queue=queue))
        return False

    def _toast(self, message: str) -> bool:
        root = self.get_root()
        if hasattr(root, "show_toast"):
            root.show_toast(message)
        return False


# -- Hilfsmittel --------------------------------------------------------

_STATUS = {
    "Ended": "Beendet",
    "Returning Series": "Laufend",
    "Canceled": "Abgesetzt",
    "In Production": "In Produktion",
    "Planned": "Geplant",
    "Pilot": "Pilot",
}


def _year_range(show: dict) -> str:
    first = (show.get("firstAirDate") or "")[:4]
    last = (show.get("lastAirDate") or "")[:4]
    if first and last and first != last:
        return f"{first}–{last}"
    return first or last


def _counts(show: dict) -> str:
    seasons = show.get("numberOfSeasons") or 0
    episodes = show.get("numberOfEpisodes") or 0
    parts = []
    if seasons:
        parts.append(f"{seasons} Staffeln" if seasons != 1 else "1 Staffel")
    if episodes:
        parts.append(f"{episodes} Folgen")
    return " · ".join(parts)
