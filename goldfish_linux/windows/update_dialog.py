"""Ablauf für die Selbstaktualisierung: anbieten, laden, einspielen.

Getrennt von `updater.py` (das nichts von GTK weiss) und von `app.py` (das
sonst um den ganzen Ablauf wachsen wuerde). Hier steht nur die Bedienung.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import __version__  # noqa: E402
from ..updater import Release, UpdateError, download, install  # noqa: E402

# Kurzfassung im Dialog (User-Vorgabe 2026-09-18: „nicht die ganze Geschichte
# erzählt werden, sondern nur kurz die Highlights — kurz und prägnant, mittig
# als Stichpunkte"). Höchstens so viele Punkte, jeder so kurz.
_HIGHLIGHT_MAX = 5
_HIGHLIGHT_MAX_CHARS = 110

# Klammerzusätze, die nur Herkunft/Datum nennen (siehe _strip_internal_notes):
# Wörter wie „User-Report"/„User-Vorgabe" oder ein Datum in der Klammer.
_INTERNAL_PAREN = re.compile(
    r"\([^()]*(?:User-Report|User-Vorgabe|User-Wunsch|CLAUDE|siehe |"
    r"\d{4}-\d{2}-\d{2})[^()]*\)"
)


def _clean_notes(notes: str) -> str:
    """Freigabe-Notizen für den Dialog aufbereiten.

    Zwei Dinge passieren hier (User-Vorgabe 2026-09-18):

    1. **Der Installations-Abschnitt fliegt raus.** Die GitHub-Notizen
       beginnen mit „## Installation" samt curl-/apt-Befehlen — die App spielt
       das Paket aber SELBST ein, die Anleitung zum Selber-Installieren ist an
       dieser Stelle nur Ballast. Behalten wird alles ab der Überschrift
       „Neu in dieser Version"; fehlt sie (ältere Freigaben), wird nur ein
       führender Installations-Abschnitt entfernt.
    2. **Markdown-Reste weg**: „## " vor Überschriften und Code-Zäune, damit
       der Text als Fließtext lesbar bleibt.
    """
    text = notes or ""
    marker = "Neu in dieser Version"
    idx = text.find(marker)
    if idx >= 0:
        # Über die Überschrift hinaus bis zum Zeilenende springen.
        nl = text.find("\n", idx)
        text = text[nl + 1:] if nl >= 0 else ""

    out: list[str] = []
    skipping = False
    for line in text.splitlines():
        stripped = line.strip()
        is_heading = stripped.startswith("#")
        if is_heading and "installation" in stripped.lower():
            # Installations-Block aus: bis zur nächsten Überschrift überspringen.
            skipping = True
            continue
        if skipping:
            if is_heading:
                skipping = False
            else:
                continue
        if stripped.startswith("```"):
            continue  # Code-Zaun ohne Inhalt für einen Fließtext-Dialog
        if is_heading:
            out.append(stripped.lstrip("#").strip().upper())
        else:
            out.append(line.rstrip())
    cleaned = "\n".join(out).strip()
    return cleaned or "Keine Beschreibung hinterlegt."


def _strip_internal_notes(text: str) -> str:
    """Interne Klammerzusätze entfernen — sie gehören nicht in eine Kurzfassung.

    Die Änderungsberichte dieses Projekts nennen in Klammern gern Herkunft und
    Datum („(User-Report 2026-09-18, zweimal: …)", „(User-Vorgabe …)",
    „(siehe CLAUDE.md …)"). Für den Nutzer ist das Ballast — und die erste
    Fassung dieser Kürzung schnitt mitten hinein, weil sie nur nach Zeichen
    zählte. Ein regulärer Ausdruck trifft die Klammer als Ganzes, auch
    verschachtelte Nebensätze darin bleiben unangetastet.
    """
    cleaned = _INTERNAL_PAREN.sub("", text or "")
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)
    return cleaned.strip()


def _highlights(notes: str) -> list[str]:
    """Kurzfassung der Neuerungen: Stichpunkte, je der erste Satz.

    User-Vorgabe 2026-09-18: im Aktualisierungs-Dialog nicht „die ganze
    Geschichte", sondern kurz und prägnant die Highlights, mittig als
    Stichpunkte.

    ⚠ Zuerst werden mehrzeilige Stichpunkte ZUSAMMENGEFÜGT: der Änderungsbericht
    ist auf ~76 Zeichen umbrochen, ein Punkt besteht also aus mehreren Zeilen.
    Nur die erste Zeile zu nehmen lieferte Fragmente mitten in einer Klammer
    („… (User-Report 2026-09-18, zweimal:") — genau das war in der ersten
    Fassung dieses Kürzens zu sehen.
    """
    cleaned = _clean_notes(notes)

    # Logische Stichpunkte bilden: eine Zeile mit Aufzählungszeichen beginnt
    # einen Punkt, jede folgende Zeile ohne Aufzählungszeichen gehört dazu.
    bullets: list[str] = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("- ", "* ", "• ")):
            bullets.append(stripped)
        elif bullets:
            bullets[-1] += " " + stripped

    out: list[str] = []
    for bullet in bullets:
        text = _strip_internal_notes(bullet.lstrip("-*• ").strip())
        # Der erste Satz trägt die Aussage; Aufzählungen ohne Punkt bleiben ganz.
        cut = text.find(". ")
        if cut > 0:
            text = text[: cut + 1]
        text = text.rstrip(". ")
        if len(text) > _HIGHLIGHT_MAX_CHARS:
            text = text[:_HIGHLIGHT_MAX_CHARS].rsplit(" ", 1)[0] + " …"
        if text:
            out.append(text)
        if len(out) >= _HIGHLIGHT_MAX:
            break

    if out:
        return out
    first = _strip_internal_notes(cleaned).split(". ")[0].strip().rstrip(".")
    return [first[: _HIGHLIGHT_MAX_CHARS * 2]] if first else ["Fehlerbehebungen und Verbesserungen."]


def offer_update(parent: Gtk.Window, release: Release) -> None:
    """Fragt, ob die neue Fassung eingespielt werden soll.

    Aufbau (User-Vorgabe 2026-09-18): Die Zeile mit der installierten und der
    verfügbaren Fassung steht FEST oben — vorher stand sie im Fließtext der
    Notizen und wanderte beim Scrollen weg („die Zeile springt"). Darunter
    stehen nur noch wenige, mittige Stichpunkte zu den Neuerungen (siehe
    `_highlights`), kein Fließtext.
    """
    size = f"  ({release.size / (1 << 20):.1f} MB)" if release.size else ""

    # Kopf: fest, nicht scrollbar.
    head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    installed = Gtk.Label(label=f"Installiert: {__version__}", xalign=0)
    installed.add_css_class("dim-label")
    available = Gtk.Label(label=f"Verfügbar: {release.version}{size}", xalign=0)
    available.add_css_class("heading")
    head.append(installed)
    head.append(available)

    # Neuerungen: kurze Stichpunkte, mittig. Kein Fließtext, kein Scrollbereich
    # (max. `_HIGHLIGHT_MAX` kurze Zeilen) — der Kopf darüber bleibt dadurch
    # automatisch an seinem Platz.
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    content.append(head)
    content.append(Gtk.Separator())
    for point in _highlights(release.notes):
        bullet = Gtk.Label(
            label=f"•  {point}",
            wrap=True,
            justify=Gtk.Justification.CENTER,
            halign=Gtk.Align.CENTER,
            max_width_chars=52,
            margin_top=2,
            margin_bottom=2,
        )
        content.append(bullet)

    dialog = Adw.MessageDialog(
        transient_for=parent,
        heading=f"Aktualisierung auf {release.version}",
        body=None,
    )
    dialog.set_extra_child(content)
    dialog.add_response("cancel", "Später")
    dialog.add_response("install", "Herunterladen und einspielen")
    dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("install")
    dialog.set_close_response("cancel")
    dialog.connect("response", lambda d, r: _on_response(parent, release, r))
    dialog.present()


def _on_response(parent: Gtk.Window, release: Release, response: str) -> None:
    if response != "install":
        return
    _run_install(parent, release)


def _run_install(parent: Gtk.Window, release: Release) -> None:
    progress_dialog = Adw.MessageDialog(
        transient_for=parent,
        heading=f"Aktualisierung auf {release.version}",
        body="Paket wird geladen …",
    )
    bar = Gtk.ProgressBar(show_text=True, margin_top=6)
    progress_dialog.set_extra_child(bar)
    # Kein Abbruch-Knopf während des Einspielens: ein halb eingespieltes
    # Paket ist schlimmer als ein zu Ende gefuehrtes. Der Rechte-Dialog von
    # PolicyKit laesst sich ohnehin abbrechen, das ist der sichere Ausstieg.
    progress_dialog.present()

    def set_progress(fraction: float) -> bool:
        bar.set_fraction(fraction)
        bar.set_text(f"{fraction * 100:.0f} %")
        return False

    def set_body(text: str) -> bool:
        progress_dialog.set_body(text)
        return False

    def done(error: str | None) -> bool:
        progress_dialog.close()
        if error:
            fail = Adw.MessageDialog(
                transient_for=parent,
                heading="Aktualisierung fehlgeschlagen",
                body=error,
            )
            fail.add_response("ok", "Schließen")
            fail.present()
            return False
        ok = Adw.MessageDialog(
            transient_for=parent,
            heading=f"Fassung {release.version} eingespielt",
            body="Die Anwendung muss neu gestartet werden, damit die neue "
                 "Fassung läuft.",
        )
        ok.add_response("later", "Später")
        ok.add_response("quit", "Jetzt beenden")
        ok.set_response_appearance("quit", Adw.ResponseAppearance.SUGGESTED)
        # Bewusst nur beenden, nicht neu starten: ein Neustart aus dem gerade
        # ausgetauschten Programm heraus laedt halb alte, halb neue Dateien.
        ok.connect("response", lambda d, r: parent.get_application().quit() if r == "quit" else None)
        ok.present()
        return False

    def worker() -> None:
        try:
            target_dir = Path(GLib.get_user_cache_dir()) / "goldfish-linux" / "updates"
            path = download(release, target_dir,
                            progress=lambda f: GLib.idle_add(set_progress, f))
            GLib.idle_add(set_body, "Paket wird eingespielt — bitte die "
                                    "Rechteabfrage bestätigen …")
            GLib.idle_add(set_progress, 1.0)
            install(path)
        except UpdateError as exc:
            GLib.idle_add(done, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — der Dialog darf nie hängenbleiben
            GLib.idle_add(done, f"Unerwarteter Fehler: {exc}")
            return
        GLib.idle_add(done, None)

    threading.Thread(target=worker, daemon=True).start()
