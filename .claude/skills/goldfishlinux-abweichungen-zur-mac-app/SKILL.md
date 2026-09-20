---
name: goldfishlinux-abweichungen-zur-mac-app
description: "Use when comparing GoldfishLinux with the Mac app: what this client intentionally does differently, and what was deliberately not ported."
metadata:
  project: GoldfishLinux (boernie77/goldfish-linux)
  source: "CLAUDE.md-Aufteilung 2026-09-20"
---

# goldfishlinux-abweichungen-zur-mac-app

Aus der frueheren Sammel-CLAUDE.md des GoldfishLinux-Repos ausgelagerter Themenbereich (Zeichen: 2906, Sektionen: 2). Volltext des Originals: Skill `goldfishlinux-full-archive`.

## Harte Regeln (zuerst lesen)

- Musik laeuft immer direkt ueber `/api/stream/{id}` (auch FLAC/WAV) - das erspart pro Titelwechsel einen ffprobe-Lauf.
- Lokale Bibliotheken brauchen keine Formatanpassung; eingelesen wird mit `GstPbutils.Discoverer`, nicht mit ffprobe (ffmpeg/ffprobe sind auf dem Zielsystem nicht zwingend installiert).
- Vorschaubilder lokaler Dateien entstehen notfalls mit GStreamer, nicht mit ffmpeg.
- Bewusst nicht uebernommen: Cast/AirPlay, ein Fenster pro Video, Serververwaltung, Formatanpassung lokaler Dateien, Vorlaufpuffer-Regler.

---

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

