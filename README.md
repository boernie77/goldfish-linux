# Goldfish Linux

Nativer Desktop-Client für den selbstgehosteten
[Goldfish-Videoserver](https://github.com/boernie77/goldfish) — GTK4 +
libadwaita, als `.deb` für Debian, Ubuntu und Linux Mint.

[![Release](https://img.shields.io/github/v/release/boernie77/goldfish-linux?label=Release)](https://github.com/boernie77/goldfish-linux/releases/latest)
[![Lizenz](https://img.shields.io/github/license/boernie77/goldfish-linux)](LICENSE)

Filme, Serien, Privatvideos und Musik vom eigenen Server ansehen: Kacheln mit
TMDB-Postern, Staffelansicht, Besetzung, Tonspur- und Untertitelwahl,
Weiterschauen an der alten Stelle, Downloads für unterwegs — und dazu eigene
Festplatten als lokale Bibliothek, auch ganz ohne Server.

Die App gehört zur Goldfish-Familie neben den Clients für
[Android](https://github.com/boernie77/goldfish-android) und
[Apple-Plattformen](https://github.com/boernie77/goldfish-apple) und deckt
inzwischen im Wesentlichen denselben Funktionsumfang ab wie die Mac-App
(Ausnahmen unten unter „Bewusst nicht enthalten").

---

## Funktionen

### Bibliotheken & Navigation

- Alle Bibliotheken des Servers in der Seitenleiste; welche dort und auf der
  Startseite erscheinen, ist pro Benutzer einstellbar.
- **Kachelraster mit TMDB-Postern**, Auflösungs- und Laufzeit-Kennzeichnung,
  Gesehen-Haken und Favoritenherz direkt auf der Kachel.
- **Startseite** mit „Fortsetzen" und „Als nächstes" je in einer Zeile über
  alle Bibliotheken hinweg, darunter „Neu hinzugefügt" je Bibliothek.
- **Staffelansicht für Serien** mit Poster, Beschreibung, Besetzung und
  „x von y Folgen" pro Staffel; fehlende Folgen sind erkennbar.
- **Sammlungen** (James Bond, Star Wars …) inklusive der Teile, die noch
  fehlen, mit „✓ komplett" für vollständige Reihen.
- **Playlists** anlegen, füllen, abspielen und gemischt abspielen — getrennt
  für Video und Musik, wie auf dem Server.
- **Personenseite:** ein Klick auf einen Schauspieler zeigt alles mit ihm,
  quer über alle Bibliotheken.
- **Ordnernavigation** wie im Browser, inklusive Drilldown-Ordner — dieselben
  drei Fälle, damit nie Ordnerkacheln und ihre Dateien doppelt erscheinen.
- **Buchstabenleiste A–Z** am rechten Rand für lange Listen.

### Suchen, Filtern, Sortieren

- Suche über Titel **und** Schauspielernamen (der Server durchsucht beides).
- Sortieren nach Titel, Dateiname, Veröffentlichung, Hinzugefügt, Zuletzt
  abgespielt, Laufzeit, Dateigröße, Bewertung, Auflösung — bei Musik zusätzlich
  Künstler und Album; Richtung umschaltbar.
- Filter für Gesehen-Status, Favoriten, Auflösung (acht Stufen von 4K bis
  360p) und Genre.
- Die gewählte Sortierung bleibt pro Bibliothek und Ordner erhalten.

### Detailansicht

- Poster, Beschreibung, Jahr, Genres, Bewertung, Laufzeit, Auflösung,
  Dateigröße und FSK-Kennzeichnung.
- **Besetzung mit Fotos**, anklickbar → Personenseite.
- **Trailer** ansehen (bei Filmen mit TMDB-Zuordnung).
- **Tonspur, Untertitel und Qualität wählen** — inklusive der serverseitig
  erzeugten Untertitel (Whisper-KI und OCR).
- Liegt ein Titel in mehreren Fassungen vor, lässt sich die Version auswählen.
- Gesehen markieren, als Favorit merken, zu einer Playlist hinzufügen,
  herunterladen.

### Wiedergabe

- **Direkte Wiedergabe** ohne Umwandlung, wo möglich — GStreamer spielt MKV,
  MP4, AVI, WMV, HEVC, H.264, VP9, AV1, AC3, DTS und E-AC3 selbst (getestet
  bis 4K-HEVC mit TrueHD Atmos 7.1).
- **Serverseitige Umwandlung (HLS)**, wenn nötig oder wenn eine kleinere
  Qualitätsstufe gewählt ist; Springen funktioniert dabei über den ganzen Film.
- Eigene Steuerleiste mit Fortschritt, Lautstärke, Vollbild, Tonspur- und
  Untertitelwahl.
- **Weiterschauen an der alten Stelle** mit Rückfrage „von Anfang oder
  fortsetzen"; die Position wird an den Server zurückgemeldet und gilt damit
  auch in allen anderen Clients.
- **Vorschaubilder beim Spulen** (Trickplay-Sprites des Servers).
- **Untertitel im Bild** (WebVTT, eingebettet oder erzeugt).
- **Zufallswiedergabe** pro Bibliothek — der Bereich folgt dem, was gerade
  offen ist.
- Nächster Titel läuft automatisch weiter (Playlist, Album, Zufall).

### Musik

- **Drei Ansichten, ein Klick auseinander:** Alben als Kacheln, Alben als
  Liste, alle Titel der Bibliothek — Suche nach Titel, Künstler oder Album;
  der Ordner-Browser bleibt erreichbar.
- **Listen sind echte Tabellen:** Spaltenbreite ziehen, Reihenfolge am
  Spaltenkopf verschieben, Kopfklick sortiert. Welche Spalten erscheinen
  (Album, Künstler, Genre, Jahr, Dauer, Zuletzt gehört, Wiedergaben,
  Hinzugefügt …), wählt ein eigenes „Spalten"-Menü; Breite, Reihenfolge und
  Auswahl bleiben gemerkt.
- Eigener Filter für „nur Favoriten" und Genre, dazu die Anzahl der
  gezeigten Alben bzw. Titel.
- Albumansicht mit Titelliste, Dauer, Genre, Jahr und Favoritenherz.
- **Abspielleiste am unteren Fensterrand**, die beim Navigieren stehen bleibt.
- Warteschlange ansehen, anspringen, einzelne Titel entfernen, mischen.
- Alben als Favorit merken, offline mitnehmen, Musik-Playlists.
- Musik läuft immer direkt — auch FLAC und WAV, ohne Umwandlung.

### Eigene Datenträger (ohne Server)

- Ordner oder angeschlossene Festplatten als **lokale Bibliothek** einrichten.
- Einlesen ermittelt Laufzeit und Auflösung je Datei über GStreamer (gemessen:
  122 Videos in anderthalb Sekunden) — kein ffmpeg nötig.
- **Mehrere Datenträger zu einem Eintrag zusammenlegen**: erscheinen als eine
  Bibliothek, gemeinsam durchsuchbar, jederzeit wieder trennbar.
- **Doppelte Dateien finden** (gleiche Größe und Laufzeit), auch über mehrere
  zusammengelegte Platten hinweg.
- Eine abgezogene Platte behält ihren Bestand in der Übersicht und ist als
  „gerade nicht angeschlossen" gekennzeichnet. Entfernen betrifft nur die
  App — auf der Platte wird nichts gelöscht.

### Downloads & Offline

- Videos und ganze Alben herunterladen, mit Fortschritt und Abbrechen.
- Wahlweise **kleiner als das Original** (dieselben Qualitätsstufen wie beim
  Streamen).
- Heruntergeladene Titel spielen auch ohne Netzwerkverbindung.

### Konto & Einstellungen

- Anmeldung mit Benutzername und Passwort; die Sitzung bleibt über Neustarts
  erhalten (das Passwort wird nie gespeichert).
- **Single-Sign-on über Authentik** (OIDC), wenn ein eingebetteter Browser
  vorhanden ist — WebKit ist eine Empfehlung, keine Voraussetzung.
- Eigenes Passwort ändern.
- **Gesehen-Status mit einem zweiten Konto teilen** (anfragen, bestätigen,
  trennen).
- Startseite und Reiterleiste selbst zusammenstellen.

---

## 🚀 Installation für Einsteiger (Linux Mint, Ubuntu, Debian)

Diese Anleitung setzt **keinerlei Vorwissen** voraus — jeder Befehl, der
nötig ist, steht hier. Du brauchst nur ein Terminal-Fenster.

### Schritt 1: Terminal öffnen

- **Linux Mint (Cinnamon):** Tastenkombination `Strg` + `Alt` + `T`, oder im
  Startmenü nach „Terminal" suchen.
- **Ubuntu:** genauso, `Strg` + `Alt` + `T`.

Es öffnet sich ein schwarzes Fenster mit einer blinkenden Eingabezeile —
dort werden die folgenden Befehle eingegeben.

### Schritt 2: Installationsbefehl ausführen

Diesen kompletten Befehl in das Terminal **hineinkopieren** (markieren,
`Strg`+`Umschalt`+`C` zum Kopieren aus dieser Anleitung, dann im Terminal mit
`Strg`+`Umschalt`+`V` einfügen) und mit `Enter` bestätigen:

```bash
curl -fsSL https://raw.githubusercontent.com/boernie77/goldfish-linux/main/install.sh | bash
```

Was jetzt passiert:

1. Das Skript prüft kurz, ob dein System passt (Debian/Ubuntu/Mint).
2. Es lädt automatisch die aktuellste Version von Goldfish Linux herunter.
3. Es fragt einmal nach deinem **Benutzer-Passwort** (nicht sichtbar beim
   Tippen — das ist normal bei Linux-Terminals, einfach tippen und `Enter`
   drücken). Das ist nötig, um Software zu installieren (`sudo`).
4. Danach installiert es Goldfish Linux inklusive aller benötigten
   Zusatzprogramme (GTK4, libadwaita, GStreamer-Videocodecs) automatisch.

Am Ende erscheint:

```
✓ Goldfish Linux ist installiert!
```

### Schritt 3: App starten

Zwei Möglichkeiten:

- **Über das Anwendungsmenü:** unten links (oder wo dein Startmenü liegt)
  öffnen, „Goldfish" eintippen, anklicken.
- **Direkt im Terminal:**
  ```bash
  goldfish
  ```

Beim allerersten Start fragt die App:

- **Server-Adresse** deines Goldfish-Servers, z. B. `http://192.168.1.50:8098`
  oder `https://goldfish.example.com` (die Adresse, unter der du Goldfish
  sonst im Browser öffnest).
- **Benutzername** und **Passwort** deines Goldfish-Kontos — oder
  „Mit Single-Sign-on anmelden", falls dein Server Authentik nutzt.

Danach bist du drin: links eine Bibliothek anklicken, ein Video anklicken →
„▶ Abspielen".

### Update auf eine neuere Version

Denselben Befehl aus Schritt 2 erneut ausführen — er holt immer das aktuelle
Release und installiert darüber. Einstellungen, Anmeldung und Downloads
bleiben erhalten.

### Falls etwas nicht klappt

**„curl: command not found"** — sehr selten, aber falls es passiert:
```bash
sudo apt update
sudo apt install curl
```
und den Befehl aus Schritt 2 danach erneut ausführen.

**„Unable to locate package" / Installation schlägt fehl** — die
Paketliste deines Systems ist veraltet. Einmal auffrischen:
```bash
sudo apt update
```
und den Befehl aus Schritt 2 danach erneut ausführen.

**Die App startet, aber Videos spielen nicht ab (schwarzes Bild, kein
Ton)** — meistens fehlen einzelne GStreamer-Codec-Pakete. Im Terminal
nachinstallieren:
```bash
sudo apt install gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-libav
```
Danach die App einmal komplett schließen und neu öffnen. Hilft das nicht,
bitte als [GitHub-Issue](https://github.com/boernie77/goldfish-linux/issues)
melden — am besten mit der Ausgabe von `goldfish` bei Start im Terminal.

**„Mit Single-Sign-on anmelden" fehlt** — dafür braucht es den eingebetteten
Browser:
```bash
sudo apt install gir1.2-webkit-6.0
```
Die Anmeldung mit Benutzername und Passwort funktioniert auch ohne.

**„Diese Distribution wird nicht unterstützt" / die Navigationsleiste sieht
kaputt aus oder die App stürzt beim Start ab** — dein System hat eine zu
alte libadwaita-Version (unter 1.4). Das betrifft ältere Systeme wie
**Ubuntu 22.04 LTS** oder **Linux Mint 21.x**. Version prüfen:
```bash
apt list --installed 2>/dev/null | grep libadwaita
```
Zeigt die Ausgabe eine Version unter `1.4`, ist dein System für Goldfish
Linux (aktuell) leider zu alt — siehe „Systemvoraussetzungen".

**„Verbindung fehlgeschlagen: … Read timed out"** beim Anmelden — die App
versucht es automatisch ein zweites Mal (manche selbstgehosteten Server
brauchen für den allerersten Request nach einer Weile Inaktivität spürbar
länger). Passiert es weiterhin:
- Prüfe, ob die Server-Adresse im Browser vom selben Rechner aus erreichbar
  ist (dieselbe Adresse in einen Browser-Tab eingeben).
- Bist du im selben Heimnetz wie der Goldfish-Server? Dann probiere statt
  der öffentlichen Adresse (z. B. `https://goldfish.example.com`) direkt die
  lokale Netzwerkadresse (z. B. `http://192.168.1.50:8098`) — manche Router
  haben Probleme damit, eine eigene öffentliche Domain aus dem eigenen Netz
  heraus aufzulösen („NAT-Hairpinning").

**„Connection reset"** (nicht Timeout) gegen einen Server mit DynDNS — meist
ein veraltetes IPv6-Präfix im DNS. Prüfen mit `dig AAAA <domain>`; die App
weicht in diesem Fall auf IPv4 aus.

### App wieder deinstallieren

```bash
sudo apt remove goldfish-linux
```

Persönliche Daten (Server-Login, heruntergeladene Videos) bleiben dabei
erhalten, für den Fall, dass du die App später neu installierst. Komplett
entfernen inklusive dieser Daten:
```bash
sudo apt remove goldfish-linux
rm -rf ~/.config/goldfish-linux ~/.local/share/goldfish-linux ~/.cache/goldfish-linux
```

---

## Systemvoraussetzungen

- Ein laufender [Goldfish-Server](https://github.com/boernie77/goldfish) —
  jede halbwegs aktuelle Installation reicht. Für die lokalen Bibliotheken
  (eigene Festplatten) braucht es gar keinen Server.
- **Debian 12 (Bookworm) oder neuer**, **Ubuntu 24.04 LTS oder neuer**,
  **Linux Mint 22 oder neuer** — bzw. jede andere Distribution mit
  **libadwaita ≥ 1.4** und **GTK4 ≥ 4.10**.
- Ubuntu 22.04 LTS und Linux Mint 21.x werden **nicht** unterstützt
  (libadwaita 1.0 dort ist zu alt für `Adw.NavigationSplitView`).
- Optional: `gir1.2-webkit-6.0` für die Single-Sign-on-Anmeldung.

## Andere Installationswege

### Manuell (ohne das install.sh-Skript)

1. `.deb`-Datei von der [Releases-Seite](https://github.com/boernie77/goldfish-linux/releases)
   im Browser herunterladen.
2. Im Ordner mit der heruntergeladenen Datei ein Terminal öffnen (im
   Dateimanager meist Rechtsklick → „Im Terminal öffnen") und:
   ```bash
   sudo apt install ./goldfish-linux_*.deb
   ```

### Aus dem Quellcode bauen

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

### Direkt aus dem Repo starten (Entwicklung)

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

## Wo landen die Dateien?

| Zweck | Pfad |
|---|---|
| Einstellungen/Sitzung | `~/.config/goldfish-linux/settings.json` |
| Sortierung/Ansichten je Bibliothek | `~/.config/goldfish-linux/view_prefs.json` |
| Eigene Datenträger (lokale Bibliotheken) | `~/.config/goldfish-linux/local_libraries.json` |
| Heruntergeladene Videos und Musik | `~/.local/share/goldfish-linux/downloads/` |
| Download-Register (Metadaten) | `~/.local/share/goldfish-linux/downloads.json` |
| Poster-/Cover-Zwischenspeicher | `~/.cache/goldfish-linux/posters/` |

## Architektur (kurz)

- **Sprache/Toolkit:** Python 3 + PyGObject (GTK4 + libadwaita), rund 8500
  Zeilen. Kein Compile-Schritt: das `.deb` kopiert den Quellcode 1:1 nach
  `/usr/lib/python3/dist-packages/goldfish_linux/`.
- **HTTP-Client** (`goldfish_linux/api.py`): `requests.Session` mit
  Cookie-Auth, identisch zum Browser-Login; deckt rund 80 Endpunkte ab. Für
  die Wiedergabe (GStreamer trägt keine Cookies) wird der
  `?session=<token>`-Fallback genutzt, den der Server ursprünglich für
  Cast-Empfänger bereitstellt.
- **Player** (`windows/player_window.py`): `Gtk.MediaFile` als Paintable in
  einem `Gtk.Picture` mit **eigener** Steuerleiste. `Gtk.Video` wäre einfacher,
  ist aber eine Sackgasse, sobald mehr als Play/Pause gebraucht wird — seine
  Steuerleiste ist fest eingebaut und von außen nicht erreichbar (keine
  Untertitel über dem Bild, keine Vorschaubilder am Fortschrittsbalken).
- **Lange Listen** laufen über `Gtk.GridView` mit Widget-Recycling, nicht über
  `Gtk.FlowBox`: 2717 Album-Kacheln kosteten dort gemessen 4,65 Sekunden
  blockierten Hauptablauf.
- **Lokale Bibliotheken** (`local_library.py`) lesen Laufzeit und Auflösung
  über `GstPbutils.Discoverer` ein — ffprobe/ffmpeg sind auf einem
  Desktopsystem nicht zwingend installiert, GStreamer dagegen schon.
- **Downloads** (`downloads.py`): Hintergrund-Thread plus kleine
  JSON-Registry, kein SQLite.
- **Navigation:** `Adw.NavigationSplitView` (Seitenleiste) +
  `Adw.NavigationView` (Zurück-Navigation von libadwaita verwaltet) — daher
  die harte Mindestanforderung libadwaita ≥ 1.4.

## Bewusst nicht enthalten

- **Cast und AirPlay** — auf Linux ohne Entsprechung.
- **Ein eigenes Fenster pro Video** — eine macOS-Eigenheit; hier läuft der
  Player im Hauptfenster.
- **Serververwaltung** (Bibliotheken anlegen, Benutzer, Scan, Trickplay,
  Whisper …) — bleibt wie in allen Goldfish-Clients dem Browser überlassen.
- **Formatanpassung lokaler Dateien** — gegenstandslos, GStreamer spielt hier
  alles direkt ab.
- **Vorlaufpuffer-Regler für langsame Platten** — bei GStreamer ohne
  erkennbaren Nutzen.
- Alle gesehenen Downloads auf einmal löschen (einzeln geht).

## Bekannte Lücken

- Auf der **Personenseite** erscheinen bisher nur die tatsächlich vorhandenen
  Titel; die vollständige TMDB-Filmografie (mit ausgegrauten, nicht
  vorhandenen Filmen) fehlt noch.
- Die **Single-Sign-on-Anmeldung** ist bis zum Laden der Authentik-Seite
  geprüft, aber nicht bis zum Ende durchgespielt.

## Mitentwickeln / Fehler melden

Issues und PRs sind willkommen:
<https://github.com/boernie77/goldfish-linux/issues>

Hilfreich bei einem Fehlerbericht: die genaue Fehlermeldung und die Ausgabe
von `goldfish` bei Start im Terminal (statt über das Menü).

Bei API-Änderungen am Server (`github.com/boernie77/goldfish`) bitte prüfen,
ob `goldfish_linux/api.py` noch zu den tatsächlichen Endpunkten passt —
analog zu den Kompatibilitäts-Hinweisen in den Android-/Apple-App-Repos.
Architekturhinweise und Fallstricke für Mitentwickler stehen in
[CLAUDE.md](CLAUDE.md).

## Lizenz

MIT — siehe [LICENSE](LICENSE).
