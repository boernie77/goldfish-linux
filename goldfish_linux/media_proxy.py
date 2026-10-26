"""Lokaler Durchreich-Proxy für alles, was GStreamer vom Server holt.

Hintergrund: der API-Client erzwingt IPv4 (`api._force_ipv4_only`), GStreamers
`souphttpsrc` tut das nicht. Hat die Server-Domain einen AAAA-Eintrag, der vom
eigenen Netz aus nicht erreichbar ist (Router-Hairpinning), nimmt libsoup die
IPv6-Adresse und scheitert mit „Internal data stream error (-5)", obwohl
API-Aufrufe und Browser funktionieren. Der Proxy lässt GStreamer deshalb nur
mit `127.0.0.1` reden; die Verbindung zum Server baut `requests` auf — und
damit über IPv4.

Er wird nur von `api.GoldfishClient.media_url` aufgerufen — also erst, wenn
`api` und damit das IPv4-only geladen ist.

Der Proxy gibt Anfragen unverändert weiter (Pfad, Query, `Range`) und trägt
selbst keine Zugangsdaten: die Anmeldung steckt wie bisher im
`?session=`-Parameter der URL. Ein anderer lokaler Benutzer bekäme darüber
also nichts, was er nicht schon mit dem Token hätte.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, urlunparse

import requests

from . import __version__

# Nur diese Antwort-Header sind für den Abspieler relevant. Alles andere
# (Connection, Transfer-Encoding, Server, …) gehört der jeweiligen Strecke.
_FORWARD_HEADERS = (
    "Content-Type",
    "Content-Length",
    "Content-Range",
    "Accept-Ranges",
    "Cache-Control",
    "ETag",
    "Last-Modified",
)
_CHUNK = 64 * 1024
# Verbindungsaufbau knapp, Lesen großzügig: ein Transcode braucht beim ersten
# Segment einige Sekunden, bis ffmpeg etwas liefert.
_UPSTREAM_TIMEOUT = (10, 120)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: "_ProxyServer"

    def do_GET(self) -> None:
        self._relay(send_body=True)

    def do_HEAD(self) -> None:
        self._relay(send_body=False)

    def _relay(self, send_body: bool) -> None:
        headers = {
            "User-Agent": f"Goldfish-Linux/{__version__}",
            "X-Goldfish-Client": f"GoldfishLinux/{__version__}",
            # Unkomprimiert, damit Content-Length zu den durchgereichten Bytes passt.
            "Accept-Encoding": "identity",
        }
        for name in ("Range", "If-Range", "If-None-Match", "If-Modified-Since"):
            if self.headers.get(name):
                headers[name] = self.headers[name]
        url = self.server.upstream_base + self.path
        try:
            resp = self.server.upstream.request(
                "GET" if send_body else "HEAD",
                url,
                headers=headers,
                stream=True,
                timeout=_UPSTREAM_TIMEOUT,
                allow_redirects=True,
            )
        except requests.RequestException:
            self._fail(502)
            return
        try:
            self.send_response(resp.status_code)
            has_length = False
            for name in _FORWARD_HEADERS:
                value = resp.headers.get(name)
                if value is None:
                    continue
                if name == "Content-Length":
                    has_length = True
                self.send_header(name, value)
            if not has_length and send_body and resp.status_code not in (204, 304):
                # Ohne Länge (Server sendet chunked) bleibt nur: Verbindung
                # nach dem Körper schließen.
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if send_body:
                for chunk in resp.iter_content(_CHUNK):
                    if chunk:
                        self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            # Der Abspieler hat die Verbindung abgebrochen (Sprung, Ende) — normal.
            self.close_connection = True
        except requests.RequestException:
            # Upstream riss mitten im Körper ab: Verbindung kappen, damit der
            # Abspieler den Fehler sieht statt auf fehlende Bytes zu warten.
            self.close_connection = True
        finally:
            resp.close()

    def _fail(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


class _ProxyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, upstream_base: str):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.upstream_base = upstream_base
        # Eigene Session ohne Cookie-Jar: die Anmeldung kommt aus der URL.
        self.upstream = requests.Session()


_lock = threading.Lock()
_servers: dict[str, _ProxyServer] = {}


def proxied_url(url: str, base_url: str) -> str:
    """Schreibt `url` auf den lokalen Proxy um, wenn sie auf `base_url` zeigt.

    Fremde Adressen (z. B. externe Trailer) und alles, wenn der Proxy nicht
    starten will, bleiben unverändert — dann gilt das alte Verhalten."""
    base = urlparse(base_url)
    target = urlparse(url)
    if not base.netloc or (target.scheme, target.netloc) != (base.scheme, base.netloc):
        return url
    origin = f"{base.scheme}://{base.netloc}"
    with _lock:
        server = _servers.get(origin)
        if server is None:
            try:
                server = _ProxyServer(origin)
            except OSError:
                return url
            threading.Thread(target=server.serve_forever, daemon=True, name="media-proxy").start()
            _servers[origin] = server
    port = server.server_address[1]
    return urlunparse(("http", f"127.0.0.1:{port}", target.path, target.params, target.query, ""))
