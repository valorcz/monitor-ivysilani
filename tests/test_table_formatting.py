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
