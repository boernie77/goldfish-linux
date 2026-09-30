"""Ordner-Sammlungen/Ermittler-Katalog: reine Regeln aus `goldfish_linux.catalog`
(Kommissar-Zeile, Einreihen fehlender Folgen) — ohne GTK.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from goldfish_linux import catalog  # noqa: E402


def _ep(rel: str, date: str = "", tmdb_type: str = "episode") -> dict:
    return {"relPath": rel, "metadata": {"tmdbType": tmdb_type, "releaseDate": date}}


class EpisodeGroupTest(unittest.TestCase):
    def test_kommissar_folder(self) -> None:
        item = _ep("Tatort/Batic und Leitmayr/Tatort.S1991E01.mkv")
        self.assertEqual(catalog.episode_group(item), "Batic und Leitmayr")
        self.assertEqual(catalog.episode_group_folder(item), "Tatort/Batic und Leitmayr")

    def test_season_folders_are_ignored(self) -> None:
        for seg in ("Staffel 1", "Season 02", "serie 3", "S1", "Specials", "special", "Extras", "extra"):
            self.assertEqual(catalog.episode_group(_ep(f"Show/{seg}/x.mkv")), "", seg)

    def test_needs_three_segments_and_episode(self) -> None:
        self.assertEqual(catalog.episode_group(_ep("Tatort/x.mkv")), "")
        self.assertEqual(catalog.episode_group(_ep("Tatort/Batic/x.mkv", tmdb_type="movie")), "")
        self.assertEqual(catalog.episode_group({"relPath": "Tatort/Batic/x.mkv"}), "")


class MergeMissingTest(unittest.TestCase):
    def test_chronological_insert(self) -> None:
        items = [_ep("a", "1991-01-01"), _ep("b", "1995-01-01")]
        missing = catalog.missing_placeholders(
            [{"nr": "1", "date": "1990-01-01"}, {"nr": "2", "date": "1993-01-01"},
             {"nr": "3", "date": "1994-01-01"}, {"nr": "4", "date": "2000-01-01"}]
        )
        merged = catalog.merge_missing(items, missing, chronological=True)
        order = [m.get("nr") or m["relPath"] for m in merged]
        self.assertEqual(order, ["1", "a", "2", "3", "b", "4"])

    def test_not_chronological_appends(self) -> None:
        items = [_ep("a", "1991-01-01")]
        missing = catalog.missing_placeholders([{"nr": "1", "date": "1990-01-01"}])
        merged = catalog.merge_missing(items, missing, chronological=False)
        self.assertEqual([m.get("nr") or m["relPath"] for m in merged], ["a", "1"])
        self.assertTrue(merged[1][catalog.MISSING_KEY])

    def test_team_placeholders_only_without_folder(self) -> None:
        groups = [{"team": "A", "folder": "A"}, {"team": "B", "folder": ""}]
        self.assertEqual([g["team"] for g in catalog.team_placeholders(groups)], ["B"])


class SmallHelpersTest(unittest.TestCase):
    def test_forced_folder(self) -> None:
        self.assertTrue(catalog.in_forced_folder("Tatort", "Tatort"))
        self.assertTrue(catalog.in_forced_folder("Tatort/Batic", "Tatort"))
        self.assertFalse(catalog.in_forced_folder("Tatortx", "Tatort"))
        self.assertFalse(catalog.in_forced_folder("Tatort", None))
        self.assertTrue(catalog.is_team_folder("Tatort/Batic"))
        self.assertFalse(catalog.is_team_folder("Tatort"))

    def test_labels(self) -> None:
        self.assertEqual(catalog.format_catalog_date("1990-02-04"), "04.02.1990")
        self.assertEqual(catalog.catalog_note({"available": True, "total": 80, "owned": 72}), "72/80 Folgen vorhanden")
        self.assertEqual(catalog.catalog_note({"available": False}), "")
        self.assertEqual(catalog.file_count_label(1), "1 Datei")
        self.assertEqual(catalog.file_count_label(3), "3 Dateien")


if __name__ == "__main__":
    unittest.main()
