"""Ansicht und Verwaltung lokaler Bibliotheken.

Zwei Seiten: eine Übersicht aller eingerichteten Ordner und Datenträger, und
je Bibliothek das Raster ihrer Videos. Abgespielt wird direkt von der Platte,
ohne Server.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ..formatting import (  # noqa: E402
    format_count,
    format_duration,
    format_size,
    resolution_bucket,
)
from ..local_library import LocalLibrary  # noqa: E402
from ..widgets.filterbar import FilterBar, FilterState  # noqa: E402
from ..widgets.grid import LocalGrid  # noqa: E402
from .player_window import PlayerWindow  # noqa: E402


class LocalLibrariesPage(Adw.NavigationPage):
    """Übersicht der eingerichteten Ordner und Datenträger."""

    def __init__(self, ctx, nav_view: Adw.NavigationView):
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Ordner oder Datenträger hinzufügen")
        add.connect("clicked", lambda *_: self._choose_folder())
        header.pack_end(add)

        self.merge_button = Gtk.Button(
            icon_name="object-group-symbolic",
            tooltip_text="Mehrere Datenträger zu einem zusammenlegen",
        )
        self.merge_button.connect("clicked", lambda *_: self._ask_merge())
        header.pack_end(self.merge_button)
        toolbar_view.add_top_bar(header)

        super().__init__(title="Eigene Datenträger", tag="local-libraries", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.toolbar_view = toolbar_view
        self._render()

    def _render(self) -> None:
        libraries = self.ctx.local.visible_libraries()
        # Zusammenlegen braucht zwei Datenträger. Der Knopf bleibt trotzdem ab
        # dem ersten sichtbar — sonst findet man die Funktion nie (genau so
        # gefragt: "wie kann ich eigene Datenträger zusammenlegen?"); der
        # Dialog erklärt dann, was fehlt.
        if hasattr(self, "merge_button"):
            self.merge_button.set_visible(bool(self.ctx.local.libraries))
        if not libraries:
            self.toolbar_view.set_content(
                Adw.StatusPage(
                    icon_name="drive-harddisk-symbolic",
                    title="Noch kein eigener Datenträger",
                    description=(
                        "Über das Plus oben rechts lässt sich ein Ordner oder eine angeschlossene "
                        "Platte hinzufügen. Die Videos darauf werden dann hier gelistet und direkt "
                        "abgespielt — ohne Server."
                    ),
                )
            )
            return

        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        listbox.add_css_class("boxed-list")
        for library in libraries:
            listbox.append(self._row(library))
        self.toolbar_view.set_content(Gtk.ScrolledWindow(vexpand=True, child=listbox))

    def _row(self, library: LocalLibrary) -> Adw.ActionRow:
        available = library.available
        total_size = sum(v.size_bytes for v in library.videos)
        parts = [f"{len(library.videos)} Videos"]
        if total_size:
            parts.append(format_size(total_size))
        if library.is_merged:
            parts.append(f"aus {len(library.merged_from)} Datenträgern")
        else:
            parts.append(library.root)
        if not available:
            # Bei externen Platten der Normalfall — der Bestand bleibt sichtbar.
            parts.insert(0, "gerade nicht angeschlossen")

        row = Adw.ActionRow(title=library.name, subtitle=" · ".join(parts), activatable=available)
        if library.is_merged:
            icon = Gtk.Image(icon_name="object-group-symbolic")
            icon.set_tooltip_text("\n".join(library.merged_from))
            row.add_prefix(icon)
        elif not available:
            row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))

        if library.is_merged:
            # Eine Sammel-Bibliothek hat keine eigene Wurzel; eingelesen wird
            # je Datenträger, also führt der Weg über das Auflösen.
            split = Gtk.Button(
                icon_name="object-ungroup-symbolic",
                valign=Gtk.Align.CENTER,
                tooltip_text="Wieder in einzelne Datenträger trennen",
            )
            split.connect("clicked", lambda *_: self._split_merge())
            row.add_suffix(split)
        else:
            rescan = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Neu einlesen")
            rescan.set_sensitive(available)
            rescan.connect("clicked", lambda _b, lib=library: self._scan(lib))
            row.add_suffix(rescan)

        rename = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Umbenennen")
        rename.connect("clicked", lambda _b, lib=library: self._ask_rename(lib))
        row.add_suffix(rename)

        dupes = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Doppelte Dateien finden")
        dupes.connect("clicked", lambda _b, lib=library: self._show_duplicates(lib))
        row.add_suffix(dupes)

        if not library.is_merged:
            remove = Gtk.Button(
                icon_name="user-trash-symbolic",
                valign=Gtk.Align.CENTER,
                tooltip_text="Aus der App entfernen",
            )
            remove.connect("clicked", lambda _b, lib=library: self._ask_remove(lib))
            row.add_suffix(remove)

        if available:
            row.connect("activated", lambda _r, lib=library: self.nav_view.push(LocalVideosPage(self.ctx, self.nav_view, lib)))
        return row

    def _ask_rename(self, library: LocalLibrary) -> None:
        """Namen ändern — der Name steht in der Übersicht UND in der
        Seitenleiste. Bei einer Sammlung ist es der Name der Gruppe."""
        dialog = Adw.MessageDialog(
            transient_for=self.ctx.window,
            heading="Umbenennen",
            body="Unter diesem Namen erscheint der Eintrag in der Seitenleiste.",
        )
        entry = Gtk.Entry(text=library.name, activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Abbrechen")
        dialog.add_response("save", "Speichern")
        dialog.set_default_response("save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

        def on_response(_d, response: str) -> None:
            name = entry.get_text().strip()
            if response != "save" or not name:
                return
            if library.is_merged:
                # Eine Sammlung hat keine eigene Zeile in der Liste; ihr Name
                # gehört zur Gruppe.
                self.ctx.local.set_merged(self.ctx.local.merged_roots, name)
            else:
                self.ctx.local.rename(library, name)
            self._render()
            self._refresh_sidebar()

        dialog.connect("response", on_response)
        dialog.present()

    def _refresh_sidebar(self) -> None:
        """Die Seitenleiste zeigt die eigenen Datenträger mit an — nach jeder
        Änderung muss sie neu gebaut werden."""
        window = self.ctx.window
        if hasattr(window, "reload_libraries"):
            window.reload_libraries()

    # -- Hinzufügen und einlesen -----------------------------------------

    def _choose_folder(self) -> None:
        dialog = Gtk.FileDialog(title="Ordner oder Datenträger auswählen")

        def picked(source, result) -> None:
            try:
                folder = source.select_folder_finish(result)
            except GLib.Error:
                return  # abgebrochen
            if folder is None or folder.get_path() is None:
                return
            library = self.ctx.local.add(folder.get_path())
            if library is None:
                _toast(self, "Dieser Ordner ist schon eingerichtet.")
                return
            self._render()
            self._refresh_sidebar()
            self._scan(library)

        dialog.select_folder(self.ctx.window, None, picked)

    def _scan(self, library: LocalLibrary) -> None:
        progress = Adw.MessageDialog(
            transient_for=self.ctx.window,
            heading=f"„{library.name}“ wird eingelesen",
            body="Dauer und Auflösung werden je Datei ermittelt.",
        )
        bar = Gtk.ProgressBar(show_text=True)
        progress.set_extra_child(bar)
        progress.add_response("hide", "Im Hintergrund weiter")
        progress.present()

        def on_progress(index: int, total: int, name: str) -> None:
            bar.set_fraction(index / total if total else 0)
            bar.set_text(f"{index}/{total} · {name[:40]}")

        def on_done(count: int) -> None:
            progress.close()
            if count < 0:
                _toast(self, f"„{library.name}“ ist gerade nicht erreichbar.")
            else:
                _toast(self, f"„{library.name}“: {count} Videos eingelesen.")
            self._render()

        self.ctx.local.scan_async(library, on_progress=on_progress, on_done=on_done)

    def _ask_remove(self, library: LocalLibrary) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self.ctx.window,
            heading="Datenträger entfernen?",
            body=(
                f"„{library.name}“ verschwindet aus der App. "
                "Die Dateien auf der Platte bleiben unangetastet — es wird nichts gelöscht."
            ),
        )
        dialog.add_response("cancel", "Abbrechen")
        dialog.add_response("remove", "Entfernen")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect(
            "response",
            lambda _d, r: (self.ctx.local.remove(library), self._render(), self._refresh_sidebar())
            if r == "remove"
            else None,
        )
        dialog.present()

    def _ask_merge(self) -> None:
        """Auswahl, welche Datenträger gemeinsam erscheinen sollen.

        Sinnvoll, wenn eine Sammlung über mehrere Platten verteilt ist: dann
        durchsucht und durchblättert man sie zusammen statt jede einzeln."""
        dialog = Adw.MessageDialog(
            transient_for=self.ctx.window,
            heading="Datenträger zusammenlegen",
            body=(
                "Die ausgewählten erscheinen als ein Eintrag, mit den Videos aus allen. "
                "Einlesen und Entfernen bleiben je Datenträger möglich — dafür die Gruppe "
                "wieder trennen."
            ),
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        if len(self.ctx.local.libraries) < 2:
            note = Gtk.Label(
                label=(
                    "Dafür braucht es mindestens zwei eingerichtete Datenträger. "
                    "Über das Plus oben rechts kommt ein weiterer dazu."
                ),
                wrap=True,
                xalign=0,
            )
            note.add_css_class("dim-label")
            box.append(note)
        name_entry = Gtk.Entry(text=self.ctx.local.merged_name, placeholder_text="Name der Sammlung")
        box.append(name_entry)

        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        checks: dict[str, Gtk.CheckButton] = {}
        for library in self.ctx.local.libraries:
            check = Gtk.CheckButton(active=library.root in self.ctx.local.merged_roots, valign=Gtk.Align.CENTER)
            row = Adw.ActionRow(title=library.name, subtitle=f"{len(library.videos)} Videos · {library.root}")
            row.add_prefix(check)
            row.set_activatable_widget(check)
            checks[library.root] = check
            listbox.append(row)
        scroll = Gtk.ScrolledWindow(child=listbox, propagate_natural_height=True, max_content_height=320)
        box.append(scroll)

        hint = Gtk.Label(
            label="Unter zwei ausgewählten Datenträgern wird die Gruppe aufgelöst.",
            wrap=True,
            xalign=0,
        )
        hint.add_css_class("dim-label")
        box.append(hint)

        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Abbrechen")
        dialog.add_response("apply", "Übernehmen")
        dialog.set_default_response("apply")
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)

        def on_response(_d, response: str) -> None:
            if response != "apply":
                return
            chosen = [root for root, check in checks.items() if check.get_active()]
            self.ctx.local.set_merged(chosen, name_entry.get_text().strip())
            self._render()
            self._refresh_sidebar()
            if len(chosen) >= 2:
                _toast(self, f"{len(chosen)} Datenträger erscheinen jetzt gemeinsam.")
            else:
                _toast(self, "Die Gruppe wurde aufgelöst.")

        dialog.connect("response", on_response)
        dialog.present()

    def _split_merge(self) -> None:
        self.ctx.local.set_merged([])
        self._refresh_sidebar()
        self._render()
        _toast(self, "Die Datenträger erscheinen wieder einzeln.")

    def _show_duplicates(self, library: LocalLibrary) -> None:
        groups = self.ctx.local.find_duplicates(library)
        dialog = Adw.MessageDialog(
            transient_for=self.ctx.window,
            heading="Doppelte Dateien",
            body=(
                f"{len(groups)} Gruppen mit gleicher Größe und Laufzeit gefunden."
                if groups
                else "Keine Datei liegt doppelt vor."
            ),
        )
        if groups:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            hint = Gtk.Label(
                label="Gleiche Größe und Laufzeit sprechen für dieselbe Datei. Neu kodierte "
                "Fassungen werden bewusst nicht erkannt, damit nichts Falsches gelöscht wird.",
                wrap=True,
                xalign=0,
            )
            hint.add_css_class("dim-label")
            box.append(hint)
            listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            listbox.add_css_class("boxed-list")
            for group in groups[:40]:
                row = Adw.ActionRow(
                    title=group[0].name,
                    subtitle=f"{len(group)}× · {format_size(group[0].size_bytes)} · {format_duration(group[0].duration_sec)}",
                )
                row.set_tooltip_text("\n".join(v.path for v in group))
                listbox.append(row)
            scroll = Gtk.ScrolledWindow(child=listbox, propagate_natural_height=True, max_content_height=360)
            box.append(scroll)
            dialog.set_extra_child(box)
        dialog.add_response("close", "Schließen")
        dialog.present()


class LocalVideosPage(Adw.NavigationPage):
    """Die Videos einer lokalen Bibliothek."""

    def __init__(self, ctx, nav_view: Adw.NavigationView, library: LocalLibrary):
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        search = Gtk.SearchEntry(placeholder_text="Im Datenträger suchen…")
        header.set_title_widget(search)
        toolbar_view.add_top_bar(header)

        shuffle = Gtk.Button(
            icon_name="media-playlist-shuffle-symbolic",
            tooltip_text="Zufällig abspielen",
        )
        header.pack_start(shuffle)

        super().__init__(title=library.name, tag=f"local-{library.root}", child=toolbar_view)
        shuffle.connect("clicked", lambda *_: self._play_random())
        self.ctx = ctx
        self.nav_view = nav_view
        self.library = library
        self.toolbar_view = toolbar_view
        self.search = search
        self.grid: LocalGrid | None = None
        self.count_label: Gtk.Label | None = None
        # Sortieren und Filtern wie in den Server-Bibliotheken — nur gerechnet
        # wird hier im Client, weil es keinen Server zu fragen gibt. Die Art
        # "local" blendet in der Leiste aus, was es hier nicht gibt (Gesehen,
        # Favoriten, Genre) und lässt Titel, Laufzeit, Größe, Auflösung und
        # Änderungsdatum stehen.
        self.filters = FilterState(sort="title")
        self.filter_bar = FilterBar("local", self.filters, on_change=self._render)
        header.pack_end(self.filter_bar)
        search.connect("search-changed", lambda *_: self._render())
        self._render()
        # Fehlende Vorschaubilder im Hintergrund nachziehen, unabhängig davon,
        # ob man an ihnen vorbeiscrollt.
        if library.available:
            ctx.local.prefetch_thumbnails_async(library)

    def _render(self) -> None:
        needle = self.search.get_text().strip().lower()
        videos = [v.as_item() for v in self.library.videos if not needle or needle in v.name.lower()]
        total = len(self.library.videos)
        if self.filters.buckets:
            videos = [
                v for v in videos if resolution_bucket(v.get("width") or 0, v.get("height") or 0) in self.filters.buckets
            ]
        videos = self._sorted(videos)
        if not videos:
            self.grid = None
            self.toolbar_view.set_content(
                Adw.StatusPage(
                    icon_name="folder-videos-symbolic",
                    title="Keine Treffer" if needle else "Keine Videos",
                    description=(
                        "Kein Dateiname passt zur Suche."
                        if needle
                        else "Dieser Datenträger enthält keine erkannten Videodateien. Über das "
                        "Neu-Einlesen in der Übersicht lässt sich das wiederholen."
                    ),
                )
            )
            return
        if self.grid is None:
            self.grid = LocalGrid(self.ctx.client, on_video=self._play)
        self.grid.set_videos(videos)
        self.shown = videos
        if self.count_label is None:
            self.count_label = Gtk.Label(xalign=0, margin_start=16, margin_top=8, margin_end=16)
            self.count_label.add_css_class("dim-label")
            self.count_label.add_css_class("caption")
            self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.content_box.append(self.count_label)
            self.content_box.append(self.grid)
        shown = len(videos)
        if shown == total:
            self.count_label.set_text(f"{format_count(total)} Videos")
        else:
            self.count_label.set_text(f"{format_count(shown)} von {format_count(total)} Videos")
        if self.toolbar_view.get_content() is not self.content_box:
            self.toolbar_view.set_content(self.content_box)

    def _sorted(self, videos: list[dict]) -> list[dict]:
        """Sortiert im Client. Die Schlüssel entsprechen denen des Servers,
        damit dieselbe Leiste beide Fälle bedienen kann."""
        keys = {
            "title": lambda v: (v.get("title") or "").lower(),
            "duration": lambda v: v.get("durationSec") or 0,
            "size": lambda v: v.get("sizeBytes") or 0,
            "resolution": lambda v: max(v.get("height") or 0, int((v.get("width") or 0) * 9 / 16)),
            "modified": lambda v: v.get("modified") or 0,
        }
        key = keys.get(self.filters.sort, keys["title"])
        return sorted(videos, key=key, reverse=not self.filters.effective_ascending())

    def _play_random(self) -> None:
        """Zufälliges Video von diesem Datenträger — und im Player geht es mit
        ⏭ zum nächsten Zufallsvideo weiter, genau wie bei den
        Server-Bibliotheken. Die Suche wirkt mit: was gefiltert ist, wird auch
        nicht gezogen."""
        import random

        pool = list(getattr(self, "shown", []))
        if not pool:
            _toast(self, "Keine Videos zum Ziehen.")
            return
        self._play(random.choice(pool), draw=lambda: random.choice(pool))

    def _play(self, video: dict, draw=None) -> None:
        """Spielt direkt von der Platte.

        Kein Umweg über den Server und keine Formatanpassung: GStreamer spielt
        MKV, HEVC, DTS und AC3 von sich aus. Die Mac-App muss dafür umwandeln,
        weil macOS diese Formate nicht kann — hier entfällt das."""
        path = video.get("path")
        if not path:
            return
        window = PlayerWindow(
            self.ctx.application,
            self.ctx.client,
            video,
            local_path=path,
            window_title=video.get("title") or "",
            random_fetch=draw,
        )
        window.set_transient_for(self.ctx.window)
        window.present()


def _toast(page: Adw.NavigationPage, message: str) -> bool:
    root = page.get_root()
    if hasattr(root, "show_toast"):
        root.show_toast(message)
    return False
