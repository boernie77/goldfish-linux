"""Einstiegspunkt: `python3 -m goldfish_linux` bzw. das per .deb installierte
`/usr/bin/goldfish`-Skript ruft `main()` hier auf."""

from __future__ import annotations

import sys

from .app import GoldfishApplication


def main() -> int:
    app = GoldfishApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
