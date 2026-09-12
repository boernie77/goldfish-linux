#!/usr/bin/env bash
# Ein-Kommando-Installer für Goldfish Linux (Debian/Ubuntu/Mint).
#
# Nutzung:
#   curl -fsSL https://raw.githubusercontent.com/boernie77/goldfish-linux/main/install.sh | bash
#
# Lädt das aktuellste .deb-Release von GitHub und installiert es per
# `apt install` (löst Abhängigkeiten — GTK4, libadwaita, GStreamer — dabei
# automatisch mit auf). Erfordert sudo-Rechte für die Installation.

set -euo pipefail

REPO="boernie77/goldfish-linux"

if ! command -v apt >/dev/null 2>&1; then
  echo "Fehler: dieses Installationsskript funktioniert nur auf Debian-basierten" >&2
  echo "Systemen (Debian, Ubuntu, Linux Mint, …) mit apt." >&2
  exit 1
fi

echo "→ Suche aktuellstes Release von $REPO …"
API_URL="https://api.github.com/repos/$REPO/releases/latest"
DEB_URL="$(curl -fsSL "$API_URL" | grep -o '"browser_download_url": *"[^"]*\.deb"' | head -n1 | sed -E 's/.*"(https[^"]+)"/\1/')"

if [ -z "$DEB_URL" ]; then
  echo "Fehler: Kein .deb-Release gefunden." >&2
  echo "Entweder gibt es noch keine Veröffentlichung, oder GitHub war nicht erreichbar." >&2
  echo "Alternative: Repo klonen und selbst bauen — siehe README.md, Abschnitt" >&2
  echo "\"Aus dem Quellcode bauen\"." >&2
  exit 1
fi

TMP_DEB="$(mktemp --suffix=.deb)"
trap 'rm -f "$TMP_DEB"' EXIT

echo "→ Lade $DEB_URL …"
curl -fsSL "$DEB_URL" -o "$TMP_DEB"

echo "→ Installiere (benötigt sudo) …"
sudo apt install -y "$TMP_DEB"

echo
echo "✓ Goldfish Linux installiert. Im Anwendungsmenü als \"Goldfish\" zu finden,"
echo "  oder direkt per Terminal starten mit:  goldfish"
