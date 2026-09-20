---
name: goldfishlinux-gtk-architecture
description: "Use when touching GoldfishLinux's architecture: player widget, session auth, libadwaita minimum, widget parenting, folder API convention, background-thread error handling, DynDNS/IPv6, icon, VAAPI."
metadata:
  project: GoldfishLinux (boernie77/goldfish-linux)
  source: "CLAUDE.md-Aufteilung 2026-09-20"
---

# goldfishlinux-gtk-architecture

Aus der frueheren Sammel-CLAUDE.md des GoldfishLinux-Repos ausgelagerter Themenbereich (Zeichen: 4207, Sektionen: 1). Volltext des Originals: Skill `goldfishlinux-full-archive`.

## Harte Regeln (zuerst lesen)

- Player = `Gtk.MediaFile` als Paintable in `Gtk.Picture` mit eigener Steuerleiste; `Gtk.Video` ist eine Sackgasse und wird nicht zurueckgeholt.
- Nur EIN Wiedergabefenster; Abgeloeste Medien abbauen UND abklemmen; in der Steuerleiste darf kein Kind seine Sichtbarkeit aendern.
- `folder=""` bedeutet in `ListItems` 'kein Filter' - fuer 'nur Wurzelebene' immer `"/"` verwenden.
- In Hintergrund-Faeden immer breit fangen (`except Exception`) und sichtbar melden, sonst haengt der Spinner fuer immer.
- Icon und Branding kommen aus dem Server-Repo (`favicon.svg`), nicht selbst zeichnen; `Adw.Spinner` ist tabu (braucht libadwaita >= 1.6).

---

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

