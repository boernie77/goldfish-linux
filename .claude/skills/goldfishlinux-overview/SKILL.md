---
name: goldfishlinux-overview
description: "Use when you need GoldfishLinux's product identity, its current state, or the version chronicle 0.1.7-0.1.56 (which feature, report or fix landed in which version)."
metadata:
  project: GoldfishLinux (boernie77/goldfish-linux)
  source: "CLAUDE.md-Aufteilung 2026-09-20"
---

# goldfishlinux-overview

Aus der frueheren Sammel-CLAUDE.md des GoldfishLinux-Repos ausgelagerter Themenbereich (Zeichen: 12584, Sektionen: 3). Volltext des Originals: Skill `goldfishlinux-full-archive`.

## Harte Regeln (zuerst lesen)

- Der aktuelle Stand steht NIE in der Doku, sondern nur in `git log --oneline -10` + `goldfish_linux/__init__.py` (`__version__`); Feature-Historie im Detail: `debian/changelog`.
- Die Versions-Chronik (0.1.7 bis 0.1.56) ist Gedaechtnis, nicht Zustandsbeschreibung: sie sagt, WO etwas entstanden ist, und nennt die User-Reports je Fassung.
- Viele Eintraege ab 0.1.47 sind ausdruecklich 'ungeprueft auf echtem GTK' (Arbeitssession auf macOS) - das gilt weiter, bis jemand am Linux-Rechner nachgeprueft hat.
- Aeltere Bugfix-Historie bis v0.1.6 liegt als Memory `project_feature_goldfish_linux` im SERVER-Repo und wird hier nicht automatisch gesehen - fuer den aktuellen Stand ist sie nicht verlaesslich.

---

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
| 0.1.48 | 0.1.47 reichte nicht — Vollbild tat gar nichts mehr, solange das Hauptfenster offen war, funktionierte aber sofort danach. Ursache: `open_player()` hängte das Wiedergabefenster per `set_transient_for(ctx.window)` ans Hauptfenster — der Compositor (Cinnamon/Muffin) behandelt transiente Fenster wie Dialoge und ignoriert Vollbild-Anfragen dafür. Fix: `set_transient_for()` entfernt (siehe unten). **Weiterhin ungetestet auf echtem GTK.** |
| 0.1.50 | **„Nächste Folge automatisch starten"** (User-Wunsch 2026-09-18, über ALLE Goldfish-Apps): Einstellungen → Wiedergabe, Standard AUS. Am Ende einer Serienfolge zeigt der Player ein Hinweis-Overlay mit 10-s-Countdown und „Jetzt abspielen"/„Abbrechen"; Countdown oder Knopf wechseln per `_switch_to()` im GLEICHEN Fenster weiter — `self.profile`/`self.audio_index` bleiben stehen, die Folge startet also in derselben Auflösung/Tonspur. Die nächste Folge bestimmt der **Server** (`GET /api/items/{id}/next-episode`, siehe Server-CLAUDE.md „Nächste Folge automatisch starten") — anfangs leitete der Client sie selbst aus der Staffelliste ab, was an Doppelfolgen scheiterte (der Seasons-Endpoint listet beide Hälften mit derselben `itemId`). Anzeigename ist `nextTitle` (TMDB-Folgentitel), nicht der Dateiname. Der Schalter liegt **pro Konto auf dem Server** (`GET/PUT /api/playback/preferences`); `view_prefs.json` hält nur die lokale Kopie für den Offline-Fall. Das Overlay liegt VOR `_play_next()`: nur wenn der Queue-Nachfolger zur selben Serie gehört, übernimmt der Hinweis (und zieht `queue_index` mit), ein fremder Queue-Titel behält Vorrang, der Zufallsmodus ist unberührt. **Ungeprüft auf echtem GTK** (Arbeitssession lief auf macOS; nur `py_compile`/`pyflakes`). |
| 0.1.51 | **Zeitleiste behält ihre Länge** (User-Report 2026-09-18: „wenn man in die Zeitleiste drückt … wird die Zeitleiste kurz länger und dann wieder kürzer"): `_tick`/`seek_to` fielen auf `media.get_duration()` zurück, und ein wachsender HLS-Transcode meldet eine mitwandernde Dauer — ein Sprung startet überdies eine neue Umwandlung ab der Zielsekunde. Jetzt kommt die Gesamtlänge ausschließlich aus der Item-Dauer (`durationSec`), bei unbekannter Dauer bleibt die Zeitleiste stehen; die Zeit-Beschriftungen haben feste Zeichenbreite. Nebenfund: `_report_stop` meldete die Sessions-Position ohne `_virtual_offset` (falscher Weiterschauen-Punkt nach einem Sprung) — jetzt absolut + Item-Dauer. |
| 0.1.52 | **Updatefenster aufgeräumt** (User-Vorgabe 2026-09-18): Kopfzeile mit installierter + verfügbarer Fassung steht fest oben (vorher Teil des scrollenden Notizentextes — „die Zeile springt"), der Installations-Abschnitt der GitHub-Notizen (`## Installation`) wird ausgefiltert (die App spielt das Paket selbst ein), die Neuerungen stehen in einem eigenen scrollbaren Bereich. Dazu die Versionszeile unter der Seitenleiste einzeilig mit Auslassung, damit der Aktualisierungs-Hinweis die Breite nicht ändert. **Ungeprüft auf echtem GTK** (nur `py_compile`/`pyflakes`; die Notizen-Aufbereitung wurde gegen den echten Freigabe-Text von v0.1.51 geprüft). |
| 0.1.53 | **Zeitleiste springt beim Spulen nicht mehr** (2026-09-18, zweiter Anlauf — der 0.1.51-Fix an der Dauer war NICHT die Ursache): die Auflösungs-Anzeige in der Steuerleiste wurde bei unbekannter Größe (neues Medium mitten im Sprung) geleert und AUSGEBLENDET — die Zeitleiste daneben hat `hexpand` und nahm den frei werdenden Platz ein, um ihn Sekunden später wieder abzugeben. Jetzt: immer sichtbar, feste Zeichenbreite, bei unbekannter Größe unverändert. Dieselbe Ursache hatten die Vor/Zurück-Knöpfe (reservieren ihren Platz jetzt per `set_opacity`/`set_can_target` statt `set_visible`). **Lehre: in dieser Leiste darf kein Kind seine SICHTBARKEIT ändern — die Zeitleiste daneben dehnt sich aus.** Dazu: Gesehen-/Favoriten-Zustand aus der Infokarte wird an die Ansichten gemeldet (`AppContext.add_item_state_listener`/`notify_item_state`, schwache Referenzen) und zieht die gebundene Kachel nach, ohne das Rastermodell anzufassen. |
| 0.1.54 | **Anlauf-Fehler werden wiederholt** (User-Report 2026-09-18: „Zeile springt nicht mehr, aber ich bekomme jetzt ständig diesen Fehler" — GStreamer „Internal data stream error"): GStreamer bricht bei JEDER Nicht-2xx-Antwort des Stream-Servers sofort ab und versucht es NICHT selbst erneut (AVPlayer/ExoPlayer tun das). Ein Start, der ins 3-Sekunden-Sperrfenster des Servers nach einem Wiedergabe-Ende trifft (Antwort 503), war damit tot. `_on_media_error` wiederholt einen Fehler innerhalb der ersten 8 s jetzt zweimal im Abstand von 3,2 s (neue `_t`-Kennung, damit der Server sicher neu aufsetzt) — erst danach Fehlerseite und Fehlerbericht. **Updatefenster** zeigt nur noch die Highlights: höchstens 5 kurze Stichpunkte, mittig, ohne Fließtext und ohne Installations-Anleitung (`_highlights`). ⚠ **Beim Schreiben von Changelog-Einträgen darauf achten, dass der ERSTE SATZ eines Stichpunkts die Aussage trägt** — nur der landet im Dialog. |
| 0.1.55 | **Eine Kachel pro Film** — neue Datei `goldfish_linux/variants.py` (`group_variants`, Python-Port der Browser-Funktion `groupVariants`): Gruppierung über `metadataId`, `variantSplit` bleibt eigene Kachel, Repräsentant = höhere Auflösung, dann höhere Bitrate. Angewandt in `browse_page` (vor Anzahl-Zeile/Buchstabenfilter/Raster!), `person_page` und `playlists_page` (Musik ausgenommen). **Gemessen gegen die echte Serien-Bibliothek: 18 789 Dateien → 18 224 Kacheln, also 565 doppelte Kacheln entfernt** — der Client hatte die Bündelung nie, im Browser/Apple/Android gab es sie längst. Wird die Logik geändert, muss sie in allen vier Clients mitgezogen werden. |
| 0.1.56 | **Aufgegliederte Trefferanzeige** (Server 1.4.22): `/api/items?search=` matcht seit dieser Server-Version NUR NOCH den Titel (FTS5-Wortmatch) — Besetzungstreffer sind aus diesem Endpunkt entfernt. Neue Methode `GoldfishClient.search_people()` (`GET /api/search/people`, `q`/`libraryId`/`folder`, Begriffe unter drei Zeichen liefern serverseitig leer und werden clientseitig gar nicht erst abgefragt) füttert eine neue waagerecht scrollbare Schauspieler-Reihe (`widgets/people_row.py`, `PeopleSearchRow`) über dem Treffer-Raster in `BrowsePage` — Bild über `tmdb_image_url(profile_path, size="w185")`, dieselbe Konvention wie bei `CastStrip`. Klick auf eine Karte öffnet dieselbe `PersonPage`-Navigation wie ein Klick auf ein Besetzungsportrait der Detailseite (`DetailPage._open_person`/`SeasonsPage._open_person`), keine neue Ansicht gebaut. Der Client hatte nie eine eigene, clientseitige Besetzungs-Suchlogik (die alte Suche verließ sich vollständig auf den jetzt geänderten Server-Endpoint) — es gab also nichts zu entfernen, nur die neue Reihe zu ergänzen. Serien-Episoden-Sammelkacheln in der Suche (Browser-Vorbild `appendSearchResultCards`) fehlen in diesem Client weiterhin — vorbestehende separate Lücke, hier bewusst nicht angefasst. **Ungeprüft auf echtem GTK** (Arbeitssession lief auf macOS ohne GTK4-Runtime; nur `py_compile`/`pyflakes`). |

Noch offen (Stand 0.1.17): die vollständige TMDB-Filmografie auf der
Personenseite (dort erscheinen derzeit nur die vorhandenen Titel) und die
SSO-Anmeldung, die nur bis zum Laden der Authentik-Seite gegengeprüft ist,
nicht bis zum Ende durchgespielt.

