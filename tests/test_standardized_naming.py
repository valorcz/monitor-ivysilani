import pytest

from tvwatch.core.utils import (
    compute_plex_naming,
    format_standardized_title,
    parse_episode_title_and_number,
    parse_season_number,
    roman_to_int,
    sanitize_slug,
)


@pytest.mark.parametrize(
    "roman, expected",
    [
        ("I", 1),
        ("II", 2),
        ("III", 3),
        ("IV", 4),
        ("V", 5),
        ("VI", 6),
        ("VII", 7),
        ("VIII", 8),
        ("IX", 9),
        ("X", 10),
        ("XI", 11),
        ("XII", 12),
        ("XIV", 14),
        ("XIX", 19),
        ("XX", 20),
        ("vi", 6),
        ("iv", 4),
        ("", None),
        ("abc", None),
    ],
)
def test_roman_to_int(roman, expected):
    assert roman_to_int(roman) == expected


@pytest.mark.parametrize(
    "season_input, expected",
    [
        ("VI. řada", 6),
        ("VI.\xa0řada", 6),
        ("VI.", 6),
        ("VI", 6),
        ("I. řada", 1),
        ("II. řada", 2),
        ("III. řada", 3),
        ("IV. řada", 4),
        ("V. řada", 5),
        ("1. řada", 1),
        ("2. série", 2),
        ("Řada 3", 3),
        ("Season 4", 4),
        ("5", 5),
        ({"title": "VI.\xa0řada"}, 6),
        ({"name": "II. řada"}, 2),
        ("řada", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_season_number(season_input, expected):
    assert parse_season_number(season_input) == expected


@pytest.mark.parametrize(
    "title_input, expected_num, expected_title",
    [
        ("10/26 Kde se vzal Měsíc", 10, "Kde se vzal Měsíc"),
        ("10/26 Den na\xa0pláži", 10, "Den na pláži"),
        ("10/26", 10, ""),
        ("1. díl - Zrození", 1, "Zrození"),
        ("1. díl", 1, ""),
        ("Díl 5 - Velká loupež", 5, "Velká loupež"),
        ("2. Tajemství hradu", 2, "Tajemství hradu"),
        ("8. srpna 2005", None, "8. srpna 2005"),
        ("24. prosince 2024", None, "24. prosince 2024"),
        ("Kde se vzal Měsíc", None, "Kde se vzal Měsíc"),
        ("Stínka - první část", None, "Stínka - první část"),
    ],
)
def test_parse_episode_title_and_number(title_input, expected_num, expected_title):
    num, title = parse_episode_title_and_number(title_input)
    assert num == expected_num
    assert title == expected_title


@pytest.mark.parametrize(
    "raw_title, season_val, expected",
    [
        (
            "10/26 Kde se vzal Měsíc",
            {"title": "VI.\xa0řada"},
            "S06E10 - Kde se vzal Měsíc",
        ),
        ("11/26 Den na\xa0pláži", {"title": "VI.\xa0řada"}, "S06E11 - Den na pláži"),
        ("1/12 První případ", {"title": "I. řada"}, "S01E01 - První případ"),
        ("3. díl - Návrat", "2. řada", "S02E03 - Návrat"),
        ("5/10", {"title": "IV. řada"}, "S04E05"),
        ("Kde se vzal Měsíc", {"title": "VI. řada"}, "S06 - Kde se vzal Měsíc"),
        ("10/26 Kde se vzal Měsíc", None, "E10 - Kde se vzal Měsíc"),
        ("Stínka - první část", None, "Stínka - první část"),
        ("8. srpna 2005", None, "8. srpna 2005"),
        (
            "S06E10 - Kde se vzal Měsíc",
            {"title": "VI. řada"},
            "S06E10 - Kde se vzal Měsíc",
        ),  # Idempotent
    ],
)
def test_format_standardized_title(raw_title, season_val, expected):
    assert format_standardized_title(raw_title, season_val) == expected


def test_format_standardized_title_with_idec():
    # 15-digit IDEC with episode number in last 4 digits (0001 -> 1)
    res = format_standardized_title(
        "Stínka - první část",
        {"title": "1. řada"},
        idec="225384613200001",
    )
    assert res == "S01E01 - Stínka - první část"


def test_sanitize_slug():
    assert sanitize_slug("Sluneční soustava") == "sluneční_soustava"
    assert sanitize_slug("Kde se vzal Měsíc!?") == "kde_se_vzal_měsíc"
    assert sanitize_slug("  A / B : C  ") == "a_b_c"
    assert sanitize_slug("") == ""


def test_compute_plex_naming():
    res = compute_plex_naming(
        show_title="Sluneční soustava",
        raw_episode_title="2/5 Sopečné světy",
        season_val="I. řada",
    )
    assert res["clean_show_dir"] == "sluneční_soustava"
    assert res["clean_season_dir"] == "season_01"
    assert res["clean_filename"] == "sluneční_soustava_s01e02_sopečné_světy"
    assert res["season_number"] == 1
    assert res["episode_number"] == 2
    assert res["series"] == "Sluneční soustava"
    assert res["episode"] == "Sopečné světy"

    # Test with show title suffix/prefix removal
    res2 = compute_plex_naming(
        show_title="Tobiáš Lolness",
        raw_episode_title="26. díl - Tráva je křehká - Tobiáš Lolness",
        season_val="1. řada",
    )
    assert res2["clean_show_dir"] == "tobiáš_lolness"
    assert res2["clean_season_dir"] == "season_01"
    assert res2["clean_filename"] == "tobiáš_lolness_s01e26_tráva_je_křehká"
    assert res2["episode_number"] == 26
