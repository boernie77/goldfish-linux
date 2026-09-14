"""Ablauf für die Selbstaktualisierung: anbieten, laden, einspielen.

Getrennt von `updater.py` (das nichts von GTK weiss) und von `app.py` (das
sonst um den ganzen Ablauf wachsen wuerde). Hier steht nur die Bedienung.
"""

from __future__ import annotations

import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import __version__  # noqa: E402
from ..updater import Release, UpdateError, download, install  # noqa: E402

# Die Freigabe-Notizen von GitHub können lang sein; im Dialog reicht der
# Anfang, alles Weitere steht auf der Freigabe-Seite.
_NOTES_LIMIT = 1200


def offer_update(parent: Gtk.Window, release: Release) -> None:
    """Fragt, ob die neue Fassung eingespielt werden soll."""
    notes = release.notes or "Keine Beschreibung hinterlegt."
    if len(notes) > _NOTES_LIMIT:
        notes = notes[:_NOTES_LIMIT].rstrip() + " …"

    size = f" ({release.size / (1 << 20):.1f} MB)" if release.size else ""
    dialog = Adw.MessageDialog(
        transient_for=parent,
        heading=f"Aktualisierung auf {release.version}",
        body=f"Installiert ist {__version__}.{size}\n\n{notes}",
    )
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
