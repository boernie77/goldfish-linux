"""Detailansicht eines Titels.

Zeigt Poster, Beschreibung und Kenndaten, lässt Tonspur, Untertitel und
Qualität vorwählen und bietet Wiedergabe, Trailer, Download sowie die Schalter
für Gesehen und Favorit. Die Besetzung ist anklickbar.

Die Auswahlmenüs für Ton, Untertitel und Qualität werden aus
`GET /api/playback/{id}` gefüllt und NICHT aus `item["streams"]`: nur der
Wiedergabe-Endpunkt kennt auch die erzeugten Untertitel (Whisper und OCR).
Derselbe Grund, aus dem der Browser es ebenso macht.
"""

from __future__ import annotations

import json
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..api import GoldfishAPIError, PlaybackProfile  # noqa: E402
from ..formatting import (  # noqa: E402
    audio_stream_label,
    format_duration,
    format_resolution,
    format_size,
    is_displayable_subtitle,
    subtitle_stream_label,
)
from ..widgets.card import ensure_card_css  # noqa: E402
from ..widgets.cast import CastStrip  # noqa: E402
from ..widgets.poster import load_poster_async  # noqa: E402
from .player_window import close_player, open_player  # noqa: E402


def _icon_label(icon: str, text: str) -> Gtk.Box:
    """Symbol und Text in einem Knopf — für die Hauptaktion, die beschriftet
    bleibt."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.CENTER)
    box.append(Gtk.Image.new_from_icon_name(icon))
    box.append(Gtk.Label(label=text))
    return box


class DetailPage(Adw.NavigationPage):
    def __init__(self, ctx, nav_view: Adw.NavigationView, item: dict, queue: list[dict] | None = None):
        metadata = item.get("metadata") or {}
        title = metadata.get("title") or item.get("title") or "Unbenannt"

        # Adw.NavigationPage.child ist construct-only — der Widget-Baum muss vor
        # super().__init__() feststehen. Der Inhalt darf danach noch befüllt
        # werden, nur der Zielcontainer selbst nicht mehr getauscht.
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        clamp = Adw.Clamp(maximum_size=820, margin_top=20, margin_bottom=24, margin_start=18, margin_end=18)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        clamp.set_child(box)
        scrolled.set_child(clamp)
        toolbar_view.set_content(scrolled)

        super().__init__(title=title, tag=f"detail-{item['id']}", child=toolbar_view)
        # Die Plaketten und der Posterrahmen nutzen das Kachel-Stylesheet. Es
        # wird zwar beim ersten Raster geladen, aber darauf soll sich diese
        # Seite nicht verlassen — der Aufruf ist idempotent.
        ensure_card_css()
        self.ctx = ctx
        self.nav_view = nav_view
        self.item = item
        self.item_id = int(item["id"])
        self.box = box
        # Die Liste, aus der dieser Titel geöffnet wurde — damit am Ende von
        # selbst der nächste läuft (Folge auf Folge). Ohne Liste bleibt es bei
        # diesem einen Titel.
        self.queue = queue or []

        # Vorwahl für den Player; wird von den Menüs unten gesetzt.
        self.chosen_profile = ""
        self.chosen_audio: int | None = None
        self.chosen_subtitle: dict | None = None
        self.variants: list[dict] = []
        self._audio_streams: list[dict] = []
        self._subtitle_streams: list[dict] = []
        self._profiles: list[PlaybackProfile] = []

        box.append(self._build_head())
        self.stream_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(self.stream_box)
        # Der Download-Bereich sitzt IN der Aktionszeile, nicht darunter: er
        # gehört zu denselben Handgriffen wie Gesehen, Favorit und Playlist
        # (so gewünscht). Er bleibt ein eigener Behälter, weil sein Inhalt
        # wechselt — Knopf, Fortschrittsbalken oder "offline abspielen" —
        # und muss deshalb VOR der Aktionszeile bestehen.
        self.download_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, valign=Gtk.Align.CENTER)
        box.append(self._build_buttons())
        self._refresh_download_ui()

        if metadata.get("id"):
            box.append(CastStrip(ctx.client, int(metadata["id"]), on_person=self._open_person))

        # Spuren, Qualitätsstufen, Varianten und Trailer-Verfügbarkeit brauchen
        # eigene Abfragen — die Seite steht schon, das kommt nach.
        threading.Thread(target=self._load_extras, daemon=True).start()

    # -- Aufbau ----------------------------------------------------------

    def _build_head(self) -> Gtk.Widget:
        metadata = self.item.get("metadata") or {}
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)

        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        self.picture.set_size_request(180, 270)
        self.picture.set_valign(Gtk.Align.START)
        self.picture.add_css_class("gf-card-image")
        load_poster_async(self.picture, self.ctx.client, self.ctx.client.poster_path_for_item(self.item))
        row.append(self.picture)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        title_label = Gtk.Label(
            label=metadata.get("title") or self.item.get("title") or "Unbenannt",
            xalign=0,
            wrap=True,
        )
        title_label.add_css_class("title-2")
        text.append(title_label)

        self.meta_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._fill_meta_row()
        text.append(self.meta_row)

        genres = metadata.get("genres") or ""
        if genres:
            genre_label = Gtk.Label(label=_pretty_genres(genres), xalign=0, wrap=True)
            genre_label.add_css_class("dim-label")
            text.append(genre_label)

        overview = metadata.get("overview")
        if overview:
            text.append(Gtk.Label(label=overview, xalign=0, wrap=True, justify=Gtk.Justification.LEFT))

        row.append(text)
        return row

    def _fill_meta_row(self) -> None:
        """Kenndaten als Reihe kleiner Plaketten. Wird beim Wechsel der Version
        neu gefüllt, weil Auflösung, Laufzeit und Größe zur konkreten Datei
        gehören — Jahr, Bewertung und Freigabe dagegen zum Titel."""
        child = self.meta_row.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.meta_row.remove(child)
            child = nxt

        metadata = self.item.get("metadata") or {}
        for text in (
            str(metadata.get("year")) if metadata.get("year") else "",
            format_resolution(self.item.get("width") or 0, self.item.get("height") or 0),
            format_duration(self.item.get("durationSec") or 0),
            format_size(self.item.get("sizeBytes") or 0),
            (self.item.get("container") or "").upper(),
        ):
            if text:
                self.meta_row.append(_chip(text))

        age = metadata.get("ageRating")
        if age:
            self.meta_row.append(_chip(f"FSK {age}"))
        rating = metadata.get("rating") or 0
        if rating:
            self.meta_row.append(_chip(f"★ {rating:.1f}"))

    def _build_buttons(self) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.START)
        # Ein Musiktitel gehört in die Abspielleiste, nicht ins Videofenster —
        # dieselbe Regel wie überall sonst in dieser App (und im Browser).
        # Diese Seite bekommt man für Musik über den Ordner-Browser einer
        # Musikbibliothek.
        self.is_music = self.ctx.library_kind(self.item.get("libraryId")) == "music"

        # **Nur die Hauptaktion trägt Text.** Alles andere sind Symbolknöpfe
        # mit Kurzhilfe — so wie in den übrigen Apps und im Browser, wo diese
        # Aktionen ebenfalls als Symbole in einer Reihe stehen. Vorher las sich
        # die Zeile wie ein Satz ("Als gesehen markieren ♡ Favorit 📋 Zu
        # Playlist ⬇ Herunterladen") und war entsprechend breit.
        play = Gtk.Button(icon_name="media-playback-start-symbolic", label="Abspielen")
        play.set_child(_icon_label("media-playback-start-symbolic", "Abspielen"))
        play.add_css_class("suggested-action")
        play.add_css_class("pill")
        play.connect("clicked", lambda *_: self._play_music() if self.is_music else self._play_with_resume_check())
        row.append(play)

        if self.is_music:
            enqueue = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
            enqueue.add_css_class("circular")
            enqueue.set_tooltip_text("An die laufende Warteschlange anhängen")
            enqueue.connect("clicked", lambda *_: self._enqueue_music())
            row.append(enqueue)

        self.trailer_button = Gtk.Button(
            icon_name="camera-video-symbolic",
            visible=False,
            valign=Gtk.Align.CENTER,
            tooltip_text="Trailer ansehen",
        )
        self.trailer_button.add_css_class("circular")
        self.trailer_button.connect("clicked", self._on_trailer_clicked)
        row.append(self.trailer_button)

        self.watched_toggle = Gtk.ToggleButton(
            icon_name="object-select-symbolic",
            active=bool(self.item.get("watched")),
            valign=Gtk.Align.CENTER,
        )
        self.watched_toggle.add_css_class("circular")
        self._update_watched_label()
        self.watched_toggle.connect("toggled", self._on_watched_toggled)
        # "Gesehen" ist ein Videobegriff; für Musik führt der Server ihn
        # bewusst nicht (der Browser blendet den Filter dort ebenfalls aus).
        if not self.is_music:
            row.append(self.watched_toggle)

        self.favorite_toggle = Gtk.ToggleButton(
            icon_name="emblem-favorite-symbolic",
            active=bool(self.item.get("favorite")),
            valign=Gtk.Align.CENTER,
        )
        self.favorite_toggle.add_css_class("circular")
        self._update_favorite_label()
        self.favorite_toggle.connect("toggled", self._on_favorite_toggled)
        row.append(self.favorite_toggle)

        playlist_button = Gtk.Button(
            icon_name="view-list-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text="Zu einer Playlist hinzufügen",
        )
        playlist_button.add_css_class("circular")
        playlist_button.connect("clicked", lambda *_: self._open_playlist_dialog())
        row.append(playlist_button)

        # Herunterladen gehört in dieselbe Reihe wie die übrigen Handgriffe.
        row.append(self.download_box)
        return row

    # -- Musik ------------------------------------------------------------

    def _play_music(self) -> None:
        """Ab diesem Titel abspielen; die Liste, aus der er geöffnet wurde,
        wird zur Warteschlange — genauso wie ein Klick in der Titelliste eines
        Albums."""
        queue = self.queue or [self.item]
        index = next((i for i, it in enumerate(queue) if int(it["id"]) == self.item_id), 0)
        self.ctx.music.play_queue(queue, index)

    def _enqueue_music(self) -> None:
        self.ctx.music.append([self.item])
        self._toast("An die Warteschlange angehängt.")

    # -- Zu Playlist hinzufügen ------------------------------------------

    def _open_playlist_dialog(self) -> None:
        """Zeigt die passenden Playlists mit Häkchen für die, in denen der
        Titel schon liegt. Video und Musik sind getrennt — die Art richtet sich
        nach der Bibliothek, damit ein Musiktitel nicht in einer Videoliste
        landet (so hält es der Server ebenfalls)."""

        def worker() -> None:
            kind = "music" if self.ctx.library_kind(self.item.get("libraryId")) == "music" else "video"
            try:
                playlists = self.ctx.client.playlists(kind)
                current = {int(p["id"]) for p in self.ctx.client.playlists_for_item(self.item_id)}
            except GoldfishAPIError as exc:
                GLib.idle_add(self._toast, f"Playlists nicht abrufbar: {exc}")
                return
            GLib.idle_add(self._show_playlist_dialog, playlists, current, kind)

        threading.Thread(target=worker, daemon=True).start()

    def _show_playlist_dialog(self, playlists: list[dict], current: set[int], kind: str) -> bool:
        dialog = Adw.MessageDialog(transient_for=self.ctx.dialog_parent(), heading="Zu Playlist hinzufügen")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        if playlists:
            listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            listbox.add_css_class("boxed-list")
            for pl in playlists:
                inside = int(pl["id"]) in current
                row = Adw.ActionRow(
                    title=pl.get("name") or "",
                    subtitle="schon enthalten" if inside else f"{pl.get('itemCount') or 0} Titel",
                    activatable=True,
                )
                row.add_suffix(Gtk.Image(icon_name="object-select-symbolic" if inside else "list-add-symbolic"))
                row.connect("activated", lambda _r, p=pl: self._add_to_playlist(p, dialog))
                listbox.append(row)
            scroll = Gtk.ScrolledWindow(child=listbox, propagate_natural_height=True, max_content_height=320)
            box.append(scroll)
        else:
            box.append(Gtk.Label(label="Noch keine passende Playlist vorhanden.", wrap=True))

        box.append(Gtk.Separator(margin_top=6))
        entry = Gtk.Entry(placeholder_text="Neue Playlist anlegen und hinzufügen")
        entry.connect("activate", lambda e: self._create_and_add(e.get_text().strip(), kind, dialog))
        box.append(entry)

        dialog.set_extra_child(box)
        dialog.add_response("close", "Fertig")
        dialog.present()
        return False

    def _add_to_playlist(self, playlist: dict, dialog: Adw.MessageDialog) -> None:
        def worker() -> None:
            try:
                added = self.ctx.client.add_to_playlist(int(playlist["id"]), self.item_id)
            except GoldfishAPIError as exc:
                GLib.idle_add(self._toast, str(exc))
                return
            name = playlist.get("name") or ""
            # Der Server unterscheidet "hinzugefügt" von "war schon drin" —
            # das gehört auch so gemeldet, sonst klickt man zweimal.
            GLib.idle_add(self._toast, f"Zu „{name}“ hinzugefügt" if added else f"Ist bereits in „{name}“")

        threading.Thread(target=worker, daemon=True).start()
        dialog.close()

    def _create_and_add(self, name: str, kind: str, dialog: Adw.MessageDialog) -> None:
        if not name:
            return

        def worker() -> None:
            try:
                created = self.ctx.client.create_playlist(name, kind)
                self.ctx.client.add_to_playlist(int(created["id"]), self.item_id)
            except (GoldfishAPIError, KeyError, TypeError) as exc:
                GLib.idle_add(self._toast, f"Anlegen fehlgeschlagen: {exc}")
                return
            GLib.idle_add(self._toast, f"„{name}“ angelegt und Titel hinzugefügt")

        threading.Thread(target=worker, daemon=True).start()
        dialog.close()

    # -- Spuren, Qualität, Versionen, Trailer ----------------------------

    def _load_extras(self) -> None:
        client = self.ctx.client
        try:
            info = client.playback_info(self.item_id)
        except GoldfishAPIError:
            info = {}
        profiles = [PlaybackProfile.from_json(p) for p in (info.get("profiles") or [])]

        variants: list[dict] = []
        if self.item.get("metadataId"):
            try:
                fetched = client.variants(self.item_id)
                # Erst ab zwei Dateien ist eine Auswahl sinnvoll.
                variants = fetched if len(fetched) > 1 else []
            except GoldfishAPIError:
                variants = []

        has_trailer = False
        metadata = self.item.get("metadata") or {}
        if metadata.get("tmdbType") == "movie" and metadata.get("id"):
            try:
                has_trailer = client.trailer(int(metadata["id"])) is not None
            except GoldfishAPIError:
                has_trailer = False

        GLib.idle_add(self._apply_extras, info, profiles, variants, has_trailer)

    def _apply_extras(
        self,
        info: dict,
        profiles: list[PlaybackProfile],
        variants: list[dict],
        has_trailer: bool,
    ) -> bool:
        self.variants = variants
        streams = info.get("streams") or []
        audio = [s for s in streams if s.get("type") == "audio"]
        subs = [s for s in streams if s.get("type") == "subtitle" and is_displayable_subtitle(s)]

        if variants:
            self.stream_box.append(self._variant_row(variants))
        if len(audio) > 1:
            self.stream_box.append(self._audio_row(audio))
        if subs:
            self.stream_box.append(self._subtitle_row(subs))
        if len(profiles) > 1:
            self.stream_box.append(self._profile_row(profiles))
        self.trailer_button.set_visible(has_trailer)
        return False

    def _variant_row(self, variants: list[dict]) -> Gtk.Widget:
        labels = []
        for v in variants:
            bits = [
                (v.get("container") or "").upper(),
                format_resolution(v.get("width") or 0, v.get("height") or 0),
                format_size(v.get("sizeBytes") or 0),
            ]
            name = (v.get("relPath") or v.get("title") or "").rsplit("/", 1)[-1]
            labels.append(f"{name} — {' · '.join(b for b in bits if b)}")
        drop = Gtk.DropDown.new_from_strings(labels)
        drop.set_selected(next((i for i, v in enumerate(variants) if int(v["id"]) == self.item_id), 0))
        drop.connect("notify::selected", self._on_variant_changed)
        return _picker_row("🎬", f"Version ({len(variants)})", drop)

    def _audio_row(self, audio: list[dict]) -> Gtk.Widget:
        self._audio_streams = audio
        drop = Gtk.DropDown.new_from_strings([audio_stream_label(s) for s in audio])
        drop.set_selected(next((i for i, s in enumerate(audio) if s.get("isDefault")), 0))
        drop.connect("notify::selected", self._on_audio_changed)
        row = _picker_row("🔊", "Tonspur", drop)
        row.set_tooltip_text(
            "Eine andere als die voreingestellte Tonspur lässt den Server das Video "
            "umwandeln — nur dabei kann er die Spur festlegen."
        )
        return row

    def _subtitle_row(self, subs: list[dict]) -> Gtk.Widget:
        self._subtitle_streams = subs
        drop = Gtk.DropDown.new_from_strings(["— Aus —"] + [subtitle_stream_label(s) for s in subs])
        drop.set_selected(0)
        drop.connect("notify::selected", self._on_subtitle_changed)
        row = _picker_row("💬", "Untertitel", drop)
        row.set_tooltip_text(
            "Bild-Untertitel, wie sie Blu-rays mitbringen, stehen nicht zur Wahl — "
            "sie lassen sich nicht als Text einblenden."
        )
        return row

    def _profile_row(self, profiles: list[PlaybackProfile]) -> Gtk.Widget:
        self._profiles = profiles
        drop = Gtk.DropDown.new_from_strings([p.label for p in profiles])
        drop.set_selected(next((i for i, p in enumerate(profiles) if p.id == "orig"), 0))
        drop.connect("notify::selected", self._on_profile_changed)
        return _picker_row("🎞", "Qualität", drop)

    def _on_variant_changed(self, drop: Gtk.DropDown, _param) -> None:
        variant = self.variants[drop.get_selected()]
        self.item = variant
        self.item_id = int(variant["id"])
        # Poster und Titel gehören zum Titel, nicht zur Datei — nur die
        # dateibezogenen Kenndaten und der Download-Bereich ändern sich.
        self._fill_meta_row()
        self._refresh_download_ui()

    def _on_audio_changed(self, drop: Gtk.DropDown, _param) -> None:
        stream = self._audio_streams[drop.get_selected()]
        self.chosen_audio = None if stream.get("isDefault") else int(stream["index"])

    def _on_subtitle_changed(self, drop: Gtk.DropDown, _param) -> None:
        idx = drop.get_selected()
        self.chosen_subtitle = None if idx == 0 else self._subtitle_streams[idx - 1]

    def _on_profile_changed(self, drop: Gtk.DropDown, _param) -> None:
        profile = self._profiles[drop.get_selected()]
        self.chosen_profile = "" if profile.id == "orig" else profile.id
        # Die Wahl gilt auch für den Download — das soll am Knopf stehen.
        self._refresh_download_ui()

    # -- Wiedergabe ------------------------------------------------------

    def _open_player(self, local_path: str | None = None, start_position: float = 0.0) -> None:
        # Nur eine Warteschlange mitgeben, wenn der Titel tatsächlich darin
        # vorkommt — nach einem Wechsel der Version ist das sonst der falsche
        # Platz, und es würde bei einem fremden Titel weiterlaufen.
        index = next((i for i, it in enumerate(self.queue) if int(it["id"]) == self.item_id), -1)
        open_player(
            self.ctx,
            self.item,
            local_path=local_path,
            profile=self.chosen_profile,
            audio_index=self.chosen_audio,
            subtitle=self.chosen_subtitle,
            start_position=start_position,
            queue=self.queue if index >= 0 else None,
            queue_index=max(0, index),
        )

    def _play_with_resume_check(self) -> None:
        """Fragt nach, wenn der Titel schon einmal angesehen wurde.

        Die Position steckt nicht im Item, sondern hinter einem eigenen
        Endpunkt — deshalb erst fragen, dann entscheiden. Läuft im Hintergrund,
        damit ein langsamer Server den Knopf nicht einfriert."""

        def worker() -> None:
            try:
                position = self.ctx.client.get_resume(self.item_id)
            except GoldfishAPIError:
                position = 0.0
            GLib.idle_add(self._after_resume_lookup, position)

        threading.Thread(target=worker, daemon=True).start()

    def _after_resume_lookup(self, position: float) -> bool:
        # **Ein noch offenes Wiedergabefenster ZUERST schließen.** Sonst legt
        # sich die Frage dahinter und wartet dort unsichtbar auf eine Antwort,
        # während das alte Video weiterläuft — für den Benutzer hängt die App
        # dann vollständig (genau so gemeldet). Gleich hier, nicht erst beim
        # Öffnen des neuen Fensters: die Frage kommt vorher.
        close_player(self.ctx)
        duration = self.item.get("durationSec") or 0
        # Unter einer Minute lohnt die Frage nicht, und kurz vor dem Ende
        # wäre "fortsetzen" sinnlos — dann von vorn, wie im Browser.
        if position < 60 or (duration and position > duration * 0.95):
            self._open_player()
            return False

        dialog = Adw.MessageDialog(
            transient_for=self.ctx.dialog_parent(),
            heading="Weiterschauen?",
            body=f"Du warst bei {format_duration(position)} von {format_duration(duration)}.",
        )
        dialog.add_response("start", "Von Anfang")
        dialog.add_response("resume", f"Bei {format_duration(position)} fortsetzen")
        dialog.set_default_response("resume")
        dialog.set_response_appearance("resume", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _d, response: self._open_player(start_position=position if response == "resume" else 0.0),
        )
        dialog.present()
        return False

    def _on_trailer_clicked(self, button: Gtk.Button) -> None:
        metadata = self.item.get("metadata") or {}
        if not metadata.get("id"):
            return
        # Symbolknopf: der Zustand steht in der Kurzhilfe, eine Beschriftung
        # würde das Symbol verdrängen.
        button.set_sensitive(False)
        button.set_tooltip_text("Trailer wird geholt …")

        def worker() -> None:
            # Der Server lädt den Trailer per yt-dlp und fügt Bild und Ton zu
            # einer Datei zusammen; das dauert einige Sekunden. Eine einzelne
            # Datei-URL ist hier nötig, weil das Videowidget kein
            # YouTube-Einbetten kann — im Browser läuft das über ein iframe.
            try:
                path = self.ctx.client.trailer_stream_path(int(metadata["id"]))
            except GoldfishAPIError as exc:
                GLib.idle_add(self._trailer_failed, button, str(exc))
                return
            if not path:
                GLib.idle_add(self._trailer_failed, button, "Kein Trailer verfügbar.")
                return
            GLib.idle_add(self._trailer_ready, button, path)

        threading.Thread(target=worker, daemon=True).start()

    def _trailer_ready(self, button: Gtk.Button, path: str) -> bool:
        button.set_sensitive(True)
        button.set_tooltip_text("Trailer ansehen")
        title = (self.item.get("metadata") or {}).get("title") or self.item.get("title") or ""
        open_player(
            self.ctx,
            self.item,
            direct_url=self.ctx.client.with_session_param(path),
            window_title=f"Trailer: {title}",
        )
        return False

    def _trailer_failed(self, button: Gtk.Button, message: str) -> bool:
        button.set_sensitive(True)
        button.set_tooltip_text("Trailer ansehen")
        self._toast(f"Trailer nicht abspielbar: {message}")
        return False

    def _open_person(self, member: dict) -> None:
        from .person_page import PersonPage

        tmdb_id = member.get("tmdbId")
        if not tmdb_id:
            return
        self.nav_view.push(PersonPage(self.ctx, self.nav_view, int(tmdb_id), member.get("name") or ""))

    # -- Gesehen und Favorit ---------------------------------------------

    def _update_watched_label(self) -> None:
        # Der Zustand steht jetzt in der Kurzhilfe, nicht in der Beschriftung —
        # der Knopf selbst zeigt ein Symbol und ist eingedrückt, wenn gesehen.
        self.watched_toggle.set_tooltip_text(
            "Als ungesehen markieren" if self.watched_toggle.get_active() else "Als gesehen markieren"
        )

    def _update_favorite_label(self) -> None:
        self.favorite_toggle.set_tooltip_text(
            "Favorit entfernen" if self.favorite_toggle.get_active() else "Als Favorit merken"
        )

    def _on_watched_toggled(self, button: Gtk.ToggleButton) -> None:
        watched = button.get_active()
        self._update_watched_label()
        self.item["watched"] = watched
        self._state_call(lambda: self.ctx.client.set_watched(self.item_id, watched), "Gesehen-Status")

    def _on_favorite_toggled(self, button: Gtk.ToggleButton) -> None:
        favorite = button.get_active()
        self._update_favorite_label()
        self.item["favorite"] = favorite
        self._state_call(lambda: self.ctx.client.set_favorite(self.item_id, favorite), "Favorit")

    def _state_call(self, call, label: str) -> None:
        def worker() -> None:
            try:
                call()
            except GoldfishAPIError as exc:
                GLib.idle_add(self._toast, f"{label} konnte nicht gespeichert werden: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _toast(self, message: str) -> bool:
        root = self.get_root()
        if hasattr(root, "show_toast"):
            root.show_toast(message)
        return False

    # -- Download --------------------------------------------------------

    def _refresh_download_ui(self) -> None:
        child = self.download_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.download_box.remove(child)
            child = nxt

        if self.ctx.downloads.is_downloaded(self.item_id):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            offline_button = Gtk.Button(
                icon_name="media-playback-start-symbolic",
                valign=Gtk.Align.CENTER,
                tooltip_text="Die heruntergeladene Datei abspielen (ohne Netz)",
            )
            offline_button.add_css_class("circular")
            offline_button.connect("clicked", self._on_play_offline)
            row.append(offline_button)
            delete_button = Gtk.Button(
                icon_name="user-trash-symbolic",
                tooltip_text="Heruntergeladene Datei löschen",
                valign=Gtk.Align.CENTER,
            )
            delete_button.add_css_class("circular")
            delete_button.add_css_class("destructive-action")
            delete_button.connect("clicked", self._on_delete_download)
            row.append(delete_button)
            self.download_box.append(row)
        elif self.ctx.downloads.is_downloading(self.item_id):
            spinner = Gtk.Spinner()
            label = Gtk.Label(label="Download läuft …")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.append(spinner)
            row.append(label)
            self.download_box.append(row)
            spinner.start()  # erst nach dem Einhängen, sonst fehlt die Frame-Clock
        else:
            # Reiner Symbolknopf; die gewählte Qualität steht in der Kurzhilfe.
            download_button = Gtk.Button(icon_name="folder-download-symbolic", valign=Gtk.Align.CENTER)
            download_button.add_css_class("circular")
            download_button.set_tooltip_text(
                f"Herunterladen in {self.chosen_profile} — nützlich, wenn der Platz knapp ist."
                if self.chosen_profile
                else "Herunterladen (Originaldatei). Für eine kleinere Fassung oben eine Qualität wählen."
            )
            download_button.connect("clicked", self._on_download_clicked)
            self.download_box.append(download_button)

    def _on_download_clicked(self, *_args) -> None:
        child = self.download_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.download_box.remove(child)
            child = nxt
        self.progress_bar = Gtk.ProgressBar(show_text=True)
        self.download_box.append(self.progress_bar)
        if self.chosen_profile:
            # Mit gewählter Qualität wandelt der Server vorher um; das dauert
            # einmalig und ist am Fortschritt noch nicht zu sehen.
            self.progress_bar.set_text("Wird vorbereitet …")
        self.ctx.downloads.start_download(
            self.item,
            on_progress=self._on_download_progress,
            on_done=self._on_download_done,
            on_error=self._on_download_error,
            profile=self.chosen_profile,
        )

    def _on_download_progress(self, item_id: int, fraction: float) -> bool:
        if item_id != self.item_id or not hasattr(self, "progress_bar"):
            return False
        self.progress_bar.set_fraction(fraction)
        self.progress_bar.set_text(f"{int(fraction * 100)} %")
        return False

    def _on_download_done(self, item_id: int, _local_path: str) -> bool:
        if item_id != self.item_id:
            return False
        self._refresh_download_ui()
        return False

    def _on_download_error(self, item_id: int, message: str) -> bool:
        if item_id != self.item_id:
            return False
        self._refresh_download_ui()
        self._toast(f"Download fehlgeschlagen: {message}")
        return False

    def _on_play_offline(self, *_args) -> None:
        path = self.ctx.downloads.local_path(self.item_id)
        if path:
            self._open_player(local_path=str(path))

    def _on_delete_download(self, *_args) -> None:
        self.ctx.downloads.delete_download(self.item_id)
        self._refresh_download_ui()


# -- kleine Bausteine ---------------------------------------------------


def _chip(text: str) -> Gtk.Widget:
    label = Gtk.Label(label=text)
    label.add_css_class("gf-badge")
    return label


def _picker_row(icon: str, label: str, widget: Gtk.Widget) -> Gtk.Box:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    name = Gtk.Label(label=f"{icon} {label}", xalign=0)
    name.set_size_request(130, -1)
    row.append(name)
    widget.set_hexpand(True)
    row.append(widget)
    return row


def _pretty_genres(raw: str) -> str:
    """Der Server liefert Genres als JSON-Feld in einem String. Ein
    Lesefehler ist hier kein Grund für eine Fehlermeldung — dann wird der
    Rohwert gezeigt."""
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return raw
    if isinstance(parsed, list):
        names = [p.get("name") if isinstance(p, dict) else str(p) for p in parsed]
        return " · ".join(n for n in names if n)
    return raw
