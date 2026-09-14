"""Selbstaktualisierung: neuestes Release von GitHub holen und einspielen.

Die App wird als `.deb` verteilt und hat kein eigenes APT-Repository — ohne
diesen Weg müsste man jede neue Fassung von Hand auf GitHub suchen,
herunterladen und installieren. Freigaben entstehen ausschliesslich über
einen `v*`-Tag (siehe .github/workflows/release.yml), der das Paket baut und
als Anhang an das Release legt; genau dieses Paket wird hier geholt.

**Bewusst nur auf Knopfdruck, nie von selbst.** Ein Update installiert Code
und braucht Verwaltungsrechte — das gehört in die Hand des Benutzers, nicht
in einen Hintergrund-Zeitgeber.

**Sicherheit:** die API-Adresse ist fest verdrahtet, und die Adresse des
Pakets wird gegen eine Liste erlaubter Rechnernamen geprüft, bevor
heruntergeladen wird. Ein Release, das (etwa durch ein übernommenes Konto)
auf eine fremde Adresse zeigt, wird abgelehnt statt blind geladen.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests

from . import __version__

GITHUB_REPO = "boernie77/goldfish-linux"
_LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# GitHub liefert Anhänge über wechselnde Rechner aus (die API-Adresse leitet
# auf den Objektspeicher um). Alles ausserhalb dieser Liste wird abgelehnt.
_ALLOWED_HOSTS = {
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}


class UpdateError(Exception):
    """Fehler beim Suchen, Laden oder Einspielen einer neuen Fassung."""


@dataclass
class Release:
    version: str  # "0.1.40" — ohne führendes v
    tag: str  # "v0.1.40"
    notes: str
    deb_url: str
    deb_name: str
    size: int


def parse_version(text: str) -> tuple[int, ...]:
    """"v0.1.40" oder "0.1.40-1" → (0, 1, 40). Nicht-Ziffern werden verworfen.

    Die Paketfassung hinter dem Bindestrich (`-1`) gehört zur Debian-Revision
    und nicht zur Programmfassung — sie bleibt hier bewusst außen vor."""
    core = text.strip().lstrip("vV").split("-", 1)[0]
    parts = [int(m) for m in re.findall(r"\d+", core)]
    return tuple(parts) if parts else (0,)


def is_newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


def latest_release(timeout: float = 15.0) -> Release:
    """Fragt das neueste Release ab. Wirft UpdateError bei jedem Problem."""
    try:
        resp = requests.get(
            _LATEST_URL,
            timeout=timeout,
            headers={"Accept": "application/vnd.github+json"},
        )
    except requests.RequestException as exc:
        raise UpdateError(f"GitHub nicht erreichbar: {exc}") from exc
    if resp.status_code == 404:
        raise UpdateError("Es gibt noch keine Freigabe.")
    if resp.status_code >= 400:
        raise UpdateError(f"GitHub antwortete mit HTTP {resp.status_code}.")
    data = resp.json()

    tag = str(data.get("tag_name") or "")
    if not tag:
        raise UpdateError("Die Antwort von GitHub enthält keine Fassungsnummer.")

    asset = next(
        (a for a in (data.get("assets") or []) if str(a.get("name", "")).endswith(".deb")),
        None,
    )
    if asset is None:
        raise UpdateError(f"Zur Freigabe {tag} gehört kein .deb-Paket.")

    url = str(asset.get("browser_download_url") or "")
    _check_host(url)
    return Release(
        version=tag.lstrip("vV"),
        tag=tag,
        notes=str(data.get("body") or "").strip(),
        deb_url=url,
        deb_name=str(asset.get("name") or "goldfish-linux.deb"),
        size=int(asset.get("size") or 0),
    )


def _check_host(url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if urlparse(url).scheme != "https" or host not in _ALLOWED_HOSTS:
        raise UpdateError(f"Unerwartete Bezugsquelle ({host or 'keine'}) — abgebrochen.")


def download(release: Release, dest_dir: Path,
             progress: Callable[[float], None] | None = None) -> Path:
    """Lädt das Paket nach `dest_dir` und liefert den Pfad.

    `progress` bekommt Werte zwischen 0 und 1 — sofern GitHub eine Länge
    mitschickt, sonst gar nicht."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / release.deb_name
    tmp = target.with_suffix(target.suffix + ".part")
    try:
        with requests.get(release.deb_url, stream=True, timeout=60) as resp:
            if resp.status_code >= 400:
                raise UpdateError(f"Paket nicht ladbar (HTTP {resp.status_code}).")
            # Nach dem Umleiten erneut prüfen: die letzte Adresse zählt.
            _check_host(resp.url)
            total = int(resp.headers.get("Content-Length") or release.size or 0)
            done = 0
            with tmp.open("wb") as fh:
                for chunk in resp.iter_content(256 * 1024):
                    fh.write(chunk)
                    done += len(chunk)
                    if progress and total:
                        progress(min(done / total, 1.0))
    except requests.RequestException as exc:
        tmp.unlink(missing_ok=True)
        raise UpdateError(f"Laden fehlgeschlagen: {exc}") from exc
    # Erst am Ende umbenennen: ein Abbruch hinterlässt so kein Paket, das
    # aussieht, als wäre es vollständig.
    tmp.replace(target)
    return target


def install(deb_path: Path) -> None:
    """Spielt das Paket ein. Öffnet den Rechteabfrage-Dialog von PolicyKit.

    `apt-get install` statt `dpkg -i`: apt zieht fehlende Abhängigkeiten
    selbst nach, `dpkg` bräche stattdessen mit halb eingerichtetem Paket ab.
    """
    if not deb_path.exists():
        raise UpdateError("Das geladene Paket ist verschwunden.")
    try:
        proc = subprocess.run(
            ["pkexec", "apt-get", "install", "-y", "--allow-downgrades", str(deb_path)],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except FileNotFoundError as exc:
        raise UpdateError(
            "pkexec fehlt — bitte das Paket von Hand einspielen:\n"
            f"sudo apt install {deb_path}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise UpdateError("Zeitüberschreitung beim Einspielen.") from exc
    if proc.returncode == 126:
        # PolicyKit gibt 126 zurück, wenn die Abfrage abgebrochen wurde.
        raise UpdateError("Abgebrochen — es wurde nichts verändert.")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise UpdateError("Einspielen fehlgeschlagen: " + (detail[-1] if detail else "unbekannter Fehler"))
