from datetime import datetime, timezone
from tvwatch.core.utils import format_ascii_table, format_discord_timestamp


def test_format_ascii_table_basic():
    headers = ["Col A", "Col B"]
    rows = [["1", "Two"], ["Three", "4"]]
    result = format_ascii_table(headers, rows)
    expected = (
        "+-------+-------+\n"
        "| Col A | Col B |\n"
        "+-------+-------+\n"
        "| 1     | Two   |\n"
        "| Three | 4     |\n"
        "+-------+-------+"
    )
    assert result == expected


def test_format_ascii_table_empty():
    assert format_ascii_table([], []) == ""


def test_format_ascii_table_uneven_rows():
    headers = ["A", "B", "C"]
    rows = [["1"], ["1", "2", "3", "4"]]
    result = format_ascii_table(headers, rows)
    assert "A" in result
    assert "B" in result
    assert "C" in result


def test_format_ascii_table_diacritics():
    headers = ["Pořad", "Stav"]
    rows = [
        ["Země patří Luně", "Aktivní"],
        ["Příliš žluťoučký kůň", "Pozastaveno"],
    ]
    result = format_ascii_table(headers, rows)
    assert "Země patří Luně" in result
    assert "Příliš žluťoučký kůň" in result


def test_format_discord_timestamp_datetime():
    dt = datetime(2026, 9, 18, 20, 0, 0, tzinfo=timezone.utc)
    tag = format_discord_timestamp(dt, "R")
    assert tag == f"<t:{int(dt.timestamp())}:R>"


def test_format_discord_timestamp_epoch():
    epoch = 1726689600
    tag = format_discord_timestamp(epoch, "F")
    assert tag == f"<t:{epoch}:F>"


def test_episodes_view_pagination_and_playability():
    import asyncio
    from tvwatch.interfaces.discord_bot import EpisodesView

    async def _runner():
        episodes = []
        for s in [1, 6]:
            for e in range(1, 10):
                playable = (s == 6 and e >= 5)  # 5 playable episodes in season 6
                card_label = "Dostupné do 23. 9." if playable else None
                episodes.append({
                    "url": f"https://www.ceskatelevize.cz/porady/123-test/222385702{s:02d}{e:03d}/",
                    "name": f"{e}/26 Epizoda {e}",
                    "broadcast_at": "2026-09-04T07:15:00",
                    "metadata": {
                        "playable": playable,
                        "season": {"title": f"{s}. řada"},
                        "cardLabels": {"topLeft": card_label} if card_label else {},
                    },
                })

        view = EpisodesView("https://www.ceskatelevize.cz/porady/123-test/", episodes)
        assert view.current_filter == "playable"
        filtered = view._get_filtered_episodes()
        assert len(filtered) == 5  # Only 5 playable episodes

        content = view.get_content()
        assert "S06E05 - Epizoda 5" in content
        assert "Dostupné do 23. 9." in content
        assert "Vypršelo" not in content

        # Test switching to all
        view.current_filter = "all"
        view.page = 0
        all_content = view.get_content()
        assert "S01E01 - Epizoda 1" in all_content
        assert "Vypršelo" in all_content

        # Edge cases: old record without playable flag or with rights restricted
        old_ep = {"metadata": {}}
        assert EpisodesView._is_playable(old_ep) is False

        no_rights_ep = {
            "metadata": {
                "playable": True,
                "cardLabels": {"center": "ČT nemá práva pro internet"},
            }
        }
        assert EpisodesView._is_playable(no_rights_ep) is False

        playable_ep = {"metadata": {"playable": True}}
        assert EpisodesView._is_playable(playable_ep) is True

    asyncio.run(_runner())

