import json
import os
import tempfile
from unittest.mock import patch

from tvwatch.core.db import DuckRepo
from tvwatch.interfaces.cli import build_parser


def test_cli_add_list_and_disable():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test.duckdb")
        parser = build_parser()

        # 1. Add show
        args_add = parser.parse_args(
            ["--db", db_path, "add", "https://ceskatelevize.cz/porady/123-show/"]
        )
        args_add.func(args_add)

        # Verify DB directly
        with DuckRepo(db_path) as repo:
            assert len(repo.list_shows(active=True)) == 1

        # 2. List shows (JSON mode)
        with patch("sys.stdout.buffer.write") as mock_write, patch("sys.stdout.flush"):
            args_list_json = parser.parse_args(["--db", db_path, "list", "--json"])
            args_list_json.func(args_list_json)

            assert mock_write.called
            output_raw = b"".join(
                call[0][0] for call in mock_write.call_args_list
            ).decode("utf-8")
            data = json.loads(output_raw)
            assert len(data) == 1
            assert data[0]["url"] == "https://www.ceskatelevize.cz/porady/123-show/"
            assert data[0]["active"] is True

        # 3. Disable show
        args_disable = parser.parse_args(
            [
                "--db",
                db_path,
                "disable",
                "https://www.ceskatelevize.cz/porady/123-show/",
            ]
        )
        args_disable.func(args_disable)

        with DuckRepo(db_path) as repo:
            assert len(repo.list_shows(active=True)) == 0
            assert len(repo.list_shows(active=None)) == 1


def test_cli_episodes_and_status():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test.duckdb")
        show_url = "https://www.ceskatelevize.cz/porady/123-show/"

        with DuckRepo(db_path) as repo:
            repo.add_or_reactivate_show(show_url)
            repo.record_episode(
                show_url=show_url,
                url="https://www.ceskatelevize.cz/porady/123-show/225384613200001/",
                name="E01 - Pilot",
                idec="225384613200001",
                metadata={"playable": True},
            )
            repo.record_episode(
                show_url=show_url,
                url="https://www.ceskatelevize.cz/porady/123-show/225384613200002/",
                name="E02 - Expired",
                idec="225384613200002",
                metadata={"playable": False},
            )

        parser = build_parser()

        # 1. Episodes JSON (all)
        with patch("sys.stdout.buffer.write") as mock_write, patch("sys.stdout.flush"):
            args_ep_json = parser.parse_args(
                ["--db", db_path, "episodes", show_url, "--json"]
            )
            args_ep_json.func(args_ep_json)

            output_raw = b"".join(
                call[0][0] for call in mock_write.call_args_list
            ).decode("utf-8")
            episodes = json.loads(output_raw)
            assert len(episodes) == 2

        # 2. Episodes JSON (playable only)
        with patch("sys.stdout.buffer.write") as mock_write, patch("sys.stdout.flush"):
            args_ep_play = parser.parse_args(
                ["--db", db_path, "episodes", show_url, "--playable", "--json"]
            )
            args_ep_play.func(args_ep_play)

            output_raw = b"".join(
                call[0][0] for call in mock_write.call_args_list
            ).decode("utf-8")
            episodes = json.loads(output_raw)
            assert len(episodes) == 1
            assert episodes[0]["idec"] == "225384613200001"

        # 3. Status JSON
        with patch("sys.stdout.buffer.write") as mock_write, patch("sys.stdout.flush"):
            args_status = parser.parse_args(["--db", db_path, "status", "--json"])
            args_status.func(args_status)

            output_raw = b"".join(
                call[0][0] for call in mock_write.call_args_list
            ).decode("utf-8")
            status_data = json.loads(output_raw)
            assert status_data["total_shows"] == 1
            assert status_data["total_episodes"] == 2


def test_cli_remove():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test.duckdb")
        show_url = "https://www.ceskatelevize.cz/porady/123-show/"

        with DuckRepo(db_path) as repo:
            repo.add_or_reactivate_show(show_url)
            repo.record_episode(
                show_url=show_url,
                url="https://www.ceskatelevize.cz/porady/123-show/225384613200001/",
                name="Pilot",
            )

        parser = build_parser()
        args_remove = parser.parse_args(["--db", db_path, "remove", show_url])
        args_remove.func(args_remove)

        with DuckRepo(db_path) as repo:
            assert len(repo.list_shows(active=None)) == 0
            assert len(repo.get_show_episodes(show_url)) == 0


def test_cli_standardize():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test.duckdb")
        show_url = "https://www.ceskatelevize.cz/porady/123-show/"

        with DuckRepo(db_path) as repo:
            repo.add_or_reactivate_show(show_url)
            repo.record_episode(
                show_url=show_url,
                url="https://www.ceskatelevize.cz/porady/123-show/225384613200001/",
                name="1. díl - První epizoda",
                metadata={"season": "1. řada"},
            )

        parser = build_parser()
        args_std = parser.parse_args(["--db", db_path, "standardize"])
        args_std.func(args_std)

        with DuckRepo(db_path) as repo:
            eps = repo.get_show_episodes(show_url)
            assert eps[0]["name"] == "S01E01 - První epizoda"
