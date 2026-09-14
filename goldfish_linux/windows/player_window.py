"""Wiedergabefenster mit eigener Steuerleiste.

**Warum nicht Gtk.Video:** dessen Steuerleiste ist fest eingebaut und von außen
nicht erreichbar. Untertitel als Ebene über dem Bild, Vorschaubilder beim
Spulen und ein eigener Fortschrittsbalken sind damit nicht zu machen.
Stattdessen läuft hier `Gtk.MediaFile` direkt als Paintable in einem
`Gtk.Picture` — das liefert Position, Dauer, Springen und Lautstärke, und die
Bedienung ist vollständig unsere. Vorher am echten HLS-Stream des Servers
geprüft.

Die Dekodierung übernimmt weiterhin GStreamer (gstreamer1.0-plugins-good und
-bad für HTTP und HLS, -libav für H.264 und AAC).

Auth ohne Cookie: an die Stream-URL wird `?session=<token>` gehängt (derselbe
Weg, den der Server für Cast-Empfänger bereitstellt) — GStreamer hat keinen
Cookie-Speicher.

**Springen bei serverseitiger Umwandlung:** ein HLS-Stream wächst, während er
entsteht — der Abspieler kennt deshalb weder die Gesamtdauer (`get_duration()`
bleibt 0) noch kann er über das hinausspringen, was schon erzeugt ist. Beides
wird hier umgangen wie im Browser: die Dauer kommt aus `item["durationSec"]`
(der Server hat sie per ffprobe), und ein Sprung startet eine NEUE Umwandlung ab
der Zielsekunde (`start=`). `_virtual_offset` hält fest, bei welcher Sekunde die
laufende Umwandlung beginnt; alle Anzeigen rechnen sie auf die Position des
Abspielers auf. Bei direkter Wiedergabe ist der Offset 0 und es wird normal
gesprungen.

`profile` ist eine Qualitätsstufe, `audio_index` eine Tonspur. Eine gewählte
Tonspur **erzwingt die serverseitige Umwandlung**, weil nur dabei der Server
entscheidet, welche Spur in den Stream kommt; bei direkter Wiedergabe enthält
die Datei alle Spuren und der Abspieler nimmt die erste. `direct_url` spielt
eine fertige Datei ab (Trailer) und meldet nichts ins Protokoll.
"""

from __future__ import annotations

import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from ..api import GoldfishAPIError, GoldfishClient, TrickplayCue  # noqa: E402
from ..formatting import format_duration, format_resolution  # noqa: E402
from ..subtitles import SubtitleTrack, parse_vtt  # noqa: E402
from ..widgets.stats_popover import (  # noqa: E402
    PlaybackStatsPopover,
    format_ahead,
    format_bitrate,
)

# Wie oft Position, Untertitel und Fortschritt aktualisiert werden. Viermal pro
# Sekunde ist genug für einen flüssigen Balken und lässt Untertitel pünktlich
# erscheinen, ohne Last zu erzeugen.
_TICK_MS = 250

# Sprungweiten der Knöpfe, wie in der Mac-App: zurück kürzer als vorwärts —
# man springt zurück, um etwas Verpasstes nochmal zu hören, und vorwärts, um
# über eine längere Stelle hinwegzukommen.
_SKIP_BACK = 15
_SKIP_FORWARD = 30

# Abstand, in dem die Position an den Server gemeldet wird, damit
# Weiterschauen auch nach einem Absturz noch stimmt.
_RESUME_EVERY_S = 10

# Ab diesem Anteil gilt ein Titel als gesehen — dieselbe Schwelle wie im
# Browser.
_WATCHED_AT = 0.9

_CSS = b"""
.gf-subtitle {
  background-color: alpha(#000000, 0.62);
  color: #ffffff;
  font-size: 1.35rem;
  padding: 4px 12px;
  border-radius: 6px;
}
.gf-player-bar {
  background-color: alpha(#000000, 0.75);
  padding: 6px 12px;
}
.gf-player-time {
  font-family: monospace;
  color: #ffffff;
  font-size: 0.9rem;
}
.gf-player-bar button { color: #ffffff; }
"""

_css_loaded = False


def _ensure_css() -> None:
    global _css_loaded
    if _css_loaded:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_loaded = True


class PlayerWindow(Adw.Window):
    def __init__(
        self,
        app,
        client: GoldfishClient,
        item: dict,
        local_path: str | None = None,
        profile: str = "",
        audio_index: int | None = None,
        subtitle: dict | None = None,
        start_position: float = 0.0,
        queue: list[dict] | None = None,
        queue_index: int = 0,
        direct_url: str | None = None,
        window_title: str | None = None,
        random_fetch=None,
    ):
        _ensure_css()
        title = self._title_for(item, window_title)
        super().__init__(application=app, title=title)
        self.client = client
        self.app = app
        self.item = item
        self.item_id = int(item["id"])
        self.local_path = local_path
        self.profile = profile
        self.audio_index = audio_index
        self.subtitle_stream = subtitle
        self.start_position = start_position
        self.queue = queue or []
        self.queue_index = queue_index
        self.direct_url = direct_url
        # Zufallsmodus: `random_fetch` holt (im Hintergrund aufgerufen) das
        # nächste Zufallsvideo. Die schon gesehenen bleiben als Verlauf
        # stehen, damit ⏮ zurückblättern kann — genau wie im Browser
        # (`state.shuffleHistory`/`shuffleIdx`) und in der Mac-App.
        self.random_fetch = random_fetch
        self.random_history: list[dict] = [item] if random_fetch else []
        self.random_index = 0

        self._stop_reported = False
        # Gegenstueck zu _stop_reported: der Server bekommt pro Titel GENAU
        # EINEN "play"-Eintrag. Ohne das meldet jeder Sprung einen neuen
        # Wiedergabe-Start — `_play_uri` laeuft bei laufender Umwandlung ja
        # auch beim Spulen, weil dort eine neue Umwandlung ab der Zielsekunde
        # beginnt. Im Protokoll standen dadurch fuenf "play" fuer dasselbe
        # Video in derselben Sekunde (User-Report 2026-09-14).
        self._start_reported = False
        self._seeking = False
        self._last_resume_sent = 0.0
        self._watched_marked = False
        self._track: SubtitleTrack | None = None
        self._trickplay: list[TrickplayCue] = []
        self._sprite: GdkPixbuf.Pixbuf | None = None
        self._hide_bar_source: int | None = None
        self.media: Gtk.MediaFile | None = None
        self._media_handlers: list[int] = []
        # Diagnose-Anzeige: letzter bekannter Stand der serverseitigen
        # Umwandlung und wann er zuletzt geholt wurde. Der Abruf laeuft nur,
        # solange das Aufklappfenster offen ist — er haelt die Sitzung am
        # Leben (Touch), was beim Zuschauen erwuenscht, aber unnoetig ist,
        # wenn niemand hinsieht.
        self._server_position: float | None = None
        self._server_done = False
        self._last_progress_poll = 0.0

        # Umwandlungsmodus: Basis-URL ohne `start`, aktueller Zeitversatz und
        # ein pro Fenster stabiler Token. Der Token MUSS über die periodischen
        # Playlist-Abrufe hinweg gleich bleiben — der Server bricht die
        # laufende Umwandlung sonst bei jedem Abruf ab und die Wiedergabe
        # stockt (siehe ConsumeFresh im Server).
        self._is_transcode = False
        self._stream_base = ""
        self._virtual_offset = 0.0
        self._fresh_token = str(int(time.time() * 1000))
        # Dauer laut Server — bei HLS die einzige verlässliche Quelle.
        self._duration = float(item.get("durationSec") or 0)

        self.set_default_size(1100, 680)
        self._build_ui(title)
        self._wire_input()

        self.connect("close-request", self._on_close_request)
        GLib.timeout_add(_TICK_MS, self._tick)

        self._start_playback()

    # -- Aufbau ----------------------------------------------------------

    @staticmethod
    def _title_for(item: dict, window_title: str | None) -> str:
        if window_title:
            return window_title
        metadata = item.get("metadata") or {}
        return metadata.get("title") or item.get("title") or "Wiedergabe"

    def _build_ui(self, title: str) -> None:
        self.header = Adw.HeaderBar()
        self.header.set_title_widget(Adw.WindowTitle(title=title))

        # Diagnose-Anzeige oben rechts (User-Wunsch 2026-09-14, Vorbild Plex).
        # Bewusst ein Aufklappfenster am Knopf und kein Dialog: ein Dialog legt
        # sich hinter das Wiedergabefenster und laesst die App eingefroren
        # wirken (in 0.1.21 genau so passiert).
        self.stats_popover = PlaybackStatsPopover(on_measure=self._measure_throughput)
        self.stats_button = Gtk.MenuButton(
            icon_name="dialog-information-symbolic",
            popover=self.stats_popover,
            tooltip_text="Wiedergabe-Informationen",
        )
        self.header.pack_end(self.stats_button)

        self.picture = Gtk.Picture(hexpand=True, vexpand=True)
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)

        self.subtitle_label = Gtk.Label(
            valign=Gtk.Align.END,
            halign=Gtk.Align.CENTER,
            margin_bottom=28,
            justify=Gtk.Justification.CENTER,
            wrap=True,
            visible=False,
        )
        self.subtitle_label.add_css_class("gf-subtitle")

        self.busy = Gtk.Spinner(
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            width_request=48,
            height_request=48,
        )

        self.overlay = Gtk.Overlay(child=self.picture)
        self.overlay.add_overlay(self.subtitle_label)
        self.overlay.add_overlay(self.busy)

        self.bar = self._build_bar()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.overlay)
        box.append(self.bar)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(self.header)
        toolbar_view.set_content(box)
        self.set_content(toolbar_view)
        # Erst nach dem Einhängen drehen lassen, sonst fehlt die Frame-Clock.
        self.busy.start()

    def _build_bar(self) -> Gtk.Widget:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.add_css_class("gf-player-bar")

        # Vor/Zurück: im Zufallsmodus das nächste bzw. vorherige Zufallsvideo,
        # in einer Warteschlange der nächste bzw. vorige Titel. Ohne beides
        # gibt es nichts zu blättern, dann bleiben die Knöpfe weg.
        self.prev_button = Gtk.Button(icon_name="media-skip-backward-symbolic", has_frame=False)
        self.prev_button.connect("clicked", lambda *_: self._go_previous())
        bar.append(self.prev_button)

        self.back_button = Gtk.Button(icon_name="media-seek-backward-symbolic", has_frame=False)
        self.back_button.set_tooltip_text("15 Sekunden zurück")
        self.back_button.connect("clicked", lambda *_: self.skip(-_SKIP_BACK))
        bar.append(self.back_button)

        self.play_button = Gtk.Button(icon_name="media-playback-pause-symbolic", has_frame=False)
        self.play_button.connect("clicked", lambda *_: self.toggle_play())
        bar.append(self.play_button)

        self.forward_button = Gtk.Button(icon_name="media-seek-forward-symbolic", has_frame=False)
        self.forward_button.set_tooltip_text("30 Sekunden vor")
        self.forward_button.connect("clicked", lambda *_: self.skip(_SKIP_FORWARD))
        bar.append(self.forward_button)

        self.next_button = Gtk.Button(icon_name="media-skip-forward-symbolic", has_frame=False)
        self.next_button.connect("clicked", lambda *_: self._go_next())
        bar.append(self.next_button)
        self._update_step_buttons()

        self.position_label = Gtk.Label(label="0:00")
        self.position_label.add_css_class("gf-player-time")
        bar.append(self.position_label)

        self.scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            adjustment=Gtk.Adjustment(lower=0, upper=1, value=0),
            draw_value=False,
            hexpand=True,
        )
        # Größere Trefferfläche fürs Hovern (User-Report: "Cursor musste ganz
        # genau an der richtigen Stelle sein" für die Trickplay-Vorschau) — der
        # sichtbare Balken (die Rille/`trough`) bleibt bei Adwaita mittig und
        # gleich dünn, unabhängig von der Widget-Höhe; nur die unsichtbare
        # Hover-/Klickfläche drumherum wird größer. Reine Höhenänderung, keine
        # Breitenänderung, verändert also nichts am Aussehen der Zeitleiste.
        #
        # Die Trefferfläche liegt SYMMETRISCH um die Rille (User-Report
        # 2026-09-14: "Hoverbereich ist nur über der Zeitleiste, nicht
        # darunter"). Eine waagerechte Gtk.Box richtet ihre Kinder an der
        # BASISLINIE aus — die Zeitleiste klebte dadurch am oberen Rand der
        # Steuerleiste: nachgemessen 46 px Leiste, 34 px Zeitleiste bei
        # y=0..34, also 0 px Trefferfläche oben und 12 px tote Zone unten.
        #
        # **NIEMALS `set_vexpand(True)` dafür benutzen.** Dehnung propagiert in
        # GTK4 nach oben: die Steuerleiste erbt sie vom Kind und nimmt dann in
        # der senkrechten Box den ganzen Platz des VIDEOS ein. Genau das ist
        # am 2026-09-14 passiert — die Leiste wuchs auf ein Vielfaches, das
        # Bild schrumpfte auf einen Streifen. `valign=CENTER` zentriert die
        # angeforderte Höhe stattdessen um die Rille, ohne das Layout
        # anzufassen.
        self.scale.set_size_request(-1, 44)
        self.scale.set_valign(Gtk.Align.CENTER)
        self.scale.connect("change-value", self._on_scale_change)
        # Während des Ziehens nicht aus dem Medium lesen, sonst springt der
        # Griff zurück, solange die Wiedergabe der neuen Position nachläuft.
        press = Gtk.GestureClick()
        press.connect("pressed", lambda *_: setattr(self, "_seeking", True))
        press.connect("released", lambda *_: setattr(self, "_seeking", False))
        # "cancel" MUSS mit: bricht GTK die Geste ab, kommt "released" NIE.
        # Genau das passiert beim Spulen in einer laufenden Umwandlung — der
        # Sprung tauscht mitten im Ziehen das Medium aus. `_seeking` blieb
        # dann für immer wahr, und weil `_tick` die Zeitleiste nur bei
        # `not _seeking` nachführt, fror der Balken ein: die Beschriftungen
        # liefen weiter, der Knopf blieb stehen (User-Report 2026-09-14, zwei
        # Screenshots mit 0:25 von 40:50 bei 60 % und 0:07 von 47:57 bei 27 %).
        press.connect("cancel", lambda *_: setattr(self, "_seeking", False))
        self.scale.add_controller(press)

        # Der Hover-Melder sitzt NICHT auf der Zeitleiste, sondern auf der
        # ganzen Steuerleiste (siehe _on_bar_motion). Auf der Zeitleiste wäre
        # die Trefferfläche zwangsläufig deren eigene Höhe — der Nutzer musste
        # den Zeiger "ganz genau" auf die Rille setzen, und unterhalb kam gar
        # nichts (User-Report 2026-09-14, zweimal). Über der Leiste hat man die
        # volle Höhe zur Verfügung, ober- UND unterhalb der Rille.
        bar.append(self.scale)

        self.duration_label = Gtk.Label(label="0:00")
        self.duration_label.add_css_class("gf-player-time")
        bar.append(self.duration_label)

        # Welche Auflösung gerade wirklich läuft. Die Quelle ist das Medium
        # selbst (`get_intrinsic_width/height` der Paintable-Schnittstelle) —
        # bei einer serverseitigen Umwandlung steht dort die heruntergerechnete
        # Größe, nicht die der Datei. Genau das will man wissen.
        self.res_label = Gtk.Label(visible=False)
        self.res_label.add_css_class("gf-player-time")
        bar.append(self.res_label)

        self.volume = Gtk.VolumeButton()
        self.volume.set_value(1.0)
        self.volume.connect("value-changed", self._on_volume)
        bar.append(self.volume)

        self.fullscreen_button = Gtk.Button(icon_name="view-fullscreen-symbolic", has_frame=False)
        self.fullscreen_button.connect("clicked", lambda *_: self.toggle_fullscreen())
        bar.append(self.fullscreen_button)

        # Vorschaubild beim Zeigen auf den Balken. 160×90 = native Größe der
        # Server-Sprite-Kacheln (siehe Server-CLAUDE.md Trickplay-Filter-Chain)
        # — NICHT die Bildgröße vergrößern (User-Korrektur: die Größe war schon
        # richtig, gemeint war der Hover-AUSLÖSEBEREICH, siehe Motion-Controller
        # weiter unten).
        self.preview_picture = Gtk.Picture(width_request=160, height_request=90)
        self.preview_label = Gtk.Label()
        self.preview_label.add_css_class("gf-player-time")
        preview_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            margin_top=4,
            margin_bottom=4,
            margin_start=4,
            margin_end=4,
        )
        preview_box.append(self.preview_picture)
        preview_box.append(self.preview_label)
        self.preview = Gtk.Popover(child=preview_box, autohide=False, has_arrow=True)
        self.preview.set_parent(self.scale)
        # Hover über der GANZEN Leiste meldet Vorschaubilder (siehe
        # _on_bar_motion). Muss hier stehen, nicht im Konstruktor: `bar` ist
        # erst jetzt fertig aufgebaut.
        bar_motion = Gtk.EventControllerMotion()
        bar_motion.connect("motion", self._on_bar_motion)
        bar_motion.connect("leave", lambda *_: self._hide_preview())
        bar.add_controller(bar_motion)
        return bar

    def _wire_input(self) -> None:
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        # Im Vollbild verschwindet die Leiste, wenn die Maus ruht.
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda *_: self._show_bar_briefly())
        self.add_controller(motion)

    # -- Wiedergabe starten ----------------------------------------------

    def _start_playback(self) -> None:
        if self.local_path:
            self._play_uri(Gio.File.new_for_path(self.local_path).get_uri())
            return
        if self.direct_url:
            self._play_uri(self.direct_url)
            return
        threading.Thread(target=self._resolve_stream, daemon=True).start()

    def _resolve_stream(self) -> None:
        mode = "transcode" if self.audio_index is not None else "auto"
        try:
            info = self.client.playback_info(self.item_id, mode=mode, profile=self.profile or "orig")
        except GoldfishAPIError as exc:
            GLib.idle_add(self._show_load_error, str(exc))
            return
        url = info.get("url")
        if not url:
            GLib.idle_add(self._show_load_error, "Der Server lieferte keine Wiedergabe-URL.")
            return
        if self.audio_index is not None:
            # An die Playlist, nicht an die Segmente: der Server hängt seine
            # Parameter von dort selbst an jede Segment-URL weiter.
            url += ("&" if "?" in url else "?") + f"audio={self.audio_index}"
        self._is_transcode = "/api/transcode/" in url
        self._stream_base = url
        if not self._duration:
            self._duration = float((info.get("item") or {}).get("durationSec") or 0)
        start = self.start_position if self._is_transcode else 0.0
        self.start_position = 0.0 if self._is_transcode else self.start_position
        GLib.idle_add(self._play_uri, self._stream_uri(start), start)
        self._load_side_data()

    def _load_side_data(self) -> None:
        """Untertitel und Vorschaubilder nachladen — beides ist Beigabe und
        darf die Wiedergabe nicht aufhalten, läuft deshalb nach dem Start."""
        if self.subtitle_stream is not None:
            text = self._fetch_subtitle(self.subtitle_stream)
            if text:
                GLib.idle_add(self._set_track, SubtitleTrack(parse_vtt(text)))
        if self.item.get("trickplayStatus") == "done":
            cues = self.client.trickplay_cues(self.item_id)
            sprite = self.client.trickplay_sprite_bytes(self.item_id) if cues else None
            if cues and sprite:
                GLib.idle_add(self._set_trickplay, cues, sprite)

    def _fetch_subtitle(self, stream: dict) -> str | None:
        """Holt die gewählte Spur. Erzeugte Spuren liegen unter eigenen
        Adressen — die von Whisper und die von OCR jeweils nach Sprache, eine
        eingebettete dagegen nach ihrem Stream-Index."""
        codec = (stream.get("codec") or "").lower()
        title = stream.get("title") or ""
        language = stream.get("language") or "de"
        try:
            if codec == "webvtt-ocr" or title.startswith("📝"):
                return self.client.ocr_subtitle_vtt(self.item_id, language)
            if title.startswith("🎤"):
                return self.client.generated_subtitle_vtt(self.item_id, language)
            return self.client.subtitle_vtt(self.item_id, int(stream["index"]))
        except (GoldfishAPIError, KeyError, ValueError, TypeError):
            return None

    def _stream_uri(self, start: float) -> str:
        """Baut die Playlist-URL für einen Start bei `start` Sekunden.

        `fresh=1` zwingt den Server, eine bestehende Umwandlung zu beenden und
        bei der gewünschten Sekunde neu anzufangen — ohne das bekommt man eine
        Playlist, in der schon Material liegt, und landet nicht an der
        Zielstelle. `_t` hält den Zwang an dieses Fenster gebunden, damit die
        periodischen Playlist-Abrufe die Umwandlung nicht dauernd neu starten."""
        url = self._stream_base
        if self._is_transcode:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}start={start:.3f}&fresh=1&_t={self._fresh_token}"
        return self.client.with_session_param(url)

    def _play_uri(self, uri: str, virtual_offset: float = 0.0) -> bool:
        # Das vorherige Medium sauber abklemmen. Ohne das melden seine Signale
        # weiter — und weil ein Sprung die alte Umwandlung serverseitig beendet
        # (`fresh=1`), meldet das abgelöste Medium zuverlässig einen
        # Datenstromfehler. Der legte sich als "Wiedergabe fehlgeschlagen" über
        # den längst laufenden neuen Stream: im ersten Sprungtest genau so
        # passiert, während die Werte im Hintergrund korrekt weiterliefen.
        self._release_media()

        media = Gtk.MediaFile.new_for_file(Gio.File.new_for_uri(uri))
        # Ein neues Medium beendet jedes laufende Spulen — unabhängig davon,
        # ob die Geste sauber endete. Zweite Absicherung gegen den
        # eingefrorenen Fortschrittsbalken (siehe "cancel" oben).
        self._seeking = False
        self._virtual_offset = virtual_offset
        self.media = media
        self.picture.set_paintable(media)
        self._media_handlers = [
            media.connect("notify::error", self._on_media_error),
            media.connect("notify::ended", self._on_media_ended),
            media.connect("notify::prepared", self._on_media_prepared),
        ]
        media.set_volume(self.volume.get_value())
        media.play()
        if not self.direct_url and not self.local_path and not self._start_reported:
            self._start_reported = True
            self._background(lambda: self.client.playback_start(self.item_id))
        return False

    def _release_media(self) -> None:
        """Altes Medium **abbauen**, nicht bloß anhalten.

        `pause()` allein hält die Wiedergabe an, lässt die GStreamer-Kette
        aber vollständig stehen: jedes abgelöste Medium behielt seine Decoder
        samt Fäden und Puffern. Nachgemessen bei fünf Videos hintereinander:
        mit `pause()` wuchsen die Fäden von 33 auf 89 (vierzehn je Video) und
        der Speicher von 221 auf 327 MB — beim Benutzer nach acht Wechseln
        135 Fäden und 1,2 GB. Mit `clear()` bleibt beides stehen (47 Fäden,
        278 MB).

        Das Bild muss dabei zuerst abgehängt werden: solange das Gtk.Picture
        das Medium als Paintable hält, wird es nicht abgebaut."""
        media = self.media
        if media is None:
            return
        for handler in self._media_handlers:
            try:
                media.disconnect(handler)
            except TypeError:
                pass  # bereits getrennt
        self._media_handlers = []
        media.set_playing(False)
        if self.picture.get_paintable() is media:
            self.picture.set_paintable(None)
        media.clear()
        self.media = None

    def _on_media_prepared(self, media: Gtk.MediaFile, _p) -> None:
        # Signale abgelöster Medien ignorieren (zweite Absicherung neben dem
        # Trennen in _release_media).
        if media is not self.media or not media.is_prepared():
            return
        self.busy.stop()
        self.busy.set_visible(False)
        # Bei Umwandlung ist die Startstelle schon in der URL enthalten; bei
        # direkter Wiedergabe wird sie hier gesetzt, sobald das Medium bereit
        # ist (vorher lehnt es den Sprung ab).
        if self.start_position > 1 and media.is_seekable():
            media.seek(int(self.start_position * 1_000_000))
            self.start_position = 0.0

    # -- Laufende Aktualisierung -----------------------------------------

    def _update_resolution(self) -> None:
        media = self.media
        if media is None:
            return
        width, height = media.get_intrinsic_width(), media.get_intrinsic_height()
        if (width, height) == getattr(self, "_last_res", None):
            return
        self._last_res = (width, height)
        label = format_resolution(width, height) if width and height else ""
        self.res_label.set_text(label)
        self.res_label.set_visible(bool(label))
        if label:
            mode = "Umwandlung" if self._is_transcode else "Direkte Wiedergabe"
            if self._is_transcode and self.profile and self.profile != "orig":
                mode = f"Umwandlung ({self.profile})"
            self.res_label.set_tooltip_text(f"{mode} · {width}×{height}")

    def _tick(self) -> bool:
        # Auch ohne Medium: die Anzeige soll verschwinden, wenn nichts läuft.
        self._update_resolution()
        media = self.media
        if media is None:
            return True
        # Bei laufender Umwandlung zählt der Abspieler ab dem Anfang DIESER
        # Umwandlung — der Zeitversatz macht daraus die Stelle im Film.
        position = self._virtual_offset + media.get_timestamp() / 1_000_000
        duration = self._duration or media.get_duration() / 1_000_000

        if duration > 0:
            self.duration_label.set_label(format_duration(duration))
            if not self._seeking:
                self.scale.get_adjustment().set_upper(duration)
                self.scale.get_adjustment().set_value(min(position, duration))
        self.position_label.set_label(format_duration(position))
        self.play_button.set_icon_name(
            "media-playback-pause-symbolic" if media.get_playing() else "media-playback-start-symbolic"
        )

        if self._track is not None:
            text = self._track.text_at(position)
            self.subtitle_label.set_label(text)
            self.subtitle_label.set_visible(bool(text))

        self._maybe_report_position(position, duration)
        self._update_stats(position, duration)
        return True

    def _maybe_report_position(self, position: float, duration: float) -> None:
        if self.local_path or self.direct_url or position <= 0:
            return
        if position - self._last_resume_sent >= _RESUME_EVERY_S:
            self._last_resume_sent = position
            item_id = self.item_id
            self._background(lambda: self.client.set_resume(item_id, position))
        if not self._watched_marked and duration > 0 and position / duration >= _WATCHED_AT:
            self._watched_marked = True
            item_id = self.item_id
            self._background(lambda: self.client.set_watched(item_id, True))

    # -- Bedienung -------------------------------------------------------

    def toggle_play(self) -> None:
        if self.media is None:
            return
        if self.media.get_playing():
            self.media.pause()
        else:
            self.media.play()

    def skip(self, seconds: float) -> None:
        if self.media is None:
            return
        current = self._virtual_offset + self.media.get_timestamp() / 1_000_000
        self.seek_to(current + seconds)

    def seek_to(self, absolute_seconds: float) -> None:
        """Springt an eine Stelle im Film.

        Bei direkter Wiedergabe genügt ein Sprung im Abspieler. Bei laufender
        Umwandlung geht das NICHT: die Playlist reicht nur so weit, wie der
        Server schon umgewandelt hat, ein Sprung dahinter würde stumpf am Ende
        des Bekannten hängen bleiben. Deshalb wird eine neue Umwandlung ab der
        Zielsekunde gestartet — derselbe Weg wie im Browser."""
        if self.media is None:
            return
        duration = self._duration or self.media.get_duration() / 1_000_000
        target = max(0.0, absolute_seconds)
        if duration > 0:
            target = min(target, max(0.0, duration - 1))

        if not self._is_transcode:
            if self.media.is_seekable():
                self.media.seek(int(target * 1_000_000))
            return

        was_playing = self.media.get_playing()
        self.busy.set_visible(True)
        self.busy.start()
        self._play_uri(self._stream_uri(target), target)
        if not was_playing and self.media is not None:
            self.media.pause()

    def toggle_fullscreen(self) -> None:
        if self.is_fullscreen():
            self.unfullscreen()
            self.header.set_visible(True)
            self.bar.set_visible(True)
            self.fullscreen_button.set_icon_name("view-fullscreen-symbolic")
        else:
            self.fullscreen()
            self.header.set_visible(False)
            self.fullscreen_button.set_icon_name("view-restore-symbolic")
            self._show_bar_briefly()

    def _show_bar_briefly(self) -> None:
        """Im Vollbild die Leiste zeigen und nach drei Sekunden Ruhe wieder
        ausblenden. Im Fenster bleibt sie immer sichtbar."""
        self.bar.set_visible(True)
        if self._hide_bar_source is not None:
            GLib.source_remove(self._hide_bar_source)
            self._hide_bar_source = None
        if not self.is_fullscreen():
            return

        def hide() -> bool:
            self._hide_bar_source = None
            if self.is_fullscreen():
                self.bar.set_visible(False)
            return False

        self._hide_bar_source = GLib.timeout_add_seconds(3, hide)

    def _on_key(self, _controller, keyval, _code, _state) -> bool:
        if keyval == Gdk.KEY_space:
            self.toggle_play()
        elif keyval == Gdk.KEY_Left:
            self.skip(-10)
        elif keyval == Gdk.KEY_Right:
            self.skip(10)
        elif keyval in (Gdk.KEY_f, Gdk.KEY_F, Gdk.KEY_F11):
            self.toggle_fullscreen()
        elif keyval == Gdk.KEY_Escape and self.is_fullscreen():
            self.toggle_fullscreen()
        else:
            return False
        return True

    def _on_volume(self, _button, value: float) -> None:
        if self.media is not None:
            self.media.set_volume(value)

    def _on_scale_change(self, _scale, _scroll, value: float) -> bool:
        self.seek_to(value)
        return False

    # -- Vorschaubilder --------------------------------------------------

    def _set_trickplay(self, cues: list[TrickplayCue], sprite_bytes: bytes) -> bool:
        loader = GdkPixbuf.PixbufLoader()
        try:
            loader.write(sprite_bytes)
            loader.close()
        except GLib.Error:
            return False
        self._sprite = loader.get_pixbuf()
        self._trickplay = cues
        return False

    def _on_bar_motion(self, _controller, x: float, _y: float) -> None:
        """Vorschaubild anzeigen, solange der Zeiger waagerecht über der
        Zeitleiste steht — unabhängig davon, wie hoch er in der Steuerleiste
        liegt.

        Die x-Koordinate kommt relativ zur Steuerleiste und wird hier in die
        Koordinaten der Zeitleiste umgerechnet; senkrecht wird bewusst NICHT
        geprüft, das ist der ganze Zweck. Außerhalb des waagerechten Bereichs
        (über den Knöpfen, der Zeitanzeige, dem Lautstärkeregler) verschwindet
        das Vorschaubild wieder."""
        ok, rect = self.scale.compute_bounds(self.bar)
        if not ok:
            return
        left, width = rect.origin.x, rect.size.width
        if width <= 0 or x < left or x > left + width:
            self._hide_preview()
            return
        self._on_scale_motion(None, x - left, 0.0)

    def _on_scale_motion(self, _controller, x: float, _y: float) -> None:
        if not self._trickplay or self._sprite is None:
            return
        width = self.scale.get_allocated_width()
        duration = self._duration or self.scale.get_adjustment().get_upper()
        if width <= 0 or duration <= 0:
            return
        seconds = max(0.0, min(duration, duration * (x / width)))
        cue = next((c for c in self._trickplay if c.start <= seconds <= c.end), None)
        if cue is None:
            cue = min(self._trickplay, key=lambda c: abs(c.start - seconds))
        try:
            crop = GdkPixbuf.Pixbuf.new_subpixbuf(self._sprite, cue.x, cue.y, cue.width, cue.height)
        except (GLib.Error, TypeError, ValueError):
            return
        self.preview_picture.set_paintable(Gdk.Texture.new_for_pixbuf(crop))
        self.preview_label.set_label(format_duration(seconds))
        # Das Fähnchen dem Mauszeiger folgen lassen.
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), 0, 1, 1
        self.preview.set_pointing_to(rect)
        self.preview.popup()

    def _hide_preview(self) -> None:
        self.preview.popdown()

    # -- Ende, Warteschlange, Fehler -------------------------------------

    def _on_media_ended(self, media: Gtk.MediaFile, _p) -> None:
        if media is not self.media or not media.get_ended():
            return
        self._report_stop("ended")
        if not self._play_next():
            self.close()

    def _play_next(self) -> bool:
        """Weiter zum nächsten: im Zufallsmodus ein neues Zufallsvideo, sonst
        der nächste Titel der Warteschlange. Gibt False zurück, wenn es nichts
        mehr gibt — dann schließt sich das Fenster am Ende des Films."""
        if self.random_fetch is not None:
            self._go_next()
            return True
        if self.queue_index + 1 >= len(self.queue):
            return False
        self.queue_index += 1
        self._switch_to(self.queue[self.queue_index])
        return True

    # -- Blättern ---------------------------------------------------------

    def _update_step_buttons(self) -> None:
        """Vor/Zurück nur zeigen, wenn es etwas zu blättern gibt."""
        random_mode = self.random_fetch is not None
        has_queue = len(self.queue) > 1
        self.prev_button.set_visible(random_mode or has_queue)
        self.next_button.set_visible(random_mode or has_queue)
        if random_mode:
            self.prev_button.set_tooltip_text("Vorheriges Zufallsvideo")
            self.next_button.set_tooltip_text("Nächstes Zufallsvideo")
            self.prev_button.set_sensitive(self.random_index > 0)
        else:
            self.prev_button.set_tooltip_text("Voriger Titel")
            self.next_button.set_tooltip_text("Nächster Titel")
            self.prev_button.set_sensitive(self.queue_index > 0)
            self.next_button.set_sensitive(self.queue_index + 1 < len(self.queue))

    def _go_previous(self) -> None:
        if self.random_fetch is not None:
            if self.random_index > 0:
                self.random_index -= 1
                self._switch_to(self.random_history[self.random_index])
            return
        if self.queue_index > 0:
            self.queue_index -= 1
            self._switch_to(self.queue[self.queue_index])

    def _go_next(self) -> None:
        if self.random_fetch is None:
            if self.queue_index + 1 < len(self.queue):
                self.queue_index += 1
                self._switch_to(self.queue[self.queue_index])
            return
        # Schon einmal zurückgeblättert? Dann erst durch den Verlauf nach
        # vorn, bevor ein neues Video gezogen wird.
        if self.random_index + 1 < len(self.random_history):
            self.random_index += 1
            self._switch_to(self.random_history[self.random_index])
            return
        self.busy.set_visible(True)
        self.busy.start()

        def worker() -> None:
            try:
                item = self.random_fetch()
            except Exception as exc:  # noqa: BLE001 — im Fenster melden, nicht sterben
                GLib.idle_add(self._show_load_error, f"Kein Zufallstreffer: {exc}")
                return
            if not item:
                GLib.idle_add(self._show_load_error, "Kein weiteres Video gefunden.")
                return
            GLib.idle_add(self._append_random, item)

        threading.Thread(target=worker, daemon=True).start()

    def _append_random(self, item: dict) -> bool:
        self.random_history.append(item)
        self.random_index = len(self.random_history) - 1
        self._switch_to(item)
        return False

    def _switch_to(self, item: dict) -> None:
        """Im selben Fenster auf ein anderes Video umstellen. Die Vorwahl von
        Qualität und Tonspur gilt weiter; Untertitel und Vorschaubilder
        gehören zum einzelnen Titel und werden neu geladen."""
        self._report_stop("closed")
        self._release_media()
        self.item = item
        self.item_id = int(self.item["id"])
        # Lokale Videos kommen von der Platte, Server-Videos aus dem Netz —
        # beim Umschalten muss das mitwandern, sonst spielt weiter dieselbe
        # Datei (bzw. es wird vergeblich der Server gefragt).
        self.local_path = item.get("path") if item.get("local") else None
        self._stop_reported = False
        self._start_reported = False
        self._watched_marked = False
        self._last_resume_sent = 0.0
        self._track = None
        self._trickplay = []
        self._sprite = None
        self.subtitle_stream = None
        self.subtitle_label.set_visible(False)
        self._virtual_offset = 0.0
        self._duration = float(self.item.get("durationSec") or 0)
        self._fresh_token = str(int(time.time() * 1000))
        # Dritte Absicherung: beim Umschalten auf ein anderes Video zaehlt
        # kein Spulen von vorhin mehr. Die Zeitleiste wird gleich mit auf den
        # Anfang gesetzt, sonst zeigt sie bis zum naechsten Takt noch den
        # Stand des vorherigen Titels.
        self._seeking = False
        adjustment = self.scale.get_adjustment()
        adjustment.set_upper(self._duration if self._duration > 0 else 1)
        adjustment.set_value(0)

        self.start_position = 0.0
        title = self._title_for(self.item, None)
        self.set_title(title)
        self.header.set_title_widget(Adw.WindowTitle(title=title))
        self.busy.set_visible(True)
        self.busy.start()
        self._update_step_buttons()
        # Über `_start_playback`, nicht direkt über `_resolve_stream`: ein
        # lokales Video liegt auf der Platte und hat beim Server nichts zu
        # suchen (seine Kennung ist dort gar nicht bekannt). Sonst bleibt beim
        # Weiterblättern in einer eigenen Bibliothek das Bild schwarz.
        self._start_playback()

    def _set_track(self, track: SubtitleTrack) -> bool:
        self._track = track
        return False

    def _on_media_error(self, media: Gtk.MediaFile, _p) -> None:
        if media is not self.media:
            return
        error = media.get_error()
        if error is None:
            return
        if not self.direct_url and not self.local_path:
            item_id = self.item_id
            self._background(lambda: self.client.playback_error(item_id, error.message))
        self._show_load_error(error.message)

    def _on_close_request(self, *_args) -> bool:
        self._report_stop("closed")
        self._release_media()
        if self._hide_bar_source is not None:
            GLib.source_remove(self._hide_bar_source)
            self._hide_bar_source = None
        return False  # Fenster trotzdem schließen lassen

    def _report_stop(self, reason: str) -> None:
        # Lokale Dateien und Trailer gehören nicht in die Wiedergabestatistik.
        if self._stop_reported or self.local_path or self.direct_url:
            return
        self._stop_reported = True
        media = self.media
        position = (media.get_timestamp() / 1_000_000) if media else 0.0
        duration = (media.get_duration() / 1_000_000) if media and media.get_duration() > 0 else 0.0
        item_id = self.item_id
        self._background(lambda: self.client.playback_stop(item_id, reason, position, duration))
        if position > 0:
            self._background(lambda: self.client.set_resume(item_id, position))

    # -- Diagnose-Anzeige ------------------------------------------------

    def _progress_params(self) -> dict:
        """Die Parameter der LAUFENDEN Umwandlung, so wie der Server sie kennt.

        Bewusst aus der tatsaechlich abgespielten URL abgeleitet und nicht aus
        `self.profile`/`self.audio_index` zusammengebaut: der Server sucht die
        Sitzung per exaktem Schluessel (Profil, Tonspur, Startsekunde,
        Zeilensprung). Wer hier auch nur einen Wert anders rät als in der
        Playlist-Anfrage, bekommt `noSession` zurueck — und saehe dauerhaft
        "kein Vorlauf", ohne dass etwas kaputt waere.

        `fresh`/`_t` gehoeren zum Neustart-Zwang und `session` zur
        Anmeldung; beide sind nicht Teil des Sitzungsschluessels und wuerden
        nur unnoetig mitgeschickt."""
        from urllib.parse import parse_qsl, urlparse

        params = dict(parse_qsl(urlparse(self._stream_base).query))
        for drop in ("session", "fresh", "_t", "start"):
            params.pop(drop, None)
        params["start"] = f"{self._virtual_offset:.3f}"
        return params

    def _poll_server_progress(self) -> None:
        """Holt den Stand der Umwandlung. Laeuft im Hintergrund-Thread."""
        item_id = self.item_id
        params = self._progress_params()
        try:
            data = self.client.transcode_progress(item_id, params)
        except GoldfishAPIError:
            return
        if data.get("noSession"):
            GLib.idle_add(self._apply_server_progress, None, False)
            return
        GLib.idle_add(
            self._apply_server_progress,
            float(data.get("positionSec") or 0.0),
            bool(data.get("done")),
        )

    def _apply_server_progress(self, position: float | None, done: bool) -> bool:
        self._server_position = position
        self._server_done = done
        return False

    def _update_stats(self, position: float, duration: float) -> None:
        """Fuellt das Aufklappfenster — nur, solange es offen ist.

        Der Abruf beim Server haelt die Umwandlung am Leben (Touch) und kostet
        eine Anfrage pro Sekunde; beides ist unerwuenscht, wenn niemand
        hinsieht."""
        if not self.stats_popover.get_visible():
            return

        mode = "Direkte Wiedergabe"
        if self._is_transcode:
            mode = "Umwandlung"
            if self.profile and self.profile != "orig":
                mode = f"Umwandlung ({self.profile})"
        elif self.local_path:
            mode = "Lokale Datei"
        self.stats_popover.set_value("mode", mode)

        ahead = None
        if self._server_position is not None:
            ahead = max(self._server_position - position, 0.0)
        text, level = format_ahead(ahead, self._server_done, self._is_transcode)
        self.stats_popover.set_value("ahead", text, level)

        self.stats_popover.set_value(
            "position",
            f"{format_duration(position)} / {format_duration(duration)}" if duration > 0
            else format_duration(position),
        )
        self.stats_popover.set_value("source", self._source_description())
        self.stats_popover.set_value("delivered", self._delivered_description())
        self.stats_popover.set_value("audio", self._audio_description())

        now = time.monotonic()
        if self._is_transcode and now - self._last_progress_poll >= 1.0:
            self._last_progress_poll = now
            self._background(self._poll_server_progress)

    def _source_description(self) -> str:
        """Was auf dem Server liegt — Auflösung, Codec, Bitrate der Quelle."""
        item = self.item or {}
        parts: list[str] = []
        width, height = int(item.get("width") or 0), int(item.get("height") or 0)
        if width and height:
            parts.append(f"{width}×{height}")
        for key in ("videoCodec", "container"):
            value = item.get(key)
            if value:
                parts.append(str(value).upper())
                break
        kbps = int(item.get("bitrateKbps") or 0)
        if kbps:
            parts.append(format_bitrate(kbps * 1000))
        return " · ".join(parts) or "—"

    def _delivered_description(self) -> str:
        """Was tatsaechlich ankommt — die einzige Zahl, die `Gtk.MediaFile`
        ueber den Datenstrom preisgibt (ueber das Paintable)."""
        media = self.media
        if media is None:
            return "—"
        width, height = media.get_intrinsic_width(), media.get_intrinsic_height()
        if not (width and height):
            return "—"
        text = f"{width}×{height}"
        label = format_resolution(width, height)
        return f"{text} ({label})" if label else text

    def _audio_description(self) -> str:
        if self.audio_index is None:
            return "Standard (erste Spur)"
        return f"Spur {self.audio_index}"

    def _measure_throughput(self) -> None:
        """Durchsatzmessung auf Knopfdruck, im Hintergrund."""
        item_id = self.item_id

        def worker() -> None:
            try:
                bits, read, elapsed = self.client.measure_throughput(item_id)
            except GoldfishAPIError as exc:
                GLib.idle_add(self.stats_popover.measurement_done, str(exc))
                return
            text = f"{format_bitrate(bits)} ({read / (1 << 20):.0f} MB in {elapsed:.1f} s)"
            GLib.idle_add(self.stats_popover.measurement_done, text)

        threading.Thread(target=worker, daemon=True).start()

    def _background(self, call) -> None:
        def worker() -> None:
            try:
                call()
            except GoldfishAPIError:
                pass  # Meldungen an den Server sind Beigabe, nie ein Blocker

        threading.Thread(target=worker, daemon=True).start()

    def _show_load_error(self, message: str) -> bool:
        status = Adw.StatusPage(
            icon_name="dialog-error-symbolic",
            title="Wiedergabe fehlgeschlagen",
            description=message,
        )
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        toolbar_view.set_content(status)
        self.set_content(toolbar_view)
        return False


def close_player(ctx) -> None:
    """Ein offenes Wiedergabefenster schließen, falls es eines gibt."""
    existing = getattr(ctx, "player_window", None)
    ctx.player_window = None
    if existing is not None:
        existing.close()


def open_player(ctx, item: dict, **kwargs) -> "PlayerWindow":
    """Öffnet das Wiedergabefenster — und zwar immer nur EINES.

    **Zwei Fenster übereinander waren eine Falle:** ein modaler Dialog des
    Hauptfensters ("Weiterschauen?") landete unsichtbar HINTER dem noch
    offenen, munter weiterspielenden alten Playerfenster. Klicks gingen an
    den Dialog, den man nicht sah; für den Benutzer hing die ganze App ("ich
    kann weder das Video stoppen noch Goldfish schließen"). Dazu hält jedes
    Fenster seine eigene GStreamer-Kette — acht davon waren 1,2 GB.

    Deshalb: vorher schließen, danach genau eines präsentieren."""
    close_player(ctx)
    window = PlayerWindow(ctx.application, ctx.client, item, **kwargs)
    window.set_transient_for(ctx.window)
    window.connect("close-request", lambda *_: _forget_player(ctx, window))
    ctx.player_window = window
    window.present()
    return window


def _forget_player(ctx, window) -> bool:
    if getattr(ctx, "player_window", None) is window:
        ctx.player_window = None
    return False  # schließen nicht verhindern
