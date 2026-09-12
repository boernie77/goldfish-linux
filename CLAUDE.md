# GoldfishLinux — natives Linux-Desktop-Client für Goldfish

Nativer Linux-Desktop-Client für den Goldfish-Server (separates Repo
`github.com/boernie77/goldfish`, lokal `~/Projekte/Videoplayer/`). Python 3
+ PyGObject (GTK4 + libadwaita), kein Electron/Tauri. Eigenes Git-Repo seit
2026-09-12: `github.com/boernie77/goldfish-linux` (öffentlich). Als `.deb`
für Debian 12+/Ubuntu 24.04+/Mint 22+ paketiert (ältere Systeme mit
libadwaita < 1.4 werden bewusst NICHT unterstützt).

**Bei jeder Server-API-Änderung prüfen:** `goldfish_linux/api.py` — nutzt
`/api/auth/login`, `/api/libraries`, `/api/libraries/{id}/folders`,
`/api/items`, `/api/playback/{id}` + den `?session=<token>`-Query-Fallback
(ursprünglich für Cast-Receiver gedacht) für die Video-Wiedergabe ohne
Cookie-Jar, `/api/download/{id}`, `/api/items/{id}/watched|favorite`.

**⚠ Memory-Hinweis:** Die volle, chronologische Bugfix-Historie (v0.1.1 bis
v0.1.6) wurde in Sessions erarbeitet, die im Server-Repo
(`~/Projekte/Videoplayer/`) liefen, und liegt dort als Memory
`project_feature_goldfish_linux` — pro Arbeitsverzeichnis gespeichert, eine
Session, die nur in diesem Repo arbeitet, sieht sie NICHT automatisch. Die
durable Fakten (Architektur-Entscheidungen, bekannte Gotchas, aktueller
Stand) sind deshalb unten in dieser CLAUDE.md dupliziert.

## 📍 Aktueller Stand (Stand: 2026-09-12)

- **Neueste veröffentlichte Version: v0.1.6** — Tag gesetzt, Release mit
  `.deb`-Asset live: https://github.com/boernie77/goldfish-linux/releases/tag/v0.1.6
- **⚠ OFFEN:** der v0.1.6-Fix (Startup-Crash nach v0.1.5) wurde **NICHT
  verifiziert** (auf macOS nicht reproduzierbar). Nächster Schritt, sobald
  am echten Linux-Rechner getestet wird:
  1. `curl -fsSL https://raw.githubusercontent.com/boernie77/goldfish-linux/main/install.sh | bash`
     erneut ausführen (zieht v0.1.6).
  2. App NICHT über das Menü, sondern im Terminal mit `goldfish` starten.
  3. Kompletten Terminal-Output hierher — das war beim Auffinden von v0.1.4
     bereits DER entscheidende Diagnoseschritt.
  4. Falls v0.1.6 startet: weiter durchklicken (Bibliotheken, Ordner,
     Abspielen, Download, Offline-Wiedergabe) — noch nie auf echtem Linux
     komplett verifiziert.

## Tech-Stack & Architektur-Entscheidungen

- **`Gtk.Video`-Widget** (eingebauter GTK4-Player, GStreamer-Backend) statt
  eigenem playbin-Wiring — bekommt Play/Pause/Seek/Vollbild gratis.
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
- `Adw.ToolbarView.add_top_bar`/`add_bottom_bar` NIE für beliebige Widgets
  nutzen, nur für tatsächliche Bar-artige Widgets (AdwHeaderBar/
  GtkActionBar/AdwTabBar) — ein einfaches `Gtk.Label` dort verursachte in
  v0.1.5 vermutlich einen Startup-Crash (v0.1.6 tauscht es gegen einen
  normalen `Gtk.Box`-Sibling).
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

## Packaging

`.deb` via debhelper (`debian/rules` mit `--buildsystem=none`, native
Source-Format `3.0 (native)`) — kopiert `goldfish_linux/` 1:1 nach
`/usr/lib/python3/dist-packages/`, kein pybuild/setuptools-Build-Schritt.
`install.sh` lädt das aktuellste GitHub-Release-`.deb` und installiert per
`apt install`. GitHub-Actions-Workflow (`release.yml`) baut bei jedem
`v*`-Tag automatisch — braucht `permissions: contents: write` im
Workflow-YAML, sonst scheitert `softprops/action-gh-release` am
Standard-`GITHUB_TOKEN`.

## ⚠ Wichtigste offene Lücke

Der komplette Code wurde auf macOS geschrieben **ohne jede Möglichkeit,
GTK4/libadwaita/GStreamer lokal zu testen** (kein `gi`-Modul verfügbar).
Nur statisch geprüft: `python3 -m py_compile` + `pyflakes`. **Vor jedem
produktiven Einsatz unbedingt auf einem echten Debian 12+/Ubuntu 24.04+/
Mint 22+ installieren und die komplette App-Kette durchklicken.**

## v1-Grenzen (bewusst)

Kein Cast/AirPlay, keine Staffel-Ansicht mit TMDB-Layout (nur normale
Ordner-Navigation), kein Admin-Bereich, kein Genre-/Auflösungs-Filter, kein
Resume ab letzter Position.

## Versionierung

`goldfish_linux.__version__` in `__init__.py` + `pyproject.toml` +
`debian/changelog` müssen bei jedem Release synchron gehalten werden (kein
automatischer Versions-Inject beim Build). Versionsnummer ist auf der
Login-Seite + in der Seitenleiste sichtbar.
