"""Player-Fenster — nutzt GTK4s eingebauten Gtk.Video-Widget.

Gtk.Video kapselt bereits Play/Pause/Seek/Vollbild/Lautstärke-Controls; die
eigentliche Dekodierung läuft über den GStreamer-Media-Backend von GTK
(playbin), das sowohl lokale Dateien (Downloads) als auch HTTP(S)-Streams
inkl. HLS (`index.m3u8`, Server-Transcode) über eine simple `Gio.File`-URI
abspielen kann — vorausgesetzt die passenden GStreamer-Plugins sind
installiert (siehe README/Paket-Dependencies: gstreamer1.0-plugins-{good,bad}
für HTTP/HLS, gstreamer1.0-libav für H.264/AAC-Decoding).

Auth ohne Cookie-Jar: der Stream-/Transcode-URL wird `?session=<token>`
angehängt (derselbe Fallback-Mechanismus, den der Server für Cast-Receiver
wie Chromecast bereitstellt, siehe api.GoldfishClient.with_session_param).
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from ..api import GoldfishAPIError, GoldfishClient  # noqa: E402


class PlayerWindow(Adw.Window):
    def __init__(self, app, client: GoldfishClient, item: dict, local_path: str | None = None):
        title = item.get("metadata", {}).get("title") if item.get("metadata") else None
        title = title or item.get("title") or "Wiedergabe"
        super().__init__(application=app, title=title)
        self.client = client
        self.item = item
        self.item_id = int(item["id"])
        self.local_path = local_path
        self._stop_reported = False
        self.set_default_size(1024, 640)

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=title))

        self.video = Gtk.Video()
        self.video.set_hexpand(True)
        self.video.set_vexpand(True)
        self.video.set_autoplay(True)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(self.video)
        self.set_content(toolbar_view)

        self.connect("close-request", self._on_close_request)

        if local_path:
            self._load_file(Gio.File.new_for_path(local_path))
        else:
            self._load_stream()

    def _load_file(self, gio_file: Gio.File) -> None:
        self.video.set_file(gio_file)
        self._watch_media_stream()

    def _load_stream(self) -> None:
        try:
            info = self.client.playback_info(self.item_id, mode="auto")
        except GoldfishAPIError as exc:
            self._show_load_error(str(exc))
            return
        url = info.get("url")
        if not url:
            self._show_load_error("Server lieferte keine Wiedergabe-URL.")
            return
        full_url = self.client.with_session_param(url)
        self.client.playback_start(self.item_id)
        self._load_file(Gio.File.new_for_uri(full_url))

    def _watch_media_stream(self) -> None:
        stream = self.video.get_media_stream()
        if stream:
            stream.connect("notify::ended", self._on_stream_ended)
            stream.connect("notify::error", self._on_stream_error)

    def _on_stream_ended(self, stream, _pspec) -> None:
        if stream.get_ended():
            self._report_stop("ended")

    def _on_stream_error(self, stream, _pspec) -> None:
        error = stream.get_error()
        if error and not self.local_path:
            self.client.playback_error(self.item_id, error.message)

    def _on_close_request(self, *_args) -> bool:
        self._report_stop("closed")
        return False  # Fenster trotzdem schließen lassen

    def _report_stop(self, reason: str) -> None:
        if self._stop_reported or self.local_path:
            return
        self._stop_reported = True
        stream = self.video.get_media_stream()
        position_sec = (stream.get_timestamp() / 1_000_000) if stream else 0.0
        duration_sec = (stream.get_duration() / 1_000_000) if stream and stream.get_duration() > 0 else 0.0
        self.client.playback_stop(self.item_id, reason, position_sec, duration_sec)

    def _show_load_error(self, message: str) -> None:
        status = Adw.StatusPage(
            icon_name="dialog-error-symbolic",
            title="Wiedergabe fehlgeschlagen",
            description=message,
        )
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        toolbar_view.set_content(status)
        self.set_content(toolbar_view)
