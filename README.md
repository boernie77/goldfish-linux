# Goldfish Linux

Nativer Desktop-Client für den selbstgehosteten [Goldfish-Videoserver](https://github.com/boernie77/goldfish)
(GTK4 + libadwaita). Anmelden, Bibliotheken/Ordner durchsuchen, Filme/Serien/
Videos direkt streamen (Direct Play oder Server-Transcode) oder für die
Offline-Wiedergabe herunterladen.

Analog zu den bereits existierenden Goldfish-Apps für
[Android](https://github.com/boernie77/goldfish-android) und
[Apple-Plattformen](https://github.com/boernie77/goldfish-apple) — nur eben
für Debian-basierte Linux-Desktops (Debian, Ubuntu, Linux Mint, …).

## Voraussetzungen

- Ein laufender Goldfish-Server (Version mit dem `/api/…`-Endpunkten aus
  diesem Repo — jede halbwegs aktuelle Goldfish-Installation reicht).
- **Debian 12 (Bookworm) oder neuer**, **Ubuntu 24.04 LTS oder neuer**,
  **Linux Mint 22 oder neuer** (bzw. jede andere Distribution mit
  **libadwaita ≥ 1.4** und **GTK4 ≥ 4.10** — ältere Systeme wie Ubuntu 22.04/
  Mint 21.x haben nur libadwaita 1.0 und werden aktuell **nicht**
  unterstützt, siehe „Bekannte Einschränkungen" unten).

## Installation (empfohlener Weg: .deb)

### Option A — Ein-Kommando-Installer

```bash
curl -fsSL https://raw.githubusercontent.com/boernie77/goldfish-linux/main/install.sh | bash
```

Lädt das aktuellste `.deb`-Release von GitHub herunter und installiert es
per `apt install` — das löst alle Abhängigkeiten (GTK4, libadwaita,
GStreamer-Plugins) automatisch mit auf.

### Option B — .deb manuell herunterladen

1. Aktuellstes `.deb` von der [Releases-Seite](https://github.com/boernie77/goldfish-linux/releases)
   herunterladen.
2. Installieren:
   ```bash
   sudo apt install ./goldfish-linux_*.deb
   ```

Nach der Installation ist „Goldfish" im Anwendungsmenü zu finden, oder per
Terminal direkt mit `goldfish` startbar.

## Aus dem Quellcode bauen

Falls kein Release verfügbar ist, oder du selbst Änderungen testen willst:

```bash
git clone https://github.com/boernie77/goldfish-linux.git
cd goldfish-linux

# Build-Werkzeuge (einmalig)
sudo apt install build-essential debhelper dpkg-dev

# .deb bauen — landet in ./dist/
./scripts/build-deb.sh

# installieren
sudo apt install ./dist/goldfish-linux_*.deb
```

### Ohne Paketbau direkt aus dem Repo starten (Entwicklung)

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 \
  gir1.2-gdkpixbuf-2.0 python3-requests \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-libav

git clone https://github.com/boernie77/goldfish-linux.git
cd goldfish-linux
python3 -m goldfish_linux
```

`PyGObject` (das `gi`-Modul) kommt bewusst aus `apt`, nicht aus `pip` — es
bindet systemeigene GObject-Introspection-Typelibs ein, die pip nicht
mitliefern kann. `pyproject.toml` im Repo dient nur der lokalen
`requests`-Abhängigkeit sowie optional `pip install -e .` für Entwickler,
die einen eigenen `goldfish`-Befehl im PATH haben wollen — für die
`.deb`-Installation selbst wird pip nicht benutzt (siehe `debian/rules`).

## Benutzung

1. Beim ersten Start: Server-Adresse (z. B. `https://goldfish.example.com`
   oder `http://192.168.1.50:8098`), Benutzername und Passwort eingeben.
2. Die Anmeldung bleibt über Neustarts hinweg erhalten (Session-Cookie wird
   lokal unter `~/.config/goldfish-linux/settings.json` gespeichert — das
   Passwort selbst wird nie gespeichert). Läuft die Session ab, erscheint
   automatisch wieder der Login-Dialog.
3. Links in der Seitenleiste eine Bibliothek wählen, durch Ordner navigieren,
   ein Video anklicken → Detailseite mit „▶ Abspielen", „⬇ Herunterladen",
   „✓ Gesehen" und „♥ Favorit".
4. Heruntergeladene Videos erscheinen unter „⬇ Downloads" in der Seitenleiste
   und lassen sich dort auch ohne Netzwerkverbindung abspielen.
5. Abmelden über das ☰-Menü oben rechts in der Seitenleiste.

### Wo landen die Dateien?

| Zweck | Pfad |
|---|---|
| Einstellungen/Session | `~/.config/goldfish-linux/settings.json` |
| Heruntergeladene Videos | `~/.local/share/goldfish-linux/downloads/` |
| Download-Register (Metadaten) | `~/.local/share/goldfish-linux/downloads.json` |
| Poster-/Thumbnail-Cache | `~/.cache/goldfish-linux/posters/` |

## Architektur (kurz)

- **Sprache/Toolkit:** Python 3 + PyGObject (GTK4 + libadwaita). Kein
  Compile-Schritt, das `.deb` kopiert den Quellcode 1:1 nach
  `/usr/lib/python3/dist-packages/goldfish_linux/`.
- **HTTP-Client** (`goldfish_linux/api.py`): `requests.Session` für
  Cookie-basierte Server-Auth (identisch zum Browser-Login). Für die
  eigentliche Video-Wiedergabe (GStreamer/`Gtk.Video` trägt keine Cookies)
  wird stattdessen der `?session=<token>`-Query-Fallback genutzt, den der
  Server ursprünglich für Cast-Receiver (Chromecast/FireTV) bereitstellt.
- **Player** (`goldfish_linux/windows/player_window.py`): nutzt GTK4s
  eingebauten `Gtk.Video`-Widget (inkl. fertiger Play/Pause/Seek/Vollbild-
  Steuerleiste) — dahinter läuft GStreamer/`playbin`, das sowohl lokale
  Dateien (Downloads) als auch HTTP(S)-Streams/HLS-Playlists abspielen kann,
  vorausgesetzt die passenden GStreamer-Plugin-Pakete sind installiert
  (siehe Depends in `debian/control`).
- **Downloads** (`goldfish_linux/downloads.py`): lädt die Originaldatei über
  `/api/download/{id}` in einem Hintergrund-Thread herunter, hält eine
  kleine JSON-Registry (kein SQLite nötig für v1).
- **UI-Navigation:** `Adw.NavigationSplitView` (Seitenleiste) +
  `Adw.NavigationView` (Zurück-Navigation wird von libadwaita automatisch
  verwaltet) — deshalb die harte Mindestanforderung libadwaita ≥ 1.4.

## Bekannte Einschränkungen (v1)

Diese App ist bewusst als schlanker erster Wurf gebaut — analog dazu, wie
auch die Android- und Apple-Apps schrittweise gewachsen sind (siehe deren
Repos). Aktuell **nicht** enthalten:

- Kein Cast/AirPlay.
- Keine Staffel-Ansicht mit Poster+Cast-Leiste wie im Browser — Serien
  werden als normale Ordnerhierarchie durchsucht (Ordner → Unterordner →
  Videos), funktional nutzbar, aber ohne das reichhaltige TMDB-Layout der
  Web-UI.
- Kein Admin-Bereich (Bibliotheksverwaltung, Nutzerverwaltung, Scan-Steuerung
  etc.) — wie auch bei der Android-/iOS-App ist das bewusst reine
  Server-Admin-Aufgabe über den Browser.
- Kein Genre-/Auflösungs-Filter, keine Sortier-Auswahl (v1 sortiert immer
  nach Titel) — Suche pro Ordner funktioniert.
- Kein automatisches „Fortsetzen ab letzter Position" (Resume) beim erneuten
  Öffnen — `Gtk.Video` startet aktuell immer von vorn.
- **Nicht getestet auf Ubuntu 22.04/Linux Mint 21.x** (libadwaita 1.0 dort
  zu alt für `Adw.NavigationSplitView`/`Adw.NavigationView`/
  `Adw.ToolbarView`, die erst mit libadwaita 1.4 eingeführt wurden). Ein
  Downgrade der UI auf `Adw.Leaflet`/`Adw.HeaderBar` für ältere Systeme wäre
  technisch möglich, ist aber bewusst nicht Teil von v1.
- **Wichtig:** Der gesamte Code wurde sorgfältig gegen die echte
  Server-API geschrieben, konnte in dieser Entwicklungsumgebung aber
  **nicht auf einem echten Linux-Desktop mit GTK4 getestet werden** (die
  Entwicklung lief auf macOS ohne GTK4/libadwaita/GStreamer). Bitte nach der
  ersten Installation gegenprüfen und Probleme als GitHub-Issue melden —
  gerade Video-Wiedergabe (GStreamer-Plugin-Verfügbarkeit variiert je nach
  System) ist ein realistischer erster Stolperstein.

## Mitentwickeln / Fehler melden

Issues und PRs sind willkommen: <https://github.com/boernie77/goldfish-linux/issues>

Bei API-Änderungen am Server (`github.com/boernie77/goldfish`) bitte prüfen,
ob `goldfish_linux/api.py` noch zu den tatsächlichen Endpunkten passt —
analog zu den Kompatibilitäts-Hinweisen in den Android-/Apple-App-Repos.
