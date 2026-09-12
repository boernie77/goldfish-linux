"""WebVTT lesen und zur Wiedergabezeit den passenden Text finden.

Der Server liefert Untertitel immer als WebVTT — eingebettete Textspuren wandelt
er per ffmpeg um, die von Whisper und OCR erzeugten liegen ohnehin so vor. Das
Einblenden übernimmt die App selbst: das Videowidget kennt keine Untertitel,
also wird der Text als eigene Ebene über das Bild gelegt. Genauso löst es die
Mac-App, und im Browser ist es dieselbe Konstruktion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# "HH:MM:SS.mmm" oder "MM:SS.mmm", beides kommt vor.
_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
# Auszeichnungen, die in VTT vorkommen können: <i>, <b>, <v Sprecher>, <c.farbe>.
_TAG = re.compile(r"</?[^>]+>")


@dataclass
class Cue:
    start: float
    end: float
    text: str


def _parse_time(raw: str) -> float | None:
    match = _TIME.search(raw)
    if match is None:
        return None
    hours, minutes, seconds, millis = match.groups()
    return (
        int(hours or 0) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(millis.ljust(3, "0")) / 1000
    )


def parse_vtt(text: str) -> list[Cue]:
    """Zerlegt WebVTT in Einblendungen.

    Bewusst nachsichtig: Kopfzeilen, Kommentare, Stilangaben, Nummern vor der
    Zeitzeile und Positionsangaben dahinter werden übersprungen. Eine einzelne
    unlesbare Stelle darf nicht die ganze Spur unbrauchbar machen — dann fehlt
    lieber ein Satz als jeder.
    """
    cues: list[Cue] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "-->" not in line:
            i += 1
            continue
        left, _, right = line.partition("-->")
        start, end = _parse_time(left), _parse_time(right)
        i += 1
        if start is None or end is None:
            continue
        # Alles bis zur nächsten Leerzeile ist der Text dieser Einblendung.
        parts: list[str] = []
        while i < len(lines) and lines[i].strip():
            parts.append(_TAG.sub("", lines[i]).strip())
            i += 1
        body = "\n".join(p for p in parts if p)
        if body:
            cues.append(Cue(start=start, end=end, text=body))
    cues.sort(key=lambda c: c.start)
    return cues


class SubtitleTrack:
    """Hält die Einblendungen und beantwortet "was gilt bei Sekunde X?".

    Merkt sich die zuletzt getroffene Stelle, weil die Frage bei der Wiedergabe
    viermal pro Sekunde kommt und die Zeit dabei fast immer vorwärts läuft —
    dann genügt ein Blick auf die Nachbarschaft statt einer Suche über
    tausende Einträge.
    """

    def __init__(self, cues: list[Cue]) -> None:
        self.cues = cues
        self._last = 0

    def __len__(self) -> int:
        return len(self.cues)

    def text_at(self, seconds: float) -> str:
        if not self.cues:
            return ""
        idx = self._last
        if idx >= len(self.cues):
            idx = 0
        # Vorwärts, solange die aktuelle Einblendung schon vorbei ist.
        while idx < len(self.cues) - 1 and self.cues[idx].end < seconds:
            idx += 1
        # Rückwärts, falls gesprungen wurde.
        while idx > 0 and self.cues[idx].start > seconds:
            idx -= 1
        self._last = idx
        cue = self.cues[idx]
        return cue.text if cue.start <= seconds <= cue.end else ""
