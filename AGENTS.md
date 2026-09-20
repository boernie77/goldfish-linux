# GoldfishLinux — Projektregeln

**Diese Datei wird von Claude Code UND Hermes Agent bei jedem Start vollständig geladen — deshalb kurz halten.**
Detailwissen liegt in den Themenskills (Tabelle unten), nicht hier. Neue Erkenntnisse gehören in den passenden
Themenskill, nicht in diese Datei. Sie ist bewusst unter 20.000 Zeichen (harte Ladegrenze in Hermes) und unter
der von Anthropic empfohlenen 200-Zeilen-Marke.

Aufbau: `CLAUDE.md` besteht nur noch aus der Zeile `@AGENTS.md` (Claude Code folgt dem Verweis), Hermes liest
`AGENTS.md` direkt. Die Themenskills liegen unter `.claude/skills/` (Claude Code) und sind über den Symlink
`.hermes/skills/` auch für Hermes sichtbar. Volltext der früheren Sammel-`CLAUDE.md` (41.260 Bytes, Stand
2026-09-20): Skill `goldfishlinux-full-archive`.

---

## Produkt & Stack

**GoldfishLinux** ist der native Linux-Desktop-Client für den Goldfish-Server (separates Repo
`github.com/boernie77/goldfish`, lokal `~/Projekte/Videoplayer/`). Eigenes öffentliches Repo:
`github.com/boernie77/goldfish-linux` (MIT, seit 2026-09-12).

- Python 3 + PyGObject, **GTK4 + libadwaita** — kein Electron, kein Tauri, kein Web-View
- Auslieferung als `.deb` über debhelper; `install.sh` zieht das neueste GitHub-Release
- `release.yml` (GitHub Actions) baut bei jedem `v*`-Tag
- Zielsysteme: Debian 12+, Ubuntu 24.04+, Mint 22+ (libadwaita ≥ 1.4); ältere Systeme werden bewusst nicht unterstützt
- Server-Kommunikation: `requests.Session` mit echtem Cookie für API-Calls, `?session=<token>`-Fallback für die Wiedergabe (GStreamer hat keinen Cookie-Jar)
- Der Client kennt rund 80 Server-Endpunkte (Anmeldung, Bibliotheken, Items, Wiedergabe, Staffeln, Besetzung, Trailer, Personen, Fortsetz-Position, Alben, Sammlungen, Playlists, Startseiten-/Reiter-Einstellungen, Vorschaubilder, Untertitel, Gesehen-Sync, optimierte Downloads)

## Harte Verbote

- **Kein Electron/Tauri und kein Web-View** — die Oberfläche ist GTK4 + libadwaita.
- **`Adw.Spinner` NICHT verwenden** (braucht libadwaita ≥ 1.6). Überall `Gtk.Spinner()` + `.start()`.
- **GTK-Widget nur EINMAL und von innen nach außen parenten.** Nie ein Widget „vorläufig" anhängen und später umhängen — GTK verweigert das zweite `set_child()` lautlos-kritisch (Root Cause von v0.1.4).
- **Es darf nur EIN Wiedergabefenster geben.** Zwei Fenster übereinander waren eine Falle: ein modaler Dialog des Hauptfensters landete unsichtbar HINTER dem Player und wartete auf eine Antwort — für den Benutzer hing die App.
- **In der Player-Steuerleiste darf kein Kind seine SICHTBARKEIT ändern** — die Zeitleiste daneben hat `hexpand` und dehnt sich, dann springt sie. Platz stattdessen per `set_opacity`/`set_can_target` reservieren.
- **Abgelöste `Gtk.MediaFile` müssen ABGEBAUT werden, nicht nur angehalten** (`set_playing(False)` → Bild abhängen → `clear()`). Gemessen: fünf Videos hintereinander = Fäden 33 → 89, Speicher 221 → 327 MB; beim Benutzer nach acht Wechseln 135 Fäden und 1,2 GB.
- **Abgelöste Medien abklemmen** und in jedem Handler prüfen, dass das Signal vom AKTUELLEN Medium kommt — sonst legt sich „Wiedergabe fehlgeschlagen" über den laufenden Film.
- **Icon niemals selbst zeichnen** — `internal/webassets/web/favicon.svg` aus dem Server-Repo wiederverwenden (Twemoji-Tropenfisch 🐠, CC-BY 4.0).
- **Ordnerkacheln nur, wo sie hingehören** — Film-Bibliotheken sind immer flach (der Server bringt 2808 Release-Ordner mit je einer Datei).
- **`/api/playback/{id}` nie in Schleifen über viele Items aufrufen** — ffprobe serverseitig, hat den Server schon in einen Timeout gezogen.
- **Diagnosen nie auf einem System stellen, das die Oberfläche nicht ausführen kann.** Eine auf macOS entstandene Diagnose zum Startfehler war nachweislich falsch.
- **Nichts committen oder pushen, was nicht ausdrücklich beauftragt ist** — das Repo ist öffentlich.

## Versionierung (Pflicht)

Drei Orte müssen bei jedem Release **synchron** sein, im selben Commit:

1. `goldfish_linux.__version__` in `goldfish_linux/__init__.py`
2. `pyproject.toml`
3. `debian/changelog`

**Kein automatischer Versions-Inject beim Build.** Die Versionsnummer ist auf der Login-Seite und in der Seitenleiste sichtbar.

- **Changelog-Stil:** der ERSTE SATZ eines Stichpunkts muss die Aussage tragen — nur er landet im Update-Dialog (`_highlights`, höchstens 5 kurze Stichpunkte, mittig, ohne Installations-Anleitung).
- Feature-Historie im Einzelnen: `debian/changelog`. Die Chronik 0.1.7 bis 0.1.56 (welche Funktion, welcher User-Report, welcher Fix in welcher Fassung) steht im Skill `goldfishlinux-overview`.
- **Den aktuellen Stand nie aus dieser Datei ablesen:** `git log --oneline -10` und `__version__` prüfen.

## Build, Paket & Release

- `.deb` via debhelper: `debian/rules` mit `--buildsystem=none`, Source-Format `3.0 (native)`. `goldfish_linux/` wird 1:1 nach `/usr/lib/python3/dist-packages/` kopiert — **kein pybuild-/setuptools-Build-Schritt**.
- `install.sh` lädt das aktuellste GitHub-Release-`.deb` und installiert per `apt install`.
- `release.yml` braucht `permissions: contents: write`, sonst scheitert `softprops/action-gh-release` am Standard-`GITHUB_TOKEN`.
- **`gstreamer1.0-vaapi` gehört als Abhängigkeit ins Paket** (seit 0.1.26). Ohne dieses Paket dekodiert GStreamer alles rein per Software (`avdec_*` aus `gstreamer1.0-libav`), auch wenn eine Intel-/AMD-iGPU per VAAPI könnte — sichtbar als Ruckeln bei höherer Auflösung/HEVC. Kein Code-Zweig nötig: `playbin` (Server-Streams) und `uridecodebin` (Eigene Datenträger) wählen den Decoder automatisch nach Rang.
- Prüfung vor der Abgabe: `python3 -m py_compile` über die geänderten Dateien plus `pyflakes`. Auf macOS ohne GTK4-Runtime ist mehr nicht möglich — **das ist ausdrücklich kein Ersatz für einen Test am echten Linux-Rechner**, und solche Änderungen gelten als „ungeprüft auf echtem GTK".

## API-Kompatibilität mit dem Server

- **Bei jeder Server-API-Änderung `goldfish_linux/api.py` gegenprüfen.**
- **`folder=""` bedeutet in `ListItems` NICHT „nur Wurzelebene", sondern „kein Filter"** (alle Items rekursiv). Der Sonderwert für „nur Wurzelebene" ist `"/"`. Bei jedem künftigen Aufruf: immer `"/"` verwenden, nie `""`.
- `playback.Profile` trägt im Go-Code keine JSON-Tags — die Qualitätsstufen kommen GROSS geschrieben an (`ID`, `Label`, `MaxHeight`, …), als einziges Objekt der ganzen API; `api.PlaybackProfile` normalisiert das.
- `/api/subtitle/{id}/{idx}.vtt` extrahiert die Spur beim ERSTEN Abruf per ffmpeg und blockiert so lange — dort gilt ein eigenes Zeitlimit von 120 Sekunden.
- `/api/nav/preferences` und `/api/home/preferences` liefern Zeilen mit `libraryId`, **nicht** `id`; mit `lib["id"]` stirbt der Signal-Handler an einem KeyError, und GTK schreibt das nur auf die Konsole (der Schalter wirkt für den Benutzer wirkungslos).
- **„Nächste Folge automatisch starten": die Reihenfolge bestimmt der Server** (`GET /api/items/{id}/next-episode`) — nie clientseitig aus der Staffelliste ableiten, das scheiterte an Doppelfolgen. Der Schalter liegt pro Konto auf dem Server (`GET/PUT /api/playback/preferences`), `view_prefs.json` hält nur die lokale Kopie.
- Wiedergabe läuft über den `?session=<token>`-Query-Fallback, den der Server für Cast-Empfänger bereitstellt.

## Fehlerbehandlung & Hintergrund-Threads

- **In Hintergrund-Fäden NIE nur den erwarteten Fehlertyp fangen** — immer zusätzlich ein breites `except Exception` mit **sichtbarer** UI-Fehlermeldung. Sonst wirkt jeder unerwartete Fehler wie „nichts passiert" (Spinner hängt für immer).
- **Gespeicherte Anmeldung nur verwerfen, wenn der Server sie ablehnt.** Ein Zeitüberschreiten oder 502 vom Reverse-Proxy sagen nichts über ihre Gültigkeit (real passiert: Anmeldung verloren, weil ein kurz überlasteter Server den Token löschte).
- **GStreamer bricht bei jeder Nicht-2xx-Antwort des Stream-Servers sofort ab und wiederholt NICHT selbst** (AVPlayer/ExoPlayer tun das). Der Client wiederholt einen Anlauf-Fehler in den ersten 8 s zweimal im Abstand von 3,2 s, mit neuer `_t`-Kennung, damit der Server sicher neu aufsetzt.
- **DynDNS/IPv6-Fallstrick:** bei „Connection reset" (nicht Timeout!) gegen einen selbstgehosteten Server mit DynDNS zuerst `dig AAAA <domain>` prüfen — ein veraltetes IPv6-Präfix kann den TCP-Handshake zu einem falschen Host gelingen lassen, und `requests`/urllib3 fällt NICHT automatisch auf IPv4 zurück (kein Happy-Eyeballs wie im Browser). Fix bei diesem Symptom: `urllib3.util.connection.allowed_gai_family` auf `socket.AF_INET` patchen.

## GTK4/libadwaita — Regeln, die aus Fehlern entstanden sind

- **Player:** `Gtk.MediaFile` als Paintable in einem `Gtk.Picture` mit eigener Steuerleiste. `Gtk.Video` war die erste Wahl, ist aber eine Sackgasse (fest eingebaute, von außen unerreichbare Steuerleiste: kein Untertitel über dem Bild, keine Vorschaubilder, kein eigener Balken).
- `Adw.NavigationPage.child` ist **construct-only** (kein `set_child()` danach): alle Page-Klassen bauen ihr Inhalts-Widget VOR `super().__init__()` und übergeben es als `child=`-Kwarg.
- **Kacheltitel sind einzeilig mit Auslassung, der volle Titel steht im Tooltip.** Ein `Gtk.Label` mit Umbruch und `lines=2` fordert bei vorgegebener Höhe die volle Textbreite an (nachgemessen 297 statt 168 px) — die Kachel bekommt zu viel Platz und zeichnet eine Lücke. `max-width-chars`, `width-request`, `halign` und eine überschriebene `do_measure` helfen dagegen nicht.
- **`Gtk.Picture` meldet die Naturbreite des Bildes.** Jede Bildkachel über die Konstruktion in `widgets/card.py` (`_image_frame`: leere Box als HAUPTKIND eines `Gtk.Overlay`, Bild als Overlay-Kind mit `set_measure_overlay(picture, False)`) bauen — sonst ziehen sich die Abstände beim Vergrößern des Fensters auf. Dekodierbreite über `_thumb_decode_width`, sonst sind die `/api/thumb/`-Fallbacks unscharf.
- **Lange Listen wiederverwenden:** `Gtk.GridView` statt `Gtk.FlowBox` (2717 Album-Kacheln = 4,65 Sekunden blockierter Hauptablauf), `Gtk.ListView` über `Gio.ListStore` für potenziell riesige Zeilenlisten, `Gtk.ColumnView` für Musiktabellen. `Gtk.ListBox` nur dort, wo die Länge von Natur aus klein ist (Titel eines Albums, Suchtreffer).
- **`Gtk.GridView` legt rund 385 Kacheln an, egal wie groß das Fenster ist** — Poster erst laden, wenn die Kachel wirklich in Sicht ist (12 statt 385 Anfragen); dekodieren gehört in den Hintergrund.
- **NIE die Elternkette einer Kachel ablaufen** (`picture.get_parent()` in einer Schleife) — das erzeugt Hüllobjekte, die GTKs Aufräumen in die Quere kommen, und endet reproduzierbar im Speicherauszug. Stattdessen `CardWidget(scroller=...)` + `compute_bounds(scroller)`.
- Ein Modellwechsel in einem SICHTBAREN, stark schrumpfenden Raster ist nicht still (GTK-Warnungen) — deshalb steht beim Laden weiter der Ladekreis an seiner Stelle.
- `Gtk.Spinner` erst NACH dem Einhängen starten, sonst fehlt die Frame-Clock.
- Bei serverseitiger Umwandlung geht Springen nicht mit `media.seek()`: die Dauer kommt aus `item["durationSec"]`, ein Sprung startet eine neue Umwandlung ab der Zielsekunde (`start=`, `fresh=1`, stabiler `_t`-Token), `_virtual_offset` rechnet die Position um.

## Navigation, Ansichten & Wiedergabe

- **Ordnernavigation hat drei Fälle** (wie im Browser, `grid.js`): in der Bibliothekswurzel Ordnerkacheln plus die Items der Wurzel (`folder="/"`); in einem Ordner mit gesetztem `folder_nav.drilldown` dessen direkte Unterordner plus die unmittelbar darin liegenden Dateien (serverseitig gibt es kein „nur direkte Kinder", also clientseitig nachfiltern); im Regelfall gar keine Ordnerkacheln, sondern die Dateien rekursiv flach. **Fehlt der dritte Fall, erscheinen dieselben Dateien doppelt.**
- **Filmbibliotheken sind IMMER flach**, Serien behalten ihre Ordnerkacheln. Die Startseite hat zwei übergreifende Streifen („Fortsetzen", „Als nächstes") über ALLE Bibliotheken, darunter „Zuletzt hinzugefügt" pro Bibliothek — der Server liefert das getrennt, das Zusammenführen ist Client-Aufgabe.
- **Musik läuft immer direkt** über `/api/stream/{id}` (auch FLAC und WAV) statt über `/api/playback/{id}` — das erspart pro Titelwechsel einen ffprobe-Lauf.
- **„Eine Kachel pro Film"** (`variants.py`, `group_variants`, Gruppierung über `metadataId`, Repräsentant = höhere Auflösung/Bitrate): wird die Logik geändert, muss sie in allen vier Clients mitgezogen werden. Gemessen gegen die echte Serien-Bibliothek: 18 789 Dateien → 18 224 Kacheln.
- **Die Zufallswiedergabe zieht 200 Titel, nicht die ganze Bibliothek** (`_SHUFFLE_LIMIT`) — Warteschlangen mit Tausenden Einträgen sind nicht mehr überschaubar.

## Repo-Sichtbarkeit & Sicherheitsregeln

- Das Repo `boernie77/goldfish-linux` ist **öffentlich** (MIT). Bei jeder Änderung prüfen, ob committeter Code oder Kommentare echte Namen, E-Mails, interne IPs oder Zugangsdaten enthalten.
- Platzhalter in spitzen Klammern (`?session=<token>`, `dig AAAA <domain>`, `<UNRAID-LAN-IP>`) sind Absicht — **nicht durch echte Werte ersetzen**.
- Die Sichtbarkeit nie aus einer Doku-Zeile übernehmen, sondern nachsehen: `gh repo view <repo> --json visibility`.

## Prüfen am echten System

Bis v0.1.6 entstand der Code auf macOS, ohne GTK4 ausführen zu können — das hat eine falsche Fehlerdiagnose verursacht. Seit v0.1.7 wird auf einem echten Linux-Rechner gearbeitet und geprüft. Details: Skill `goldfishlinux-startfehler-und-systemtests`.

- `timeout 20 python3 -m goldfish_linux` im Repo — Exit **124** heißt „Fenster lief", Exit **0** heißt „App hat sich sofort beendet".
- Fenster finden mit `wmctrl -lp` (Titel genau `Goldfish`; ein `grep Goldfish` trifft auch Browser-Tabs).
- Screenshots mit `gnome-screenshot -w -f <pfad>` — das zählt das AKTIVE Fenster, also vorher `wmctrl -i -a <id>`. ImageMagick `import` und `xdotool` sind nicht installiert.
- Für Ansichten, die nur per Mausklick erreichbar sind: kurzes Treiberskript, das `GoldfishApplication` startet und die Seiten direkt auf den Navigationsstapel legt.

## Wo das Detailwissen liegt

| Skill | Wofür |
|---|---|
| `goldfishlinux-overview` | Produktidentität, aktueller Stand, Chronik 0.1.7 bis 0.1.56 mit User-Reports und Fixen |
| `goldfishlinux-startfehler-und-systemtests` | Korrektur der Startfehler-Diagnose, Prüfen am echten Linux-Rechner |
| `goldfishlinux-gtk-architecture` | Player-Technik, Auth, libadwaita-Mindestanforderung, Parenting, `folder`-Konvention, Hintergrund-Threads, DynDNS/IPv6, Icon, VAAPI |
| `goldfishlinux-gtk-pitfalls` | Kachelbreiten, GridView/FlowBox, MediaFile-Abbau, nur ein Playerfenster, Ordnernavigation |
| `goldfishlinux-performance-und-rendering` | flache Filmbibliotheken, Kachel-Wiederverwendung, Poster-Dekodierung, Musiklisten, Unschärfe-Fix 0.1.38 |
| `goldfishlinux-abweichungen-zur-mac-app` | bewusste Unterschiede zur Mac-App, bewusst nicht Übernommenes |
| `goldfishlinux-packaging-release` | `.deb`/debhelper, `install.sh`, `release.yml`, Versionsgleichstand |
| `goldfishlinux-full-archive` | vollständiges Original der früheren CLAUDE.md, verbatim — Fallback, wenn etwas fehlt |

Alle Skills liegen unter `.claude/skills/` und sind über den Symlink `.hermes/skills/` auch für Hermes sichtbar
(`.hermes/` ist git-ignoriert). Claude Code liest sie aus `.claude/skills/`, Hermes nach
`hermes skills trust <repo>` ebenfalls — eine Datei, zwei Agenten.

## Regel für neue Erkenntnisse

Neue dokumentationswürdige Erkenntnisse gehören **in den passenden Themenskill**, nicht in diese Datei. Was hier
steht, muss bei jedem einzelnen Start relevant sein — alles andere kostet nur Kontext und senkt die Befolgungsrate.