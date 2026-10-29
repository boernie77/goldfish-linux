"""Verwaltungs-Dialoge für Administratoren: Verschieben, Umbenennen,
Zuordnung ändern und Metadaten bearbeiten.

Der Server prüft die Admin-Rechte bei jeder dieser Routen selbst; die App
blendet die Bedienung nur aus, wenn das Konto kein Administrator ist
(`AppContext.is_admin`). Jede Aktion läuft im Hintergrund-Thread, das
Ergebnis kommt per `GLib.idle_add` zurück in die Oberfläche."""

from __future__ import annotations

import json
import posixpath
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..api import GoldfishAPIError  # noqa: E402


def _toast(ctx, message: str) -> bool:
    window = getattr(ctx, "window", None)
    if window is not None and hasattr(window, "show_toast"):
        window.show_toast(message)
    return False


def _background(ctx, work, on_ok=None, error_prefix: str = "Fehlgeschlagen") -> None:
    def runner() -> None:
        try:
            result = work()
        except GoldfishAPIError as exc:
            GLib.idle_add(_toast, ctx, f"{error_prefix}: {exc}")
            return
        if on_ok is not None:
            GLib.idle_add(on_ok, result)

    threading.Thread(target=runner, daemon=True).start()


# -- Umbenennen ---------------------------------------------------------


def rename_item(ctx, item: dict, on_done) -> None:
    name = (item.get("path") or item.get("relPath") or "").rsplit("/", 1)[-1]
    dialog = Adw.MessageDialog(
        transient_for=ctx.dialog_parent(),
        heading="Datei umbenennen?",
        body=f"„{name}“ wird nach dem Namensschema aus der TMDB-Zuordnung umbenannt. "
        "Die Umbenennung lässt sich im Protokoll nachvollziehen.",
    )
    dialog.add_response("cancel", "Abbrechen")
    dialog.add_response("ok", "Umbenennen")
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

    def answered(_d, response: str) -> None:
        if response != "ok":
            return

        def done(result: dict) -> bool:
            _toast(ctx, f"Umbenannt: {result.get('newBase') or 'ok'}")
            on_done()
            return False

        _background(ctx, lambda: ctx.client.rename_item(int(item["id"])), done, "Umbenennen fehlgeschlagen")

    dialog.connect("response", answered)
    dialog.present()


# -- Verschieben ----------------------------------------------------------


def move_item(ctx, item: dict, on_done) -> None:
    libraries = sorted(ctx.libraries_by_id.values(), key=lambda lib: (lib.get("name") or "").lower())
    current_lib = int(item.get("libraryId") or 0)
    dialog = Adw.MessageDialog(
        transient_for=ctx.dialog_parent(),
        heading="Verschieben",
        body="Die Datei wird auf dem Server physisch verschoben. Ein neuer Ordnername wird bei Bedarf angelegt.",
    )
    group = Adw.PreferencesGroup()
    lib_row = Adw.ComboRow(title="Ziel-Bibliothek")
    lib_row.set_model(Gtk.StringList.new([lib.get("name") or "?" for lib in libraries]))
    for i, lib in enumerate(libraries):
        if int(lib.get("id") or 0) == current_lib:
            lib_row.set_selected(i)
    group.add(lib_row)
    folder_row = Adw.EntryRow(title="Zielordner (leer = Wurzel)")
    rel_dir = posixpath.dirname(item.get("relPath") or "")
    folder_row.set_text(rel_dir)
    group.add(folder_row)
    dialog.set_extra_child(group)
    dialog.add_response("cancel", "Abbrechen")
    dialog.add_response("ok", "Verschieben")
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

    def answered(_d, response: str) -> None:
        if response != "ok" or not libraries:
            return
        target_lib = int(libraries[lib_row.get_selected()].get("id") or current_lib)
        target_folder = folder_row.get_text().strip().strip("/")
        _run_move(ctx, item, target_folder, target_lib, on_done)

    dialog.connect("response", answered)
    dialog.present()


def _run_move(ctx, item: dict, target_folder: str, target_lib: int, on_done) -> None:
    item_id = int(item["id"])

    def preview() -> dict:
        return ctx.client.move_preview(item_id, target_folder, target_lib)

    def after_preview(result: dict) -> bool:
        if result.get("anyCrossDevice"):
            ask = Adw.MessageDialog(
                transient_for=ctx.dialog_parent(),
                heading="Datenträgerwechsel",
                body="Die Datei würde auf einen ANDEREN Datenträger kopiert, nicht nur innerhalb "
                "desselben Laufwerks verschoben. Das kann je nach Größe dauern. Wirklich verschieben?",
            )
            ask.add_response("cancel", "Abbrechen")
            ask.add_response("ok", "Ja, verschieben")
            ask.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
            ask.connect("response", lambda _d, r: r == "ok" and _do_move(True))
            ask.present()
        else:
            _do_move(False)
        return False

    def _do_move(cross: bool) -> None:
        def finished(_result: dict) -> bool:
            _toast(ctx, "Verschoben")
            on_done()
            return False

        _background(
            ctx,
            lambda: ctx.client.move_item(item_id, target_folder, target_lib, cross),
            finished,
            "Verschieben fehlgeschlagen",
        )

    _background(ctx, preview, after_preview, "Verschieben nicht möglich")


# -- Zuordnung ändern -------------------------------------------------------

_MATCH_KINDS = [("Film", "movie"), ("Serie", "tv"), ("Episode einer Serie", "episode")]


def match_item(ctx, item: dict, on_done) -> None:
    metadata = item.get("metadata") or {}
    dialog = Adw.MessageDialog(
        transient_for=ctx.dialog_parent(),
        heading="Zuordnung ändern",
        body="Titel suchen und den richtigen TMDB-Eintrag wählen.",
    )
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_size_request(440, -1)
    group = Adw.PreferencesGroup()
    query = Adw.EntryRow(title="Titel")
    query.set_text(metadata.get("title") or item.get("title") or "")
    group.add(query)
    kind_row = Adw.ComboRow(title="Art")
    kind_row.set_model(Gtk.StringList.new([label for label, _ in _MATCH_KINDS]))
    if metadata.get("tmdbType") == "episode":
        kind_row.set_selected(2)
    elif metadata.get("tmdbType") == "tv":
        kind_row.set_selected(1)
    group.add(kind_row)
    season = Adw.SpinRow.new_with_range(0, 99, 1)
    season.set_title("Staffel (nur Episode)")
    season.set_value(float(metadata.get("season") or 1))
    group.add(season)
    episode = Adw.SpinRow.new_with_range(0, 999, 1)
    episode.set_title("Folge (nur Episode)")
    episode.set_value(float(metadata.get("episode") or 1))
    group.add(episode)
    box.append(group)

    results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    results.add_css_class("boxed-list")
    scroll = Gtk.ScrolledWindow(child=results, propagate_natural_height=True, max_content_height=260)
    box.append(scroll)
    status = Gtk.Label(label="", xalign=0, wrap=True)
    status.add_css_class("dim-label")
    box.append(status)
    search_button = Gtk.Button(label="Suchen")
    box.append(search_button)

    dialog.set_extra_child(box)
    dialog.add_response("close", "Schließen")
    dialog.add_response("unmatch", "Zuordnung entfernen")
    dialog.set_response_appearance("unmatch", Adw.ResponseAppearance.DESTRUCTIVE)

    def selected_kind() -> str:
        return _MATCH_KINDS[kind_row.get_selected()][1]

    def clear_results() -> None:
        child = results.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            results.remove(child)
            child = nxt

    def fill(found: list[dict]) -> bool:
        clear_results()
        status.set_text("" if found else "Keine Treffer.")
        for res in found[:12]:
            year = f" ({res['year']})" if res.get("year") else ""
            row = Adw.ActionRow(
                title=(res.get("title") or "?") + year,
                subtitle=(res.get("overview") or "")[:110],
                activatable=True,
            )
            row.connect("activated", lambda _r, r=res: apply(r))
            results.append(row)
        return False

    def do_search(*_args) -> None:
        text = query.get_text().strip()
        if not text:
            return
        kind = "tv" if selected_kind() in ("tv", "episode") else "movie"
        status.set_text("Suche …")
        _background(ctx, lambda: ctx.client.search_metadata(text, kind), fill, "Suche fehlgeschlagen")

    def apply(res: dict) -> None:
        kind = selected_kind()
        tmdb_id = int(res.get("id") or 0)
        if not tmdb_id:
            return

        def finished(_r) -> bool:
            _toast(ctx, "Zuordnung geändert")
            dialog.close()
            on_done()
            return False

        _background(
            ctx,
            lambda: ctx.client.set_item_metadata(
                int(item["id"]), kind, tmdb_id, int(season.get_value()), int(episode.get_value())
            ),
            finished,
            "Zuordnung fehlgeschlagen",
        )

    def answered(_d, response: str) -> None:
        if response != "unmatch":
            return

        def finished(_r) -> bool:
            _toast(ctx, "Zuordnung entfernt")
            on_done()
            return False

        _background(ctx, lambda: ctx.client.unmatch_item(int(item["id"])), finished, "Entfernen fehlgeschlagen")

    search_button.connect("clicked", do_search)
    query.connect("entry-activated", do_search)
    dialog.connect("response", answered)
    dialog.present()


# -- Metadaten bearbeiten -----------------------------------------------------

_AGE_RATINGS = ["", "0", "6", "12", "16", "18"]


def edit_metadata(ctx, item: dict, on_done) -> None:
    metadata = item.get("metadata") or {}
    meta_id = int(metadata.get("id") or item.get("metadataId") or 0)
    if not meta_id:
        _toast(ctx, "Keine Metadaten zum Bearbeiten – erst eine Zuordnung wählen.")
        return
    try:
        genres = ", ".join(json.loads(metadata.get("genres") or "[]"))
    except (ValueError, TypeError):
        genres = ""
    release = str(metadata.get("releaseDate") or "")[:10]
    if release.startswith("0001"):
        release = ""

    dialog = Adw.MessageDialog(transient_for=ctx.dialog_parent(), heading="Metadaten bearbeiten")
    group = Adw.PreferencesGroup()

    def entry(title: str, text: str) -> Adw.EntryRow:
        row = Adw.EntryRow(title=title)
        row.set_text(text)
        group.add(row)
        return row

    title = entry("Titel", metadata.get("title") or "")
    original = entry("Originaltitel", metadata.get("originalTitle") or "")
    year = entry("Jahr", str(metadata.get("year") or ""))
    date = entry("Erscheinungsdatum (JJJJ-MM-TT)", release)
    runtime = entry("Laufzeit (Minuten)", str(metadata.get("runtimeMin") or ""))
    rating = entry("Bewertung (0–10)", str(metadata.get("rating") or ""))
    genre_row = entry("Genres (Komma getrennt)", genres)
    age_row = Adw.ComboRow(title="Altersfreigabe (FSK)")
    age_row.set_model(Gtk.StringList.new(["keine Angabe" if a == "" else f"FSK {a}" for a in _AGE_RATINGS]))
    current_age = str(metadata.get("ageRating") or "")
    if current_age in _AGE_RATINGS:
        age_row.set_selected(_AGE_RATINGS.index(current_age))
    group.add(age_row)
    overview = Adw.EntryRow(title="Beschreibung")
    overview.set_text(metadata.get("overview") or "")
    group.add(overview)

    scroll = Gtk.ScrolledWindow(child=group, propagate_natural_height=True, max_content_height=420)
    scroll.set_size_request(460, -1)
    dialog.set_extra_child(scroll)
    dialog.add_response("cancel", "Abbrechen")
    dialog.add_response("ok", "Speichern")
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

    def number(row: Adw.EntryRow, cast, default):
        text = row.get_text().strip().replace(",", ".")
        try:
            return cast(text) if text else default
        except ValueError:
            return default

    def answered(_d, response: str) -> None:
        if response != "ok":
            return
        names = [g.strip() for g in genre_row.get_text().split(",") if g.strip()]
        fields = {
            "title": title.get_text().strip(),
            "originalTitle": original.get_text().strip(),
            "year": number(year, int, 0),
            "releaseDate": date.get_text().strip(),
            "overview": overview.get_text().strip(),
            "rating": number(rating, float, 0.0),
            "runtimeMin": number(runtime, int, 0),
            "genres": json.dumps(names, ensure_ascii=False),
            "ageRating": _AGE_RATINGS[age_row.get_selected()],
        }

        def finished(_r) -> bool:
            _toast(ctx, "Metadaten gespeichert")
            on_done()
            return False

        _background(ctx, lambda: ctx.client.update_metadata(meta_id, fields), finished, "Speichern fehlgeschlagen")

    dialog.connect("response", answered)
    dialog.present()
