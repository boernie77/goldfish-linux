#!/usr/bin/env bash
# Baut das .deb-Paket lokal via dpkg-buildpackage.
#
# Voraussetzungen (auf Debian/Ubuntu/Mint per apt installierbar):
#   sudo apt install build-essential debhelper dpkg-dev
#
# dpkg-buildpackage legt das fertige .deb standardmäßig im PARENT-
# Verzeichnis des Repos ab — dieses Skript verschiebt es danach nach
# ./dist/, damit es im Repo bleibt.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "→ Baue .deb-Paket in $REPO_ROOT …"
dpkg-buildpackage -us -uc -b

mkdir -p dist
mv -f ../goldfish-linux_*.deb dist/ 2>/dev/null || true
mv -f ../goldfish-linux_*.buildinfo dist/ 2>/dev/null || true
mv -f ../goldfish-linux_*.changes dist/ 2>/dev/null || true

echo "→ Fertig. Paket(e) liegen in ./dist/:"
ls -la dist/*.deb
echo
echo "Installieren mit:  sudo apt install ./dist/goldfish-linux_*.deb"
