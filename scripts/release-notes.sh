#!/usr/bin/env bash
# Baut den Text für die GitHub-Release-Seite aus debian/changelog.
#
# Der oberste Changelog-Eintrag (bis zur "-- Maintainer"-Zeile) ist bereits in
# ganzen, für Benutzer verständlichen Sätzen geschrieben — der wird als
# "Neu in dieser Version" übernommen, damit die Release-Seite nicht nur einen
# "Full Changelog"-Link zeigt. Davor steht der Installationsbefehl, weil das
# die häufigste Frage eines Besuchers ist.
#
# Aufruf: scripts/release-notes.sh > RELEASE_NOTES.md
set -euo pipefail

cd "$(dirname "$0")/.."

version="$(dpkg-parsechangelog -S Version)"
deb="goldfish-linux_${version}_all.deb"

cat <<EOF
## Installation

Neu installieren **oder aktualisieren** — diesen Befehl in ein Terminal
einfügen (\`Strg\`+\`Alt\`+\`T\`):

\`\`\`bash
curl -fsSL https://raw.githubusercontent.com/boernie77/goldfish-linux/main/install.sh | bash
\`\`\`

Alternativ die \`.deb\` unten herunterladen und im Download-Ordner:

\`\`\`bash
sudo apt install ./${deb}
\`\`\`

Einstellungen, Anmeldung und Downloads bleiben bei einem Update erhalten.

## Neu in dieser Version

EOF

# Erster Changelog-Eintrag ohne Kopf- und Signaturzeile; die Einrückung von
# debhelper (zwei Leerzeichen vor "*") wird zu normalen Markdown-Listen.
awk 'NR>1 && /^ -- /{exit} NR>2{print}' debian/changelog \
  | sed -e 's/^  \* /- /' -e 's/^    / /' \
  | sed '/^[[:space:]]*$/d'

cat <<'EOF'

---

**Voraussetzungen:** Debian 12+, Ubuntu 24.04+, Linux Mint 22+ (libadwaita
1.4 oder neuer). Ubuntu 22.04 und Mint 21.x werden nicht unterstützt.
EOF
