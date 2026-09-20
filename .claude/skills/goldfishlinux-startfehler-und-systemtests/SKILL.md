---
name: goldfishlinux-startfehler-und-systemtests
description: "Use when diagnosing GoldfishLinux launch bugs or when testing the app on the real machine - carries the proven system-check commands and the lesson about diagnoses on systems that cannot run GTK."
metadata:
  project: GoldfishLinux (boernie77/goldfish-linux)
  source: "CLAUDE.md-Aufteilung 2026-09-20"
---

# goldfishlinux-startfehler-und-systemtests

Aus der frueheren Sammel-CLAUDE.md des GoldfishLinux-Repos ausgelagerter Themenbereich (Zeichen: 1949, Sektionen: 2). Volltext des Originals: Skill `goldfishlinux-full-archive`.

## Harte Regeln (zuerst lesen)

- Diagnosen nie auf einem System stellen, das die Oberflaeche nicht ausfuehren kann - eine auf macOS entstandene Diagnose war nachweislich falsch.
- Exit-Code-Sprache: `timeout 20 python3 -m goldfish_linux` -> Exit 124 heisst 'Fenster lief', Exit 0 heisst 'App hat sich sofort beendet'.
- Fenster mit `wmctrl -lp` suchen (Titel genau `Goldfish`), Screenshots mit `gnome-screenshot -w -f <pfad>` nach `wmctrl -i -a <id>` - sonst landet ein fremdes Fenster im Bild.
- Fuer nur per Klick erreichbare Ansichten ein kurzes Treiberskript nutzen, das die Seiten direkt auf den Navigationsstapel legt.

---

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

