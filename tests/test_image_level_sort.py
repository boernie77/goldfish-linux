"""Bilder-Bibliotheken: „Hinzugefügt" sortiert Ordner und Bilder der Ebene."""

from goldfish_linux.windows.browse_page import sort_image_level


def test_hinzugefuegt_juengster_inhalt_zuerst_gemischte_formate():
    folders = [{"name": "a", "addedAt": "2026-11-01 10:00:00"}, {"name": "b", "addedAt": "2026-11-20T08:00:00Z"}]
    images = [{"id": 1, "addedAt": "2026-10-01 00:00:00"}, {"id": 2, "addedAt": "2026-11-25 00:00:00"}]
    sort_image_level(folders, images, "added", False)
    assert [f["name"] for f in folders] == ["b", "a"]
    assert [i["id"] for i in images] == [2, 1]


def test_groesse_nur_fuer_bilder():
    folders = [{"name": "z"}, {"name": "a"}]
    images = [{"id": 1, "sizeBytes": 5}, {"id": 2, "sizeBytes": 9}]
    sort_image_level(folders, images, "size", False)
    assert [f["name"] for f in folders] == ["z", "a"]
    assert [i["id"] for i in images] == [2, 1]
