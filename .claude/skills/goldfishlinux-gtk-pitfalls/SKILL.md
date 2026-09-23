---
name: goldfishlinux-gtk-pitfalls
description: "Use when building or fixing GoldfishLinux views: card width measurement, GridView vs FlowBox, MediaFile teardown, single player window, folder navigation, poster decoding."
metadata:
  project: GoldfishLinux (boernie77/goldfish-linux)
  source: "CLAUDE.md-Aufteilung 2026-09-20"
---

# goldfishlinux-gtk-pitfalls

Aus der frueheren Sammel-CLAUDE.md des GoldfishLinux-Repos ausgelagerter Themenbereich (Zeichen: 4887, Sektionen: 1). Volltext des Originals: Skill `goldfishlinux-full-archive`.

## Harte Regeln (zuerst lesen)

- Kacheltitel sind einzeilig mit Auslassung, der volle Titel steht im Tooltip - ein umbrechendes Label fordert die volle Textbreite an (nachgemessen 297 statt 168 px).
- Lange Listen brauchen `Gtk.GridView` statt `Gtk.FlowBox` (2717 Album-Kacheln = 4,65 s blockierter Hauptablauf).
- Ein abgeloestes `Gtk.MediaFile` wird abgebaut, nicht nur angehalten (`set_playing(False)` -> Bild abhaengen -> `clear()`).
- Es darf nur EIN Wiedergabefenster geben - ein zweites macht Dialoge unsichtbar und laesst die App haengen.
- Bei serverseitiger Umwandlung geht Springen nicht mit `media.seek()`; `/api/playback/{id}` ist teuer und gehoert nie in Schleifen.

---

## Fallstricke, die in Etappe 01–08 aufgetreten sind

- **Breitenmessung von Kacheln:** ein `Gtk.Label` mit Umbruch und `lines=2`
  fordert bei vorgegebener Höhe die volle Textbreite an, damit der Text
  ungekürzt passt — nachgemessen 297 statt 168 Pixel bei einem langen
  Filmtitel. Die Kachel bekommt entsprechend mehr Platz zugeteilt, zeichnet
  nur ihre Sollbreite und hinterlässt eine sichtbare Lücke zum Nachbarn.
  Weder `max-width-chars`, `width-request` noch `halign` ändern daran etwas,
  und eine überschriebene `do_measure` greift bei einer `Gtk.Box`-Unterklasse
  in PyGObject NICHT (mit einer Zählung geprüft: null Aufrufe). **Deshalb sind
  Kacheltitel einzeilig mit Auslassung, der volle Titel steht im Tooltip.**
- **Lange Listen brauchen `Gtk.GridView`, nicht `Gtk.FlowBox`.** Eine FlowBox
  erzeugt für jedes Element ein Widget: 2717 Album-Kacheln kosteten **4,65
  Sekunden blockierten Hauptablauf** (nachgemessen). Sichtbar wurde das an
  einer laufenden Musikwiedergabe, deren Position scheinbar stillstand,
  während die Engine isoliert einwandfrei zählte. FlowBox ist nur für kurze
  Listen richtig (Staffeln einer Serie, Teile einer Sammlung).
- **`Gtk.Picture` meldet die Pixelbreite des geladenen Bildes als natürliche
  Breite.** Ein zu groß dekodiertes Bild macht die Kachel breiter als gewollt
  — `load_poster_async` bekommt deshalb die Zielbreite mitgegeben.
- **⚠ Ein abgelöstes `Gtk.MediaFile` muss ABGEBAUT werden, nicht nur
  angehalten.** `pause()` hält die Wiedergabe an und lässt die GStreamer-Kette
  vollständig stehen: nachgemessen wuchsen bei fünf Videos hintereinander die
  Fäden von 33 auf 89 (vierzehn je Video) und der Speicher von 221 auf 327 MB.
  Beim Benutzer standen nach acht Wechseln im Zufallsmodus 135 Fäden, 1,2 GB
  und acht gleichzeitig "laufende" Wiedergaben. Richtig ist
  `set_playing(False)`, das Bild abhängen (solange ein `Gtk.Picture` das
  Medium als Paintable hält, wird es nicht abgebaut) und `clear()` —
  danach bleiben Fäden und Speicher stehen.
- **⚠ Es darf nur EIN Wiedergabefenster geben** (`open_player`/`close_player`
  in `windows/player_window.py`). Zwei Fenster übereinander waren eine Falle:
  ein modaler Dialog des Hauptfensters ("Weiterschauen?") landete unsichtbar
  HINTER dem noch offenen Playerfenster und wartete dort auf eine Antwort —
  für den Benutzer hing die ganze App, während das alte Video weiterlief.
  Diagnose ohne Werkzeuge: `wmctrl -lp` zeigte drei Fenster desselben
  Prozesses ("Goldfish", "Weiterschauen?", "<Filmtitel>"), und
  `/proc/<pid>/task/<pid>/wchan` sagte, dass der Hauptablauf normal in
  `poll_schedule_timeout` wartet — also kein Deadlock, sondern ein
  unsichtbarer Dialog. Dialoge hängen zusätzlich am gerade AKTIVEN Fenster
  (`AppContext.dialog_parent()`).
- **Abgelöste `Gtk.MediaFile` unbedingt abklemmen.** Beim Springen im
  Umwandlungsmodus entsteht ein neues Medium; das alte meldet danach den
  Fehler seiner beendeten Umwandlung, und ohne Trennung legt sich
  "Wiedergabe fehlgeschlagen" über den laufenden Film. Zusätzlich prüft jeder
  Handler, ob er vom aktuellen Medium kommt.
- **Springen bei serverseitiger Umwandlung** geht nicht mit `media.seek()`:
  ein wachsender HLS-Stream kennt weder die Gesamtdauer (`get_duration()`
  bleibt 0) noch Positionen jenseits des Erzeugten. Die Dauer kommt aus
  `item["durationSec"]`, ein Sprung startet eine neue Umwandlung ab der
  Zielsekunde (`start=`, `fresh=1`, plus ein pro Fenster stabiler `_t`-Token,
  damit die periodischen Playlist-Abrufe die Umwandlung nicht dauernd
  abbrechen). `_virtual_offset` rechnet die Position um.
- **`Gtk.Spinner` erst nach dem Einhängen starten**, sonst fehlt die
  Frame-Clock und es erscheint `gdk_frame_clock_get_frame_time: assertion
  'GDK_IS_FRAME_CLOCK (frame_clock)' failed`.
- **Gespeicherte Anmeldung nur verwerfen, wenn der Server sie ablehnt.** Ein
  Zeitüberschreiten oder ein 502 vom Reverse-Proxy sagen nichts über ihre
  Gültigkeit. Vorher wurde bei jedem Fehler der Token gelöscht — bei einem
  kurz überlasteten Server verlor man dadurch die Anmeldung (real passiert).
- **Ordnernavigation hat drei Fälle** (wie im Browser, `grid.js`): in der
  Bibliothekswurzel Ordnerkacheln plus die Items der Wurzel (`folder="/"`),
  in einem Ordner mit gesetztem `folder_nav.drilldown` seine direkten
  Unterordner plus die unmittelbar darin liegenden Dateien (serverseitig
  gibt es kein "nur direkte Kinder", also clientseitig nachfiltern), und im
  Regelfall gar keine Ordnerkacheln, sondern die Dateien rekursiv flach.
  Fehlt der dritte Fall, erscheinen dieselben Dateien doppelt — derselbe
  Fehler wie einst in den Apple-Apps.
- **`/api/playback/{id}` ist teuer** (ffprobe serverseitig). Nie in Schleifen
  über viele Items aufrufen; eine Suche über 40 Titel hat den Server in einen
  Timeout gezogen. Für Musik wird er gar nicht gebraucht (siehe unten).
- **`weakref.ref(obj.methode)` ist sofort tot.** `AppContext.add_item_state_listener`
  hält nur eine SCHWACHE Referenz; ein inline übergebener Ausdruck wie
  `self.ctx.add_item_state_listener(self._on_item_state_changed)` erzeugt ein
  Methoden-Objekt, das sofort wieder freigegeben wird — der Rückruf lief NIE
  (nachgestellt: `weakref.ref(a.f)()` → `None`). Die gebundene Methode deshalb
  als Attribut festhalten (`self._state_listener = self._on_item_state_changed`,
  dann anmelden) — so machten es `browse_page` und `detail_page` bis zum
  User-Report 2026-09-23 gar nicht, die „Kachel zieht nach"-Leitung war damit
  wirkungslos.
- **Gesehen-Haken der Kachel live nachziehen.** Der Player markiert eine Folge ab
  `_WATCHED_AT` (90 %) als gesehen; ohne Rückmeldung blieb die Kachel grau, bis man
  die Ansicht verließ und neu betrat (User-Report 2026-09-23, im Browser richtig über
  `player.js` → `markWatchedNow` → `silentlyRefreshItem`). `player_window` bekommt
  dafür jetzt `ctx` und ruft nach `set_watched` `ctx.notify_item_state(item_id,
  watched=True)` — die Position-Handler laufen im GTK-Hauptablauf, also direkt
  aufrufbar (kein `idle_add`). Angemeldet sind: `browse_page` (Raster),
  `seasons_page.SeasonEpisodesPage` (Folgen-Kacheln, über
  `SimpleCard.set_corner_mark`) und `detail_page` (Umschaltknopf, blockiert beim
  Nachziehen den eigenen `toggled`-Handler, sonst schickt er den Zustand doppelt zum
  Server). **Nicht** nachziehend: die Staffelübersicht („x gesehen"-Zähler) — die
  braucht weiterhin einen Neuaufbau.

