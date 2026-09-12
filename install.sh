#!/usr/bin/env bash
# Ein-Kommando-Installer für Goldfish Linux (Debian/Ubuntu/Linux Mint).
#
# Nutzung (in einem Terminal einfügen und Enter drücken):
#
#   curl -fsSL https://raw.githubusercontent.com/boernie77/goldfish-linux/main/install.sh | bash
#
# Was das Skript macht, Schritt für Schritt:
#   1. Prüft, ob dieses System überhaupt zu Goldfish Linux passt (apt vorhanden).
#   2. Lädt das aktuellste .deb-Paket von der GitHub-Releases-Seite herunter.
#   3. Installiert es per `apt install` — das löst alle Abhängigkeiten
#      (GTK4, libadwaita, GStreamer) automatisch mit auf. Dafür wird einmal
#      nach dem Admin-Passwort (sudo) gefragt.
#
# Nichts davon verändert etwas an bereits installierten Programmen — es wird
# nur EIN neues Paket ("goldfish-linux") zum System hinzugefügt.

set -euo pipefail

REPO="boernie77/goldfish-linux"

# -- kleine Ausgabe-Helfer --------------------------------------------------
info()  { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
ok()    { printf '\033[1;32m✓\033[0m %s\n' "$1"; }
fail()  { printf '\033[1;31m✗ Fehler:\033[0m %s\n' "$1" >&2; exit 1; }

echo "Goldfish Linux — Installation"
echo "=============================="
echo

# -- Schritt 1: Systemvoraussetzung prüfen ----------------------------------
if ! command -v apt >/dev/null 2>&1; then
  fail "Dieses Skript funktioniert nur auf Debian-basierten Systemen (Debian, \
Ubuntu, Linux Mint, …) mit dem Paketmanager 'apt'. Dein System scheint ein \
anderes zu sein."
fi
ok "Debian-basiertes System erkannt (apt gefunden)."

if ! command -v curl >/dev/null 2>&1; then
  info "'curl' fehlt noch — installiere es kurz nach (braucht dein Passwort) …"
  sudo apt-get update -qq
  sudo apt-get install -y curl
fi

# -- Schritt 2: aktuellstes .deb finden + herunterladen ---------------------
info "Suche aktuellstes Release von github.com/$REPO …"
API_URL="https://api.github.com/repos/$REPO/releases/latest"
API_RESPONSE="$(curl -fsSL "$API_URL")" || fail "Konnte GitHub nicht erreichen — \
prüfe deine Internetverbindung und versuche es erneut."

DEB_URL="$(printf '%s' "$API_RESPONSE" | grep -o '"browser_download_url": *"[^"]*\.deb"' | head -n1 | sed -E 's/.*"(https[^"]+)"/\1/')"
VERSION_TAG="$(printf '%s' "$API_RESPONSE" | grep -o '"tag_name": *"[^"]*"' | head -n1 | sed -E 's/.*"([^"]+)"$/\1/')"

if [ -z "$DEB_URL" ]; then
  fail "Kein .deb-Release gefunden unter https://github.com/$REPO/releases — \
entweder gibt es noch keine Veröffentlichung, oder GitHub hat gerade ein \
Problem. Alternative: Repo klonen und selbst bauen, siehe README.md, \
Abschnitt 'Aus dem Quellcode bauen'."
fi
ok "Release $VERSION_TAG gefunden."

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
TMP_DEB="$TMP_DIR/goldfish-linux.deb"

info "Lade Paket herunter …"
curl -fsSL "$DEB_URL" -o "$TMP_DEB" || fail "Download fehlgeschlagen. \
Prüfe deine Internetverbindung und versuche es erneut."
ok "Heruntergeladen ($(du -h "$TMP_DEB" | cut -f1))."

# -- Schritt 3: installieren -------------------------------------------------
info "Installiere Goldfish Linux — jetzt kommt die Passwort-Abfrage (sudo) …"
if ! sudo apt install -y "$TMP_DEB"; then
  echo
  fail "Die Installation ist fehlgeschlagen (siehe Fehlermeldung oben). \
Häufigste Ursache: 'sudo apt update' wurde lange nicht ausgeführt. Versuche:
    sudo apt update
und führe dieses Installationskommando danach noch einmal aus."
fi

echo
ok "Goldfish Linux ist installiert!"
echo
echo "Starten:"
echo "  • Über das Anwendungsmenü: nach \"Goldfish\" suchen, oder"
echo "  • Im Terminal:  goldfish"
echo
echo "Beim ersten Start werden Server-Adresse, Benutzername und Passwort"
echo "deines Goldfish-Servers abgefragt."
