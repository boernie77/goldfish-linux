"""Meldungen des DownloadManagers an angemeldete Seiten (Download fertig,
fehlgeschlagen, gelöscht) — Grundlage dafür, dass die Detailseite ihren
Abspielen-Knopf auf "Offline abspielen" umstellt, während sie offen ist.

Läuft ohne GTK und ohne `requests`: `gi.repository.GLib` und `goldfish_linux.api`
werden durch Attrappen ersetzt, `GLib.idle_add` ruft sofort auf.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

# -- Attrappen, bevor goldfish_linux.downloads importiert wird ---------------

_glib = types.SimpleNamespace(
    idle_add=lambda func, *args: func(*args),
    DateTime=types.SimpleNamespace(
        new_now_utc=lambda: types.SimpleNamespace(format_iso8601=lambda: "2026-01-01T00:00:00Z")
    ),
)
_gi = types.ModuleType("gi")
_gi_repo = types.ModuleType("gi.repository")
_gi_repo.GLib = _glib
_gi.repository = _gi_repo
sys.modules.setdefault("gi", _gi)
sys.modules.setdefault("gi.repository", _gi_repo)

_api = types.ModuleType("goldfish_linux.api")


class _FakeAPIError(Exception):
    pass


_api.GoldfishAPIError = _FakeAPIError
_api.GoldfishClient = object
sys.modules.setdefault("goldfish_linux.api", _api)

from goldfish_linux import config, downloads  # noqa: E402


class _FakeResponse:
    headers = {"Content-Length": "6"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=0):
        yield b"abc"
        yield b"def"


class _FakeClient:
    def __init__(self, fail: bool = False):
        self.fail = fail

    def download_response(self, item_id, compat=False, profile=""):
        if self.fail:
            raise downloads.GoldfishAPIError("Server sagt nein")
        return _FakeResponse()


class DownloadListenerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._saved = (config.DOWNLOADS_DIR, config.DOWNLOADS_REGISTRY_FILE, config.ensure_dirs)
        config.DOWNLOADS_DIR = tmp / "downloads"
        config.DOWNLOADS_REGISTRY_FILE = tmp / "downloads.json"
        config.ensure_dirs = lambda: config.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        self.events: list[int] = []
        # Gebundene Methode wie in der Detailseite festhalten (schwache Referenz).
        self.listener = self.events.append

    def tearDown(self):
        config.DOWNLOADS_DIR, config.DOWNLOADS_REGISTRY_FILE, config.ensure_dirs = self._saved
        self._tmp.cleanup()

    def _run_download(self, manager, item):
        # Worker direkt im Testfaden statt über start_download (eigener Thread).
        manager._active.add(int(item["id"]))
        manager._download_worker(item, None, None, None)

    def test_done_meldet_und_titel_ist_offline(self):
        manager = downloads.DownloadManager(_FakeClient())
        manager.add_listener(self.listener)
        self.assertFalse(manager.is_downloaded(7))
        self._run_download(manager, {"id": 7, "title": "Film", "container": "mkv"})
        self.assertEqual(self.events, [7])
        self.assertTrue(manager.is_downloaded(7))
        self.assertFalse(manager.is_downloading(7))

    def test_loeschen_meldet_und_titel_ist_nicht_mehr_offline(self):
        manager = downloads.DownloadManager(_FakeClient())
        self._run_download(manager, {"id": 8, "title": "Film"})
        manager.add_listener(self.listener)
        manager.delete_download(8)
        self.assertEqual(self.events, [8])
        self.assertFalse(manager.is_downloaded(8))

    def test_fehlschlag_meldet_ebenfalls(self):
        manager = downloads.DownloadManager(_FakeClient(fail=True))
        manager.add_listener(self.listener)
        self._run_download(manager, {"id": 9, "title": "Film"})
        self.assertEqual(self.events, [9])
        self.assertFalse(manager.is_downloaded(9))
        self.assertFalse(manager.is_downloading(9))

    def test_abgemeldet_bekommt_nichts_mehr(self):
        manager = downloads.DownloadManager(_FakeClient())
        manager.add_listener(self.listener)
        manager.remove_listener(self.listener)
        self._run_download(manager, {"id": 10, "title": "Film"})
        self.assertEqual(self.events, [])
        self.assertEqual(manager._listeners, [])

    def test_tote_seite_wird_ausgeraeumt(self):
        manager = downloads.DownloadManager(_FakeClient())

        class Page:
            def on_change(self, item_id):
                raise AssertionError("tote Seite darf nicht gerufen werden")

        page = Page()
        cb = page.on_change
        manager.add_listener(cb)
        del cb, page  # Seite geschlossen und freigegeben
        manager.add_listener(self.listener)
        self._run_download(manager, {"id": 11, "title": "Film"})
        self.assertEqual(self.events, [11])
        self.assertEqual(len(manager._listeners), 1)


if __name__ == "__main__":
    unittest.main()
