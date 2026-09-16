# GoldfishLinux — natives Linux-Desktop-Client für Goldfish

Nativer Linux-Desktop-Client für den Goldfish-Server (separates Repo
`github.com/boernie77/goldfish`, lokal `~/Projekte/Videoplayer/`). Python 3
+ PyGObject (GTK4 + libadwaita), kein Electron/Tauri. Eigenes Git-Repo seit
2026-09-12: `github.com/boernie77/goldfish-linux` (öffentlich). Als `.deb`
für Debian 12+/Ubuntu 24.04+/Mint 22+ paketiert (ältere Systeme mit
libadwaita < 1.4 werden bewusst NICHT unterstützt).

**Bei jeder Server-API-Änderung prüfen:** `goldfish_linux/api.py`. Der Client
kennt inzwischen rund 80 Endpunkte — neben Anmeldung, Bibliotheken, Ordnern,
Items und Wiedergabe auch Staffeln, Besetzung, Trailer, Personen,
Fortsetz-Position, Alben, Sammlungen, Playlists, Startseiten- und
Reiter-Einstellungen, Vorschaubilder, Untertitel (eingebettet, Whisper, OCR),
Gesehen-Sync und optimierte Downloads. Für die Wiedergabe ohne Cookie-Speicher
wird weiterhin der `?session=<token>`-Fallback genutzt (ursprünglich für
Cast-Empfänger gedacht).

**Zwei Eigenheiten des Servers, die im Client dokumentiert sind:**
`playback.Profile` trägt im Go-Code keine JSON-Tags, die Qualitätsstufen
kommen deshalb GROSS geschrieben an (`ID`, `Label`, `MaxHeight`, …) — als
einziges Objekt der ganzen API; `api.PlaybackProfile` normalisiert das.
Und `/api/subtitle/{id}/{idx}.vtt` extrahiert die Spur beim ERSTEN Abruf per
ffmpeg und blockiert so lange, weshalb dort ein eigenes Zeitlimit von 120
Sekunden gilt.

**⚠ Memory-Hinweis:** Ältere, chronologische Bugfix-Historie (bis v0.1.6)
wurde in Sessions erarbeitet, die im Server-Repo (`~/Projekte/Videoplayer/`)
liefen, und liegt dort als Memory `project_feature_goldfish_linux` — pro
Arbeitsverzeichnis gespeichert, eine Session, die nur in diesem Repo
arbeitet, sieht sie NICHT automatisch. Die durable Architektur-Fakten daraus
sind unten dupliziert. **Für den AKTUELLEN Stand ist diese Memory NICHT
mehr verlässlich** — der Code hier ist inzwischen deutlich weiter
(`git log`/`__version__` in `goldfish_linux/__init__.py` prüfen; Stand bei
Anlage dieser Datei bereits v0.1.15 mit Musik/Home/Sammlungen/lokalen
Bibliotheken, also weit über die in der Memory beschriebene v0.1.6 hinaus).

## 📍 Aktueller Stand

**Immer per `git log --oneline -10` und `goldfish_linux/__init__.py
__version__` prüfen, nicht diese Datei** — der Stand ändert sich schneller,
als CLAUDE.md gepflegt werden kann. Die Feature-Historie im Einzelnen steht
in `debian/changelog`.

Was unten folgt, ist bewusst nur das Dauerhafte: was in welcher Etappe
entstanden ist (als Orientierung, wo man suchen muss), die Fallstricke, und
die Stellen, an denen diese App absichtlich von der Mac-App abweicht.

### Wo was entstanden ist

| Version | Inhalt |
|---|---|
| 0.1.7 | Startfehler behoben (siehe unten) |
| 0.1.8 | Kachelraster mit TMDB-Postern statt Textliste |
| 0.1.9 | Sortierung, Filter (Gesehen, Favoriten, Auflösung, Genre), Suche |
| 0.1.10 | Detailansicht: Besetzung, FSK, Trailer, Ton-/Untertitel-/Qualitätswahl, Versionen |
| 0.1.11 | Player: eigene Steuerleiste, Untertitel im Bild, Weiterschauen, Vorschaubilder, Transcode-Springen |
| 0.1.12 | Startseite, Staffelansicht, Sammlungen, Playlists |
| 0.1.13 | Kachelbreiten-Fix (siehe unten) |
| 0.1.14 | Musik: Alben, Titelliste, Abspielleiste, Warteschlange |
| 0.1.15 | Eigene Datenträger als lokale Bibliotheken |
| 0.1.16 | Einstellungen, SSO, Buchstabenleiste, Zufall, kleinere Downloads |
| 0.1.17 | Mehrere Datenträger zu einem Eintrag zusammenlegen |
| 0.1.18 | Sechs Meldungen des Benutzers: Filme flach mit Postern, Tempo, Startseiten-Streifen, Reiterleisten-Schalter, Sammlungs-Abzeichen, Playlist-Zufall |
| 0.1.19 | Kachelbreiten, einheitliche Kachelform, Anzahl-Zeile, Player-Sprungknöpfe, echte Zufallswiedergabe, Musik-Warteschlange, eigene Datenträger in der Seitenleiste, eigene Vorschaubilder |
| 0.1.20 | Musik-Titelsuche, Sortierung/Filter für eigene Datenträger, Vorschaubilder im Vorgriff |
| 0.1.21 | Scheinbares Einfrieren (Dialog hinter dem Player), Medien-Abbau, Auflösungsanzeige, Symbolknöpfe |
| 0.1.32 | Musik-Spalten (Zuletzt gehört/Wiedergaben/Hinzugefügt) + Spalten-Menü; Startseiten-Serien-/Kanalname-Race-Fix (siehe unten) |
| 0.1.33 | Musikseite als Tabelle: verschieb- und breitenverstellbare Spalten, drei beschriftete Ansichtsschalter, eigene Knöpfe für Spalten und Filter (siehe unten) |
| 0.1.34 | Musik: Zufallswiedergabe in jeder Ansicht (ohne Hörbücher), Cover in den Suchtreffern, umschaltbare Playlist-Ansicht |
| 0.1.35 | Ansichts-Schalter der Musikseite als Symbole statt Beschriftungen |
| 0.1.36 | Rahmen am Zufallsknopf, Spaltenbreite ohne Seiteneffekt, auffindbare Warteschlange |
| 0.1.37 | Warteschlangen-Fenster öffnet wieder (Wiederverwendung statt einer Zeile je Titel), Zufall zieht 200 Titel |
| 0.1.38 | Echter Fix für unscharfe Kacheln ohne eigenes Cover (0.1.24 griff in der echten Bibliotheksansicht nie, siehe „Unscharfe Vorschaubilder" unten) |
| 0.1.47 | Vollbild-Button im Player repariert — Header/Leiste/Icon reagierten synchron auf `fullscreen()`/`unfullscreen()`, der WM wendet das aber asynchron an. Fix über `notify::fullscreened`-Signal (User-Report 2026-09-16, **ungetestet auf echtem GTK** — diese Session lief auf macOS ohne GTK-Runtime, nur `py_compile`/`pyflakes` sauber) |

Noch offen (Stand 0.1.17): die vollständige TMDB-Filmografie auf der
Personenseite (dort erscheinen derzeit nur die vorhandenen Titel) und die
SSO-Anmeldung, die nur bis zum Laden der Authentik-Seite gegengeprüft ist,
nicht bis zum Ende durchgespielt.

### ⚠ Korrektur zur früheren Diagnose des Startfehlers

Die Vermutung aus v0.1.6 — `Adw.ToolbarView.add_bottom_bar` mit einem
einfachen `Gtk.Label` sei der Auslöser — **war falsch**. Sie entstand auf
macOS ohne Möglichkeit, GTK4 auszuführen. Der tatsächliche Fehler saß in
`app.py`, `do_activate()`: im Zweig "gespeicherte Sitzung vorhanden" wurde
nur ein Hintergrund-Thread gestartet und **kein Fenster erzeugt**.
`Gtk.Application` beendet sich aber, sobald seine Fensterliste leer ist —
die App quittete mit Exit-Code 0, bevor der Thread das Hauptfenster zeigen
konnte. Deshalb trat es NUR mit gespeicherter Anmeldung auf; ein leeres
Konfigurationsverzeichnis nahm den Anmelde-Zweig und startete normal. Fix
in v0.1.7: `hold()` über die Prüfung, `release()` erst nach dem `present()`.

**Lehre für künftige Arbeit an diesem Repo:** Diagnosen nicht auf einem
System stellen, das die Oberfläche nicht ausführen kann. Der Rechner unter
`~/Projekte/GoldfishLinux` kann es — dort prüfen.

## Tech-Stack & Architektur-Entscheidungen

- **Player: `Gtk.MediaFile` als Paintable in einem `Gtk.Picture`, mit eigener
  Steuerleiste** (seit v0.1.11). `Gtk.Video` war die erste Wahl, ist aber eine
  Sackgasse, sobald mehr als Play/Pause gebraucht wird: seine Steuerleiste ist
  fest eingebaut und von außen nicht erreichbar — kein Untertitel über dem
  Bild, keine Vorschaubilder am Fortschrittsbalken, kein eigener Balken. Vor
  dem Umbau am echten HLS-Stream geprüft, dass Position, Dauer, Springen und
  Lautstärke über `Gtk.MediaFile` erreichbar sind.
- Auth für den Player: kein Cookie-Jar in GStreamer möglich → nutzt den
  `?session=<token>`-Query-Fallback, den der Server ursprünglich für
  Cast-Receiver (Chromecast/FireTV) bereitstellt
  (`resolveSessionToken` in `internal/api/auth.go` im Server-Repo). Für
  normale API-Calls läuft alles über `requests.Session` mit echtem Cookie.
- **Harte Mindestanforderung: libadwaita ≥ 1.4** (für
  `Adw.NavigationSplitView`/`Adw.NavigationView`/`Adw.ToolbarView`) —
  Ubuntu 22.04 LTS und Linux Mint 21.x werden NICHT unterstützt (libadwaita
  dort nur 1.0). Zielsysteme: Debian 12+, Ubuntu 24.04+, Mint 22+.
- `Adw.NavigationPage.child` ist **construct-only** (kein `set_child()`
  danach) — alle Page-Klassen bauen ihr Inhalts-Widget VOR
  `super().__init__()` und übergeben es als `child=`-Kwarg.
- `Adw.Spinner` bewusst NICHT genutzt (braucht libadwaita ≥ 1.6, neuer als
  die Mindestanforderung) — überall `Gtk.Spinner()` + `.start()`.
- `Adw.ToolbarView.add_top_bar`/`add_bottom_bar` sind laut Doku für
  Bar-artige Widgets gedacht (AdwHeaderBar/GtkActionBar/AdwTabBar); ein
  einfaches `Gtk.Label` dort ist nicht ausdrücklich belegt. Die Versionsanzeige
  liegt deshalb als normaler `Gtk.Box`-Sibling darunter. **Der frühere Verdacht,
  das habe den Startfehler von v0.1.5 verursacht, war falsch** — siehe die
  Korrektur oben.
- **GTK-Widget-Parenting-Regel:** beim händischen Widget-Aufbau IMMER erst
  ganz unten (innerster Container) anfangen und von innen nach außen genau
  EINMAL pro Widget parenten — nie ein Widget "vorläufig" an einen
  Container hängen und später umhängen (Root Cause von v0.1.4: dieselbe
  `box` wurde per `scrolled.set_child()` UND `clamp.set_child()` geparentet,
  GTK verweigerte das zweite `set_child()` lautlos-kritisch).
- **API-Konvention:** `folder=""` bedeutet in `ListItems` NICHT "nur
  Root-Ebene", sondern "kein Filter" (liefert ALLE Items rekursiv). Der
  Sonderwert für "nur Root-Ebene" ist `"/"`. Bei JEDEM künftigen Client, der
  gegen `/api/items?folder=` baut: IMMER `"/"` für "nur Root-Ebene"
  verwenden, NIE `""`.
- **Fehlerbehandlung in Hintergrund-Threads:** NIE nur den erwarteten
  Fehlertyp (`GoldfishAPIError`) fangen, IMMER zusätzlich ein breites
  `except Exception` mit sichtbarer UI-Fehlermeldung — sonst wirkt jeder
  unerwartete Fehler wie "nichts passiert" (Spinner hängt für immer).
- **DynDNS/IPv6-Fallstrick:** bei "Connection reset" (nicht Timeout!) gegen
  einen selbstgehosteten Server mit DynDNS: `dig AAAA <domain>` prüfen — ein
  veraltetes IPv6-Präfix kann den TCP-Handshake zu einem falschen Host
  gelingen lassen, `requests`/urllib3 fällt NICHT automatisch auf IPv4
  zurück (kein Happy-Eyeballs wie im Browser). Fix bei diesem Symptom:
  `urllib3.util.connection.allowed_gai_family` auf `socket.AF_INET` patchen.
- **Icon:** NIE selbst zeichnen — `internal/webassets/web/favicon.svg` aus
  dem Server-Repo wiederverwenden (Twemoji-Tropenfisch 🐠, CC-BY 4.0),
  identisches Branding über alle Plattformen.
- **Hardware-Videodekodierung (`gstreamer1.0-vaapi`, seit 0.1.26):** ohne
  dieses Paket dekodiert GStreamer jedes Video rein per Software (`avdec_*`
  aus `gstreamer1.0-libav`), selbst wenn eine Intel-/AMD-iGPU per VAAPI
  eigentlich könnte — auf einem schwächeren/älteren Prozessor sichtbar als
  Ruckeln bei höherer Auflösung/HEVC. Kein Code-Zweig nötig: sowohl
  `Gtk.MediaFile`s interner `playbin` (Server-Streams) als auch das
  `uridecodebin` in `local_library.py` (Eigene Datenträger) wählen den
  Decoder automatisch nach Rang — ist `gstreamer1.0-vaapi` installiert,
  wird VAAPI-Dekodierung automatisch bevorzugt, ganz ohne explizite
  Auswahl im Code.

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

## Fallstricke aus 0.1.18 (Tempo und GTK-Lebensdauern)

- **Film-Bibliotheken sind IMMER flach.** Ein Film liegt in seinem eigenen
  Release-Ordner; die Filme-Bibliothek dieses Servers bringt 2808 solcher
  Ordner mit, jeder mit einer Datei und ohne eigene TMDB-Zuordnung. Wer dort
  Ordnerkacheln zeigt, zeigt 2808 Kacheln mit Release-Dateinamen und ohne
  Poster — genau der Fehler, den der Benutzer als "keine Filmcover, Titel wie
  der Dateiname" gemeldet hat. Der Browser macht es ebenso (`grid.js`:
  `flatView = state.flatView || lib.kind === "movies" || isShuffle`). Serien
  behalten ihre Ordnerkacheln.
- **`Gtk.GridView` legt rund 385 Kacheln an, egal wie groß das Fenster ist.**
  Nachgemessen bei 626 px Höhe und 301 px hohen Zeilen (zwölf sichtbare
  Kacheln) — und zwar auch mit einem nackten GridView ohne unseren Code, also
  keine Eigenheit dieser App. Wer beim Belegen einer Kachel etwas anstößt
  (Bild laden!), tut das also 385-mal pro Seitenaufruf. Poster werden deshalb
  erst geladen, wenn die Kachel wirklich in Sicht ist: 12 statt 385 Anfragen.
- **⚠ NIE die Elternkette einer Kachel ablaufen** (`picture.get_parent()` in
  einer Schleife). Oberhalb einer Rasterkachel liegen die internen Widgets des
  GridView; schon sie aus Python anzufassen erzeugt Hüllobjekte, die GTKs
  eigenem Aufräumen in die Quere kommen. Folge beim Scrollen: erst
  `gtk_widget_insert_after: assertion 'GTK_IS_WIDGET (widget)' failed`, dann
  `Gtk:ERROR ... gtk_list_factory_widget_teardown_factory: assertion failed:
  (priv->object == NULL)` und ein Speicherauszug. Reproduzierbar in drei von
  drei Läufen; mit derselben Prüfung ohne Elternlauf still. Deshalb gibt das
  Raster seine Bildlaufleiste ausdrücklich an die Kachel weiter
  (`CardWidget(scroller=...)`), und gerechnet wird nur mit
  `compute_bounds(scroller)`.
- **Ein Modellwechsel in einem SICHTBAREN Raster, das dabei stark schrumpft,
  ist nicht still.** 3170 Kacheln auf einen Suchtreffer zu bringen, während
  das Raster im Fenster hängt, erzeugt acht Meldungen der Art
  `gtk_widget_measure: assertion 'GTK_IS_WIDGET (widget)' failed` (auch mit
  komplett neuem Modell statt `splice`, auch ohne jedes Poster-Laden). Hängt
  das Raster während des Wechsels nicht im Fenster, bleibt es still — deshalb
  tritt bei JEDEM Laden weiter der Ladekreis an seine Stelle, obwohl das
  stehende Raster ruhiger aussähe.
- **Dekodieren gehört in den Hintergrund.** Bis 0.1.17 wurde ein Bild aus dem
  Dateizwischenspeicher im Hauptablauf dekodiert ("der Thread-Umweg ist teurer"
  — stimmt pro Bild, aber nicht bei 385 davon). Jetzt: Texturen im Speicher
  halten (begrenzt), dekodieren im Faden, im Hauptablauf nur noch die fertige
  Textur setzen.
- **Die Reihenzeilen von `/api/nav/preferences` und `/api/home/preferences`
  heißen `libraryId`, nicht `id`.** Beide Endpunkte liefern eine eigene
  Zeilenform, nicht das übliche Bibliotheks-Objekt. Mit `lib["id"]` stirbt der
  Signal-Handler an einem KeyError, GTK schreibt das nur auf die Konsole — für
  den Benutzer wirkt der Schalter wirkungslos ("ein An- und Abwählen bewirkt
  nichts"). Außerdem MUSS die Seitenleiste diese Einstellung selbst lesen und
  nach einer Änderung neu aufgebaut werden.
- **⚠ `MainWindow.ctx.library_kinds` ist beim allerersten Öffnen der
  Startseite oft noch leer (Bug, gefixt 0.1.32, User-Report: "bei Linux
  fehlt auf der Startseite der Serienname und der Kanalname bei YouTube").**
  `MainWindow.__init__` pusht `HomePage` SOFORT, das Befüllen von
  `ctx.library_kinds` (eigener Hintergrund-Ladevorgang über
  `_load_libraries()`) läuft daneben her und braucht zwei sequentielle
  Netzwerk-Anfragen (`/api/libraries` + `/api/nav/preferences`) — die
  Home-Antwort selbst (nur EIN Aufruf) kommt praktisch immer zuerst zurück.
  `home_page.py`s `_strip()` fragte für die beiden bibliotheksübergreifenden
  Streifen ("Fortsetzen"/"Als nächstes", die einzigen zwei ohne explizit
  mitgegebenes `kind`) genau dieses noch leere `ctx.library_kinds` ab — die
  Bibliotheksart fiel dadurch praktisch immer auf den Fallback "movies"
  zurück, `CardWidget.bind()`s Serien-/Kanalname-Zeile (nur bei
  `kind in ("tv", "private")`) blieb unsichtbar. Fix: `_strip()` nutzt
  jetzt `self._library_by_id`, das `_apply()` bereits synchron aus der
  EIGENEN Home-Antwort baut (`section["library"]` je Sektion) — dieselbe
  Datenquelle, kein zweiter, race-anfälliger Ladevorgang.
- **Vollständigkeit einer Sammlung** ist `movieCount >= partCount -
  hiddenCount - unreleasedCount` (beide Abzüge schickt der Server nur, wenn
  sie nicht 0 sind). Ohne die Abzüge gilt jede Reihe mit angekündigter
  Fortsetzung dauerhaft als unvollständig.
- **⚠ `Gtk.Picture` meldet bei vorgegebener Höhe eine seitenverhältnis-
  abhängige NATURBREITE.** Ein 16:9-Bild in einem 168×252 angeforderten
  Picture verlangt bei `for_size=301` eine Breite von 536 Pixeln
  (nachgemessen; bei `for_size=-1` immerhin die Texturbreite). Ist im Fenster
  Platz übrig, verteilt die umgebende Box diesen Wunsch — die Kachel zeichnet
  weiter 168 und daneben klafft eine Lücke. Genau das war als "in allen
  Bibliotheken zieht sich der Abstand auf, wenn ich das Fenster größer mache"
  gemeldet. **Weder `set_size_request` noch `halign`, `hexpand=False`,
  `Adw.Clamp` (216) oder `Gtk.AspectFrame` (536) helfen dagegen.** Was hilft:
  die Sollgröße gibt eine leere Box als HAUPTKIND eines `Gtk.Overlay` vor, das
  Bild liegt als Overlay-Kind darüber und ist von der Messung ausgenommen
  (`set_measure_overlay(picture, False)`) — Naturbreite dann 168 bei jeder
  Höhe. Siehe `_image_frame` in `widgets/card.py`; JEDE neue Bildkachel muss
  darüber gebaut werden.
- **`Gtk.GridView` teilt seine Breite auf genau so viele Spalten, wie er
  anlegt.** Mit `max_columns=12` sind das in einem 2600 Pixel breiten Fenster
  216 Pixel je Spalte, also 48 Pixel Luft je 168er-Kachel. Deshalb steht die
  Grenze auf 36: dann bestimmt die Kachelbreite die Spaltenzahl.
- **Die Startseite hat zwei übergreifende Streifen, keine pro Bibliothek.**
  "Fortsetzen" und "Als nächstes" führen die Titel ALLER Bibliotheken in je
  einer Zeile (sortiert nach letztem Abspielen bzw. Hinzufügen, je 24), erst
  darunter kommt "Zuletzt hinzugefügt" pro Bibliothek. Der Server liefert
  beides pro Bibliothek getrennt; das Zusammenführen ist Client-Aufgabe
  (`views.js renderHomeView` macht es genauso). In den übergreifenden
  Streifen bestimmt jede Kachel ihre Art selbst über `libraryId` — dort liegen
  Filme, Folgen und Privatvideos nebeneinander.

- **Musiklisten sind ein `Gtk.ColumnView`, kein Stapel aus Zeilen-Widgets**
  (`widgets/column_list.py`, seit 0.1.33). Entscheidend sind drei Dinge, die
  eine selbstgebaute Zeilenliste nicht mitbringt: Recycling (7317 Titel als
  einzelne Zeilen-Widgets blockieren den Hauptablauf sekundenlang, dieselbe
  Messung wie beim Album-Raster), ziehbare Spaltenbreiten und per Kopf
  verschiebbare Spalten. Was der ColumnView NICHT mitbringt und diese Klasse
  ergänzt, ist das Merken: Reihenfolge, Breiten und Auswahl liegen unter
  einem Kontextnamen ("albums"/"allTracks"/"albumTracks") in den
  Ansichtseinstellungen, genau wie `MUSIC_LIST_CONTEXTS` im Browser.
  Fallstricke dabei:
  - **Beim Aufbau darf nicht gespeichert werden.** Das Anhängen der Spalten
    löst dasselbe `items-changed` aus wie ein Verschieben durch den Benutzer
    und würde die gerade gelesene Reihenfolge sofort überschreiben
    (`_building`-Schalter).
  - **`notify::fixed-width` feuert beim Ziehen für jedes Pixel** — Speichern
    erst nach 400 ms Ruhe, sonst schreibt die App die Einstellungsdatei
    hunderte Male je Zug.
  - **Einer dehnbaren Spalte keine feste Breite vorgeben.** `expand=True` plus
    `set_fixed_width` nimmt ihr das Dehnen, und rechts bleibt im breiten
    Fenster eine leere Fläche. Eine feste Breite bekommt sie erst, wenn der
    Benutzer sie selbst gezogen hat.
  - **Die Aktionsspalte muss nach jedem Einblenden wieder ans Ende geholt
    werden** (`_move_fixed_columns_last`) — angehängt wird immer hinten, also
    sonst hinter den Knöpfen.
  - **Keine Spalte ist dehnbar** (`expand`). Eine dehnbare Spalte saugt den
    Restplatz auf und rechnet sich bei JEDER Breitenänderung neu — zieht man
    irgendeine Spalte breiter, wandern dadurch auch die Nachbarn (nachgemessen:
    Spalte 0 auf 250 gezogen, die dehnbare Spalte 2 schrumpfte von 337 auf
    237). Genau das war als "wenn ich Spalten verschiebe, bewegen sich alle
    anderen mit" gemeldet. Mit festen Breiten ändert sich nur die angefasste
    Spalte; rechts bleibt im breiten Fenster Platz frei.
  - **Die Leiste darüber trägt NICHT die Klasse `toolbar`.** libadwaita macht
    Knöpfe darin rahmenlos — ein einzelner Knopf neben einer verbundenen
    Gruppe sieht dann aus, als hinge er lose in der Gegend.
  - **Kein Bildlauf um die Tabelle herum.** Sie bringt einen eigenen mit;
    zwei ineinander sind mit dem Rad kaum zu treffen, und die Spaltenköpfe
    wären beim Blättern weg. Auf der Albumseite steht der Kopf deshalb fest
    darüber statt mitzuscrollen.
  - **Zeilen werden wiederverwendet.** Ein Favoriten-Umschalter in der Zeile
    bekommt beim Binden seinen Zustand gesetzt und meldet das als `toggled` —
    ohne Vergleich mit dem gemerkten Wert löst allein das Scrollen
    Server-Aufrufe aus.

- **Jede Liste, die tausende Einträge haben KANN, muss wiederverwenden.**
  Das Fenster der Warteschlange baute eine `Adw.ActionRow` je Titel — bei
  einer gemischten Bibliothek (4438 Titel) öffnete es dadurch gar nicht mehr.
  Mit `Gtk.ListView` über einem `Gio.ListStore` sind es 0,07 Sekunden
  (nachgemessen, seit 0.1.37). Dasselbe gilt für die Kachelraster
  (`CardGrid`/`AlbumGrid`) und die Musiktabellen (`ColumnList`) — die
  `Gtk.ListBox`-Listen im Rest der App sind nur dort in Ordnung, wo die
  Länge von Natur aus klein ist (Titel eines Albums, Suchtreffer).
- **Die Zufallswiedergabe zieht 200 Titel, nicht die ganze Bibliothek**
  (`_SHUFFLE_LIMIT`): eine Warteschlange mit tausenden Einträgen ist nicht
  mehr überschaubar — der Zähler an der Abspielleiste sah aus wie ein Fehler.

## Was diese App bewusst anders macht als die Mac-App

Jeweils nachgeprüft, nicht vermutet:

- **Musik läuft immer direkt, auch FLAC und WAV.** GStreamer spielt alle hier
  vorkommenden Audioformate selbst, deshalb geht es auf `/api/stream/{id}`
  statt über `/api/playback/{id}`. Das erspart pro Titelwechsel einen
  ffprobe-Lauf. Der Browser muss FLAC und WAV umwandeln, weil kein Browser
  sie zuverlässig abspielt — eine Einschränkung von dort, nicht von hier.
- **Lokale Bibliotheken brauchen keine Formatanpassung.** Die Mac-App wandelt
  Dateien um, die macOS nicht abspielen kann, und pflegt dafür einen
  Zwischenspeicher. Hier gegenstandslos: MKV, MP4, AVI, WMV, HEVC, H.264,
  VP9, AV1, AC3, DTS und E-AC3 laufen direkt (Plugins einzeln geprüft, am
  schwierigsten Fall getestet: 4K-MKV mit HEVC und TrueHD Atmos 7.1).
- **Eingelesen wird mit `GstPbutils.Discoverer`, nicht mit ffprobe.** ffprobe
  und ffmpeg sind auf dem Zielsystem nicht zwingend installiert (hier:
  fehlen beide), GStreamer dagegen schon. Gemessen 0,12 Sekunden pro Datei,
  122 Videos in anderthalb Sekunden.
- **Vorschaubilder lokaler Dateien entstehen im Vorgriff** (seit 0.1.20,
  `prefetch_thumbnails_async`): ein Faden geht die ganze Bibliothek durch,
  angestoßen beim Start, beim Öffnen und nach dem Einlesen. **Reihenfolge der
  Prüfungen ist dabei entscheidend:** erst der eigene Zwischenspeicher (ein
  `stat`, 0,01 ms), dann die Auskunft des Dateimanagers über Gio — die kann
  auf einem langsamen USB-Stick zehner Millisekunden kosten, in der falschen
  Reihenfolge kam der Vorgriff über 550 schon fertige Dateien in 27 Sekunden
  nicht hinaus. Aus demselben Grund fragt `LocalCardWidget.bind` gar nichts
  mehr nach, sondern gibt nur `gstthumb://<pfad>` weiter; entschieden wird im
  Ladefaden.
- **Vorschaubilder lokaler Dateien** kommen zuerst aus dem Zwischenspeicher
  des Dateimanagers (`thumbnail::path` über Gio). Fehlt eines, erzeugt die App
  seit 0.1.19 selbst eins — **mit GStreamer, nicht mit ffmpeg**: Pipeline in
  den Pause-Zustand bringen, auf 15 % der Laufzeit springen, das anliegende
  Bild als JPEG abholen (`local_library.thumbnail_bytes`, gemessen 0,03 bis
  0,11 Sekunden je Datei). Die frühere Aussage "bräuchte wieder ffmpeg" war
  falsch.

## Unscharfe Vorschaubilder ohne eigenes Cover — echter Fix in 0.1.38

User-Report 2026-09-13: "Ich hatte auf der Linux App gestern bemängelt, dass
die Vorschaubilder in den Kacheln, wenn kein Cover geladen ist, also bei den
privaten Bibliotheken, aber auch bei Serienfolgen, ziemlich unscharf ist.
Angeblich wurde das gefixt, aber die sind immer noch unscharf." Betrifft
jedes Item OHNE TMDB/Custom-Metadata-Poster (`poster_path_for_item` fällt
dann auf `/api/thumb/{id}` zurück — server-generiert, fix 480×270, 16:9,
siehe Server-CLAUDE.md „Scanner & Metadaten").

**Warum der 0.1.24-Fix nie griff:** er machte `CardWidget`/`FolderCardWidget`
selbst zwar breiter für `kind == "private"` (`CARD_WIDTH_WIDE`), aber die
tatsächlich benutzte Bibliotheksansicht (`widgets/grid.py: CardGrid`) erzwingt
für PRIVATE Bibliotheken absichtlich dieselbe Kartenform wie Filme/Serien
(`aspect = "movies" if self.kind == "private" else self.kind` — User-Wunsch
aus einer früheren Session: "auch in der Bibliothek YouTube sollen die
Kacheln die gleiche Größe haben") und übergibt diese überschriebene Form an
beide Widgets. Der Breiten-Umschalter aus 0.1.24 wird dadurch nie erreicht —
weder bei privaten Bibliotheken noch (da nie dafür gebaut) bei Serienfolgen
ohne Poster, die ohnehin schon immer in der normalen Filme/Serien-Kartenform
(2:3) laufen.

**Der tatsächliche Bug saß einen Schritt früher, beim Dekodieren:**
`load_poster_async(decode_width=…)` bekam bisher immer die KARTENBREITE
(z. B. 168 px) — bei einem 16:9-Bild ergibt das nur ≈94 px Höhe. Erst danach
skaliert `Gtk.Picture`s `ContentFit.COVER` dieses bereits stark verkleinerte
Bild auf die volle, deutlich größere Kartenhöhe (z. B. 252 px bei einer
2:3-Karte) wieder hoch — ein Faktor von ≈2,7×. Genau dieses nachträgliche
Hochskalieren eines zu klein dekodierten Bilds war die Unschärfe, nicht der
Zuschnitt selbst (der reine COVER-Crop eines 16:9-Bilds in eine 2:3-Karte
skaliert eigentlich eher leicht HERUNTER + croppt seitlich, kein
Qualitätsverlust).

**Fix (`widgets/card.py`):** neue Funktion `_thumb_decode_width(frame_width,
frame_height)` — liefert `max(frame_width, frame_height * 16/9)`, also genug
Dekodierbreite, damit die anschließende COVER-Höhenskalierung nicht mehr
über die native Auflösung hinaus hochskalieren muss (deckt sich ungefähr mit
der nativen Server-Auflösung 480×270, kein künstlicher Zusatzverlust vorher).
`CardWidget`/`FolderCardWidget` merken sich jetzt zusätzlich `_frame_height`
und nutzen die Funktion NUR, wenn der gewählte Pfad tatsächlich ein
`/api/thumb/`-Fallback ist (echte TMDB/Custom-Poster behalten `_frame_width`
als Dekodierbreite — deren Seitenverhältnis passt schon zur Kartenform, da
gibt es das COVER-Mismatch-Problem gar nicht).

**Kein Server-seitiger Rescan/Neu-Einlesen nötig** — reines Client-
Rendering-Problem, keine gespeicherten Daten waren betroffen. Nach dem
Update auf 0.1.38 sind die Kacheln beim nächsten Öffnen der Bibliothek
automatisch scharf (der Bild-Cache in `widgets/poster.py` schlüsselt ohnehin
über `(server_path, decode_width)` — ein geänderter `decode_width` erzeugt
automatisch einen neuen Cache-Eintrag statt eine alte, zu klein dekodierte
Version wiederzuverwenden).

## Packaging

`.deb` via debhelper (`debian/rules` mit `--buildsystem=none`, native
Source-Format `3.0 (native)`) — kopiert `goldfish_linux/` 1:1 nach
`/usr/lib/python3/dist-packages/`, kein pybuild/setuptools-Build-Schritt.
`install.sh` lädt das aktuellste GitHub-Release-`.deb` und installiert per
`apt install`. GitHub-Actions-Workflow (`release.yml`) baut bei jedem
`v*`-Tag automatisch — braucht `permissions: contents: write` im
Workflow-YAML, sonst scheitert `softprops/action-gh-release` am
Standard-`GITHUB_TOKEN`.

## Prüfen am echten System

Bis v0.1.6 entstand der Code auf macOS, ohne GTK4 ausführen zu können — das
hat eine falsche Fehlerdiagnose verursacht (siehe oben). Seit v0.1.7 wird auf
einem echten Linux-Rechner gearbeitet und geprüft.

Nützliche Griffe dort: `timeout 20 python3 -m goldfish_linux` im Repo — Exit
124 heißt "Fenster lief", Exit 0 heißt "App hat sich sofort beendet". Fenster
finden mit `wmctrl -lp` (Titel genau `Goldfish`; ein `grep Goldfish` trifft
auch Browser-Tabs). Screenshots mit `gnome-screenshot -w -f <pfad>` — dabei
zählt das AKTIVE Fenster, also vorher mit `wmctrl -i -a <id>` nach vorn
holen, sonst landet ein fremdes Fenster im Bild. ImageMagick `import` und
`xdotool` sind nicht installiert.

Für Ansichten, die nur per Mausklick erreichbar sind, hilft ein kurzes
Treiberskript, das `GoldfishApplication` startet und die Seiten direkt auf
den Navigationsstapel legt — schneller und verlässlicher als der Versuch,
Klicks zu erzeugen.

## Bewusst nicht übernommen

Cast und AirPlay (auf Linux ohne Entsprechung), ein eigenes Fenster pro
Video (macOS-Eigenheit), die Serververwaltung (bleibt wie in allen Clients
dem Browser überlassen), die Formatanpassung lokaler Dateien (GStreamer
spielt alles direkt ab) und der Regler für den Vorlaufpuffer lokaler
Bibliotheken (bei GStreamer ohne erkennbaren Nutzen).

Das Zusammenlegen mehrerer Datenträger stand hier ursprünglich auch —
die Begründung ("reine Bequemlichkeit") hat der Benutzer zu Recht
zurückgewiesen, seit 0.1.17 ist es gebaut (`local_library.py`:
`merged_roots`/`merged_library`/`visible_libraries`/`find_duplicates`).

## Versionierung

`goldfish_linux.__version__` in `__init__.py` + `pyproject.toml` +
`debian/changelog` müssen bei jedem Release synchron gehalten werden (kein
automatischer Versions-Inject beim Build). Versionsnummer ist auf der
Login-Seite + in der Seitenleiste sichtbar.
