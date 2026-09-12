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


def format_count(value: int) -> str:
    """Tausenderpunkte wie im Deutschen: 18574 wird zu "18.574".

    Bewusst von Hand und nicht über `locale`: die App setzt nirgends eine
    Locale, und `f"{n:n}"` liefert ohne gesetzte Locale wieder die nackte
    Zahl."""
    text = f"{int(value):,}".replace(",", ".")
    return text


def format_date(iso: str | None) -> str:
    """Ein ISO-Zeitstempel des Servers als Tagesdatum, z. B. "08.09.2026".

    Der Server liefert `releasedAt` als vollen Zeitstempel ("2026-09-08T00:00:
    00Z"); die Uhrzeit ist dabei bedeutungslos — sie kommt je nach Quelle aus
    einem Datei-Tag, einem yt-dlp-`DATE`-Feld oder schlicht der Änderungszeit
    der Datei. Deshalb nur das Datum."""
    if not iso or not isinstance(iso, str) or len(iso) < 10:
        return ""
    year, month, day = iso[:4], iso[5:7], iso[8:10]
    if not (year.isdigit() and month.isdigit() and day.isdigit()):
        return ""
    return f"{day}.{month}.{year}"


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


# ISO-639-2/B-Codes, wie ffprobe sie in den Stream-Metadaten liefert (also
# "ger" und nicht "deu"). Bewusst nur die Sprachen, die in dieser Sammlung
# vorkommen können — ein unbekannter Code wird unverändert groß geschrieben
# gezeigt, das ist ehrlicher als ihn zu verschweigen.
_LANGUAGES = {
    "ger": "Deutsch", "deu": "Deutsch", "de": "Deutsch",
    "eng": "Englisch", "en": "Englisch",
    "fre": "Französisch", "fra": "Französisch", "fr": "Französisch",
    "ita": "Italienisch", "it": "Italienisch",
    "spa": "Spanisch", "es": "Spanisch",
    "dut": "Niederländisch", "nld": "Niederländisch", "nl": "Niederländisch",
    "pol": "Polnisch", "pl": "Polnisch",
    "rus": "Russisch", "ru": "Russisch",
    "tur": "Türkisch", "tr": "Türkisch",
    "jpn": "Japanisch", "ja": "Japanisch",
    "kor": "Koreanisch", "ko": "Koreanisch",
    "chi": "Chinesisch", "zho": "Chinesisch", "zh": "Chinesisch",
    "por": "Portugiesisch", "pt": "Portugiesisch",
    "swe": "Schwedisch", "sv": "Schwedisch",
    "dan": "Dänisch", "da": "Dänisch",
    "nor": "Norwegisch", "no": "Norwegisch",
    "fin": "Finnisch", "fi": "Finnisch",
    "cze": "Tschechisch", "ces": "Tschechisch", "cs": "Tschechisch",
    "hun": "Ungarisch", "hu": "Ungarisch",
    "gre": "Griechisch", "ell": "Griechisch", "el": "Griechisch",
    "heb": "Hebräisch", "he": "Hebräisch",
    "ara": "Arabisch", "ar": "Arabisch",
    "hin": "Hindi", "hi": "Hindi",
    "tha": "Thai", "th": "Thai",
    "und": "Unbekannt",
}

# Untertitel-Formate, die als Bild vorliegen. Sie lassen sich nicht in Text
# wandeln (der Server antwortet auf einen Abruf mit 415) und können daher auch
# nicht eingeblendet werden — im Auswahlmenü haben sie nichts zu suchen.
BITMAP_SUBTITLE_CODECS = frozenset({"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub", "vobsub", "pgssub"})


def language_name(code: str | None) -> str:
    if not code:
        return "Unbekannt"
    return _LANGUAGES.get(code.lower(), code.upper())


def format_channels(channels: int) -> str:
    """Kanalzahl als gängige Bezeichnung: 2 wird Stereo, 6 wird 5.1."""
    return {0: "", 1: "Mono", 2: "Stereo", 3: "2.1", 6: "5.1", 7: "6.1", 8: "7.1"}.get(channels, f"{channels} Kanäle" if channels else "")


def audio_stream_label(stream: dict) -> str:
    """Eine Tonspur so beschriften, dass die Wahl ohne Nachdenken klar ist:
    Sprache zuerst, dann Format und Kanäle."""
    parts = [language_name(stream.get("language"))]
    codec = (stream.get("codec") or "").upper()
    if codec:
        parts.append(codec)
    channels = format_channels(stream.get("channels") or 0)
    if channels:
        parts.append(channels)
    label = " · ".join(parts)
    if stream.get("isDefault"):
        label += "  (Standard)"
    return label


def subtitle_stream_label(stream: dict) -> str:
    """Untertitel beschriften. Erzeugte Spuren behalten die Kennzeichnung des
    Servers (🎤 für Whisper, 📝 für OCR), damit erkennbar bleibt, dass sie
    maschinell entstanden sind."""
    title = stream.get("title") or ""
    if title.startswith(("🎤", "📝")):
        return title
    parts = [language_name(stream.get("language"))]
    if stream.get("isForced"):
        parts.append("erzwungen")
    if title and title.lower() not in ("forced", "full"):
        parts.append(title)
    return " · ".join(parts)


def is_displayable_subtitle(stream: dict) -> bool:
    return (stream.get("codec") or "").lower() not in BITMAP_SUBTITLE_CODECS
