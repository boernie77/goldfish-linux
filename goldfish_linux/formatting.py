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


def format_resolution(width: int, height: int) -> str:
    if not height:
        return ""
    effective = max(height, int(width * 9 / 16)) if width else height
    if effective >= 2000:
        return "4K"
    if effective >= 1400:
        return "2K"
    if effective >= 900:
        return "1080p"
    if effective >= 600:
        return "720p"
    if effective >= 500:
        return "576p"
    return f"{effective}p"
