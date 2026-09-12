"""Kleine Formatierungs-Helfer für Größen/Laufzeiten in der UI."""

from __future__ import annotations


def format_size(size_bytes: float) -> str:
    if size_bytes <= 0:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return ""
    total = int(seconds + 0.5)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# Die Stufen des Servers (internal/store/items.go, ResBuckets-Filter). Gleiche
# Grenzen wie dort, damit ein per Filter gewähltes "720p" auch auf der Kachel
# als 720p steht. Die Formel max(height, width*9/16) fängt Breitwandformate:
# ein 1920x800-Film ist 1080p, nicht 720p.
_BUCKETS = (
    (2000, "4K"),
    (1400, "2K"),
    (1000, "1080p"),
    (700, "720p"),
    (540, "576p"),
    (500, "540p"),
    (440, "480p"),
)


def format_resolution(width: int, height: int) -> str:
    if not height:
        return ""
    effective = max(height, int(width * 9 / 16)) if width else height
    for limit, label in _BUCKETS:
        if effective >= limit:
            return label
    return "360p"
