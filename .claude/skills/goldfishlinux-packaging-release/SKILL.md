---
name: goldfishlinux-packaging-release
description: "Use when packaging or releasing GoldfishLinux: .deb via debhelper, install.sh, release.yml, and keeping the three version locations in sync."
metadata:
  project: GoldfishLinux (boernie77/goldfish-linux)
  source: "CLAUDE.md-Aufteilung 2026-09-20"
---

# goldfishlinux-packaging-release

Aus der frueheren Sammel-CLAUDE.md des GoldfishLinux-Repos ausgelagerter Themenbereich (Zeichen: 800, Sektionen: 2). Volltext des Originals: Skill `goldfishlinux-full-archive`.

## Harte Regeln (zuerst lesen)

- Alle drei Versionsorte synchron halten: `goldfish_linux/__init__.py`, `pyproject.toml`, `debian/changelog` - kein automatischer Versions-Inject beim Build.
- Paketbau ist debhelper mit `--buildsystem=none` und nativem Source-Format `3.0 (native)` - kein pybuild/setuptools-Schritt.
- `release.yml` braucht `permissions: contents: write`, sonst scheitert `softprops/action-gh-release` am Standard-`GITHUB_TOKEN`.
- `install.sh` laedt das neueste GitHub-Release-`.deb` und installiert per `apt install`.

---

## Packaging

`.deb` via debhelper (`debian/rules` mit `--buildsystem=none`, native
Source-Format `3.0 (native)`) — kopiert `goldfish_linux/` 1:1 nach
`/usr/lib/python3/dist-packages/`, kein pybuild/setuptools-Build-Schritt.
`install.sh` lädt das aktuellste GitHub-Release-`.deb` und installiert per
`apt install`. GitHub-Actions-Workflow (`release.yml`) baut bei jedem
`v*`-Tag automatisch — braucht `permissions: contents: write` im
Workflow-YAML, sonst scheitert `softprops/action-gh-release` am
Standard-`GITHUB_TOKEN`.


## Versionierung

`goldfish_linux.__version__` in `__init__.py` + `pyproject.toml` +
`debian/changelog` müssen bei jedem Release synchron gehalten werden (kein
automatischer Versions-Inject beim Build). Versionsnummer ist auf der
Login-Seite + in der Seitenleiste sichtbar.

