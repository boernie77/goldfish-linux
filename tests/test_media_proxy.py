"""Der Medien-Proxy reicht Pfad, Query und Range an den Server durch."""

import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from goldfish_linux.media_proxy import proxied_url

DATA = bytes(range(256)) * 4


class _Upstream(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.seen.append((self.path, self.headers.get("Range")))
        start = 0
        rng = self.headers.get("Range")
        body = DATA
        status = 200
        if rng:
            a, b = rng.removeprefix("bytes=").split("-")
            start, end = int(a), int(b)
            body = DATA[start : end + 1]
            status = 206
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{start + len(body) - 1}/{len(DATA)}")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _start_upstream():
    server = HTTPServer(("127.0.0.1", 0), _Upstream)
    server.seen = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_range_wird_durchgereicht():
    up = _start_upstream()
    base = f"http://127.0.0.1:{up.server_address[1]}"
    url = proxied_url(f"{base}/api/stream/1?session=abc", base)
    assert url.startswith("http://127.0.0.1:") and not url.startswith(base)
    req = urllib.request.Request(url, headers={"Range": "bytes=10-19"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 206
        assert resp.read() == DATA[10:20]
        assert resp.headers["Content-Range"] == f"bytes 10-19/{len(DATA)}"
    assert up.seen == [("/api/stream/1?session=abc", "bytes=10-19")]
    up.shutdown()


def test_fremde_adresse_bleibt_unveraendert():
    other = "https://example.org/trailer.mp4?x=1"
    assert proxied_url(other, "http://127.0.0.1:1") == other
