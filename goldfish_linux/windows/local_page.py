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

from ..formatting import format_duration, format_size  # noqa: E402
from ..local_library import LocalLibrary  # noqa: E402
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
        toolbar_view.add_top_bar(header)

        super().__init__(title="Eigene Datenträger", tag="local-libraries", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.toolbar_view = toolbar_view
        self._render()

    def _render(self) -> None:
        libraries = self.ctx.local.libraries
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
        parts.append(library.root)
        if not available:
            # Bei externen Platten der Normalfall — der Bestand bleibt sichtbar.
            parts.insert(0, "gerade nicht angeschlossen")

        row = Adw.ActionRow(title=library.name, subtitle=" · ".join(parts), activatable=available)
        if not available:
            row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))

        rescan = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Neu einlesen")
        rescan.set_sensitive(available)
        rescan.connect("clicked", lambda _b, lib=library: self._scan(lib))
        row.add_suffix(rescan)

        dupes = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Doppelte Dateien finden")
        dupes.connect("clicked", lambda _b, lib=library: self._show_duplicates(lib))
        row.add_suffix(dupes)

        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Aus der App entfernen")
        remove.connect("clicked", lambda _b, lib=library: self._ask_remove(lib))
        row.add_suffix(remove)

        if available:
            row.connect("activated", lambda _r, lib=library: self.nav_view.push(LocalVideosPage(self.ctx, self.nav_view, lib)))
        return row

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
        dialog.connect("response", lambda _d, r: (self.ctx.local.remove(library), self._render()) if r == "remove" else None)
        dialog.present()

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

        super().__init__(title=library.name, tag=f"local-{library.root}", child=toolbar_view)
        self.ctx = ctx
        self.nav_view = nav_view
        self.library = library
        self.toolbar_view = toolbar_view
        self.search = search
        self.grid: LocalGrid | None = None
        search.connect("search-changed", lambda *_: self._render())
        self._render()

    def _render(self) -> None:
        needle = self.search.get_text().strip().lower()
        videos = [v.as_item() for v in self.library.videos if not needle or needle in v.name.lower()]
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
        if self.toolbar_view.get_content() is not self.grid:
            self.toolbar_view.set_content(self.grid)

    def _play(self, video: dict) -> None:
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
        )
        window.set_transient_for(self.ctx.window)
        window.present()


def _toast(page: Adw.NavigationPage, message: str) -> bool:
    root = page.get_root()
    if hasattr(root, "show_toast"):
        root.show_toast(message)
    return False
