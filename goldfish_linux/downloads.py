"""Offline-Downloads: lädt die Originaldatei über /api/download/{id} herunter
und hält eine kleine JSON-Registry (Item-ID → lokaler Pfad + Metadaten), damit
die App nach einem Neustart weiß, was offline verfügbar ist — ohne dafür eine
eigene Datenbank zu brauchen."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Callable

from gi.repository import GLib

from . import config
from .api import GoldfishAPIError, GoldfishClient

_SANITIZE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

ProgressCB = Callable[[int, float], None]  # (item_id, fraction 0..1)
DoneCB = Callable[[int, str], None]  # (item_id, local_path)
ErrorCB = Callable[[int, str], None]  # (item_id, message)


def _sanitize_filename(name: str) -> str:
    name = _SANITIZE_RE.sub("", name).strip(" .")
    return name or "video"


class DownloadManager:
    def __init__(self, client: GoldfishClient):
        self.client = client
        self._registry: dict[str, dict] = {}
        self._active: set[int] = set()
        self._load()

    # -- Registry --------------------------------------------------------

    def _load(self) -> None:
        config.ensure_dirs()
        if not config.DOWNLOADS_REGISTRY_FILE.exists():
            return
        try:
            self._registry = json.loads(config.DOWNLOADS_REGISTRY_FILE.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            self._registry = {}

    def _save(self) -> None:
        config.ensure_dirs()
        config.DOWNLOADS_REGISTRY_FILE.write_text(json.dumps(self._registry, indent=2), "utf-8")

    def list_downloads(self) -> list[dict]:
        return sorted(self._registry.values(), key=lambda r: r.get("downloadedAt", ""), reverse=True)

    def is_downloaded(self, item_id: int) -> bool:
        rec = self._registry.get(str(item_id))
        return bool(rec and Path(rec["path"]).exists())

    def is_downloading(self, item_id: int) -> bool:
        return item_id in self._active

    def local_path(self, item_id: int) -> Path | None:
        rec = self._registry.get(str(item_id))
        if rec and Path(rec["path"]).exists():
            return Path(rec["path"])
        return None

    def delete_download(self, item_id: int) -> None:
        rec = self._registry.pop(str(item_id), None)
        if rec:
            try:
                Path(rec["path"]).unlink(missing_ok=True)
            except OSError:
                pass
            self._save()

    # -- Download ----------------------------------------------------------

    def start_download(
        self,
        item: dict,
        on_progress: ProgressCB | None = None,
        on_done: DoneCB | None = None,
        on_error: ErrorCB | None = None,
    ) -> None:
        item_id = int(item["id"])
        if item_id in self._active:
            return
        self._active.add(item_id)
        thread = threading.Thread(
            target=self._download_worker,
            args=(item, on_progress, on_done, on_error),
            daemon=True,
        )
        thread.start()

    def _download_worker(
        self,
        item: dict,
        on_progress: ProgressCB | None,
        on_done: DoneCB | None,
        on_error: ErrorCB | None,
    ) -> None:
        item_id = int(item["id"])
        try:
            resp = self.client.download_response(item_id)
        except GoldfishAPIError as exc:
            self._active.discard(item_id)
            if on_error:
                GLib.idle_add(on_error, item_id, str(exc))
            return

        title = item.get("title") or f"item-{item_id}"
        ext = (item.get("container") or "mp4").lower()
        filename = f"{_sanitize_filename(title)}-{item_id}.{ext}"
        config.ensure_dirs()
        final_path = config.DOWNLOADS_DIR / filename
        tmp_path = final_path.with_suffix(final_path.suffix + ".part")

        total = int(resp.headers.get("Content-Length") or item.get("sizeBytes") or 0)
        downloaded = 0
        try:
            with resp, open(tmp_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if on_progress and total > 0:
                        fraction = min(1.0, downloaded / total)
                        GLib.idle_add(on_progress, item_id, fraction)
            tmp_path.rename(final_path)
        except OSError as exc:
            tmp_path.unlink(missing_ok=True)
            self._active.discard(item_id)
            if on_error:
                GLib.idle_add(on_error, item_id, f"Datei konnte nicht geschrieben werden: {exc}")
            return

        self._registry[str(item_id)] = {
            "id": item_id,
            "title": title,
            "path": str(final_path),
            "sizeBytes": downloaded,
            "durationSec": item.get("durationSec", 0),
            "downloadedAt": GLib.DateTime.new_now_utc().format_iso8601(),
        }
        self._save()
        self._active.discard(item_id)
        if on_done:
            GLib.idle_add(on_done, item_id, str(final_path))
