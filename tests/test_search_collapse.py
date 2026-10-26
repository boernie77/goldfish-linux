"""Suchtreffer: Folgen werden pro Serie zu einer Ordnerkachel zusammengefasst."""

from goldfish_linux.windows.browse_page import collapse_episode_hits


def _ep(item_id, rel, parent=7):
    return {"id": item_id, "relPath": rel, "metadata": {"tmdbType": "episode", "parentId": parent}}


def test_folgen_werden_pro_serie_gebuendelt():
    items = [_ep(1, "Dark/S1/a.mkv"), _ep(2, "Dark/S1/b.mkv"), {"id": 3, "relPath": "Film.mkv", "metadata": {}}]
    folders, rest = collapse_episode_hits(items)
    assert [(f["name"], f["itemCount"], f["metadataId"]) for f in folders] == [("Dark", 2, 7)]
    assert [i["id"] for i in rest] == [3]


def test_ohne_folgen_bleibt_alles_stehen():
    items = [{"id": 1, "relPath": "A.mkv", "metadata": {}}]
    assert collapse_episode_hits(items) == ([], items)
