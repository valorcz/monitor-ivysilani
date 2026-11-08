import yt_dlp

from yt_dlp_plugins.extractor.ceskatelevize import CeskaTelevizeIE
from yt_dlp_plugins.extractor.ceskatelevize_tvwatch import CeskaTelevizeTvwatchIE


def test_ytdlp_plugin_registered():
    ydl = yt_dlp.YoutubeDL()
    ie = ydl.get_info_extractor("CeskaTelevize")
    assert isinstance(ie, CeskaTelevizeTvwatchIE)
    assert "tvwatch" in ie.IE_NAME


def test_tvwatch_enrich_entry():
    ie = CeskaTelevizeTvwatchIE()
    entry = {
        "id": "225384613200001",
        "title": "Sluneční soustava 1",
        "series": "Sluneční soustava",
        "_raw_episode_title": "1/5 Vnitřní planety",
        "_season_title": "I. řada",
    }
    ie._enrich_entry(entry)

    assert entry["clean_show_dir"] == "sluneční_soustava"
    assert entry["clean_season_dir"] == "season_01"
    assert entry["clean_filename"] == "sluneční_soustava_s01e01_vnitřní_planety"
    assert entry["season_number"] == 1
    assert entry["episode_number"] == 1
    assert "_raw_episode_title" not in entry
    assert "_season_title" not in entry


def test_fix_subtitles():
    raw_subs = "1; 1000 5000\nHello world\n2; 6000 10500\nSecond line"
    fixed = CeskaTelevizeIE._fix_subtitles(raw_subs)
    lines = fixed.splitlines()
    assert lines[0] == "1"
    assert lines[1] == "00:00:01.000 --> 00:00:05.000"
    assert lines[2] == "Hello world"
    assert lines[3] == "2"
    assert lines[4] == "00:00:06.000 --> 00:00:10.500"
    assert lines[5] == "Second line"
