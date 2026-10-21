from goldfish_linux.formatting import player_title


def test_episode_title_has_show_and_number():
    item = {
        "title": "show.s01e02.1080p.mkv",
        "relPath": "Dark/Staffel 1/show.s01e02.mkv",
        "metadata": {"title": "Lügen", "tmdbType": "episode", "season": 1, "episode": 2, "showTitle": "Dark"},
    }
    assert player_title(item) == "Dark - Lügen - S01E02"


def test_show_falls_back_to_top_folder_and_double_episode():
    item = {
        "relPath": "Luther/S03/x.mkv",
        "episodeEnd": 2,
        "metadata": {"title": "Folge", "tmdbType": "episode", "season": 3, "episode": 1},
    }
    assert player_title(item) == "Luther - Folge - S03E01-02"


def test_movie_and_unmatched_keep_title():
    assert player_title({"title": "x.mkv", "metadata": {"title": "Inception"}}) == "Inception"
    assert player_title({"title": "x.mkv"}) == "x.mkv"
