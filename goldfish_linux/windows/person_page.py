"""Alles, worin eine Person mitspielt — bibliotheksübergreifend.

Schlanke Fassung: Portrait, Lebensdaten und das Raster der vorhandenen Titel.
Die vollständige Filmografie von TMDB (also auch die Titel, die NICHT im
Bestand sind, im Browser ausgegraut mit "nicht vorhanden") folgt in einer
späteren Etappe zusammen mit den übrigen großen Ansichten.

Die Liste kommt über `GET /api/items?personId=<tmdbId>` und ist damit
automatisch auf die Bibliotheken beschränkt, die dieser Benutzer sehen darf —
die Rechteprüfung sitzt im Server, nicht hier.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from ..api import GoldfishAPIError  # noqa: E402
from ..variants import group_variants  # noqa: E402
from ..widgets.card import SimpleCard, card_flow  # noqa: E402
from ..widgets.grid import CardGrid  # noqa: E402
from ..widgets.poster import load_poster_async  # noqa: E402


class PersonPage(Adw.NavigationPage):
    def __init__(self, ctx, nav_view: Adw.NavigationView, tmdb_id: int, name: str):
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())

        super().__init__(title=name, tag=f"person-{tmdb_id}", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.tmdb_id = int(tmdb_id)
        self.name = name
        self.toolbar_view = toolbar_view

        spinner = Gtk.Spinner()
        spinner.set_size_request(48, 48)
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        wrap.append(spinner)
        toolbar_view.set_content(wrap)
        spinner.start()

        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        details: dict = {}
        items: list[dict] = []
        error: str | None = None
        try:
            # Bio-Daten sind Beigabe: fehlt TMDB oder schlägt der Aufruf fehl,
            # bleibt die Titelliste trotzdem nützlich.
            try:
                details = self.ctx.client.person(self.tmdb_id)
            except GoldfishAPIError:
                details = {}
            items = self.ctx.client.items(person_id=self.tmdb_id, sort="released", sort_dir="desc")
        except GoldfishAPIError as exc:
            error = str(exc)
        GLib.idle_add(self._apply, details, items, error)

    def _apply(self, details: dict, items: list[dict], error: str | None) -> bool:
        if error:
            self.toolbar_view.set_content(
                Adw.StatusPage(icon_name="dialog-error-symbolic", title="Fehler", description=error)
            )
            return False

        # Der Seitentitel kam bisher vom aufrufenden Besetzungseintrag. Sobald
        # der Server geantwortet hat, ist dessen Name die verlässlichere
        # Quelle — sonst können Kopfzeile und Titel auseinanderlaufen.
        if details.get("name"):
            self.set_title(details["name"])

        # User-Report 2026-09-20: "die Treffer in den Serien zeigen nicht die
        # Serie, sondern wieder die einzelnen Folgen. Es soll so sein, dass
        # nur die Serie gezeigt wird, und auf der Kachel steht, in wievielen
        # Folgen er enthalten ist." Gleiche Regel wie im Browser
        # (grid.js renderPersonFilterBranch): Filme normal, Episoden pro Show
        # (Parent-Show-metadataId, sonst libraryId+Ordner als Fallback-Schlüssel
        # für unmatched Serien) zu EINER Sammelkachel mit Folgenzahl.
        movies = [it for it in items if (it.get("metadata") or {}).get("tmdbType") == "movie"]
        episodes = [it for it in items if (it.get("metadata") or {}).get("tmdbType") == "episode"]

        shows: dict[object, dict] = {}
        for ep in episodes:
            rel = ep.get("relPath") or ""
            folder = rel.split("/", 1)[0] if "/" in rel else ""
            show_meta_id = (ep.get("metadata") or {}).get("parentId") or 0
            key = show_meta_id or f"{ep.get('libraryId')}|{folder}"
            entry = shows.get(key)
            if entry is None:
                entry = {
                    "folder": folder,
                    "libraryId": ep.get("libraryId"),
                    "showMetaId": show_meta_id,
                    "fallbackThumbId": ep.get("id"),
                    "episodes": [],
                }
                shows[key] = entry
            entry["episodes"].append(ep)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.append(self._build_header(details))

        # Eine Kachel pro Film, auch wenn mehrere Auflösungen vorhanden sind
        # (Browser `groupVariants`) — siehe ..variants.
        movies = group_variants(movies)
        self.shown_items = movies

        if movies:
            grid = CardGrid(
                self.ctx.client,
                "movies",
                on_item=self._open_detail,
                on_toggle_watched=lambda it, w: self._state_call(lambda: self.ctx.client.set_watched(it["id"], w)),
                on_toggle_favorite=lambda it, f: self._state_call(lambda: self.ctx.client.set_favorite(it["id"], f)),
            )
            grid.set_content([], movies)
            outer.append(grid)

        if shows:
            h = Gtk.Label(label="📺 Serien", xalign=0, margin_start=16, margin_top=12)
            h.add_css_class("title-3")
            outer.append(h)
            flow = card_flow()
            for show in shows.values():
                count = len(show["episodes"])
                image_path = (
                    f"/api/poster/metadata/{show['showMetaId']}"
                    if show["showMetaId"]
                    else f"/api/thumb/{show['fallbackThumbId']}"
                )
                flow.insert(
                    SimpleCard(
                        self.ctx.client,
                        image_path,
                        title=show["folder"] or "Serie",
                        subtitle=f"{count} Folge{'n' if count != 1 else ''}",
                        on_click=lambda s=show: self._open_show_episodes(s),
                    ),
                    -1,
                )
            outer.append(flow)

        if not movies and not shows:
            outer.append(
                Adw.StatusPage(icon_name="emblem-photos-symbolic", title="Nichts gefunden", description="")
            )

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_child(outer)
        self.toolbar_view.set_content(scrolled)
        return False

    def _open_show_episodes(self, show: dict) -> None:
        """Klick auf eine Serien-Sammelkachel: zeigt NUR die Folgen dieser
        Serie, in denen die Person mitspielt (gleiche Bedienung wie im
        Browser — kein voller Serien-Ordner, keine erneute Server-Anfrage
        nötig, die Episoden liegen schon vor)."""
        episodes = group_variants(show["episodes"])
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        page = Adw.NavigationPage(
            title=show["folder"] or "Serie", tag=f"person-show-{show['showMetaId']}", child=toolbar_view
        )
        grid = CardGrid(
            self.ctx.client,
            "tv",
            on_item=lambda it: self._open_episode_detail(it, episodes),
            on_toggle_watched=lambda it, w: self._state_call(lambda: self.ctx.client.set_watched(it["id"], w)),
            on_toggle_favorite=lambda it, f: self._state_call(lambda: self.ctx.client.set_favorite(it["id"], f)),
        )
        grid.set_content([], episodes)
        toolbar_view.set_content(grid)
        self.nav_view.push(page)

    def _open_episode_detail(self, item: dict, queue: list[dict]) -> None:
        from .detail_page import DetailPage

        self.nav_view.push(DetailPage(self.ctx, self.nav_view, item, queue=queue))

    def _build_header(self, details: dict) -> Gtk.Widget:
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=16,
            margin_top=16,
            margin_bottom=8,
            margin_start=16,
            margin_end=16,
        )

        photo = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        photo.set_size_request(96, 96)
        photo.set_overflow(Gtk.Overflow.HIDDEN)
        photo.add_css_class("gf-cast-photo")
        photo.set_valign(Gtk.Align.START)
        load_poster_async(photo, self.ctx.client, self.ctx.client.person_profile_path(self.tmdb_id))
        box.append(photo)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        name = Gtk.Label(label=details.get("name") or self.name, xalign=0, wrap=True)
        name.add_css_class("title-2")
        text.append(name)

        facts = [f for f in (details.get("birthday"), details.get("placeOfBirth")) if f]
        if details.get("deathday"):
            facts.append(f"gestorben {details['deathday']}")
        if facts:
            sub = Gtk.Label(label=" · ".join(facts), xalign=0, wrap=True)
            sub.add_css_class("dim-label")
            text.append(sub)

        bio = (details.get("biography") or "").strip()
        if bio:
            # Lange Biografien nicht die halbe Seite füllen lassen — die Titel
            # sind hier die Hauptsache.
            label = Gtk.Label(label=bio, xalign=0, wrap=True, lines=4, ellipsize=Pango.EllipsizeMode.END)
            label.add_css_class("dim-label")
            text.append(label)
        box.append(text)
        return box

    def _open_detail(self, item: dict) -> None:
        from .detail_page import DetailPage

        self.nav_view.push(DetailPage(self.ctx, self.nav_view, item, queue=getattr(self, "shown_items", [])))

    def _state_call(self, call) -> None:
        def worker() -> None:
            try:
                call()
            except GoldfishAPIError:
                pass  # Server bleibt die Wahrheit; beim nächsten Laden stimmt es wieder

        threading.Thread(target=worker, daemon=True).start()
