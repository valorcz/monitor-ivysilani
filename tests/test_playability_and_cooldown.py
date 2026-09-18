import os
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from tvwatch.core.db import DuckRepo
from tvwatch.core.scraper import fetch_all_episodes
from tvwatch.core.utils import (
    canonical_episode_url,
    extract_episode_id,
    normalize_show_url,
)


def test_url_normalization_rules():
    assert (
        normalize_show_url("https://ceskatelevize.cz/porady/123-test")
        == "https://www.ceskatelevize.cz/porady/123-test/"
    )
    assert (
        normalize_show_url("https://www.ceskatelevize.cz/porady/123-test/dily/")
        == "https://www.ceskatelevize.cz/porady/123-test/"
    )
    assert (
        normalize_show_url("https://www.ceskatelevize.cz/porady/123-test?param=1#sec")
        == "https://www.ceskatelevize.cz/porady/123-test/"
    )
    assert (
        canonical_episode_url(
            "https://ceskatelevize.cz/porady/123-test", "225384613200001"
        )
        == "https://www.ceskatelevize.cz/porady/123-test/225384613200001/"
    )
    assert (
        extract_episode_id(
            "https://www.ceskatelevize.cz/porady/123-test/225384613200001/"
        )
        == "225384613200001"
    )


def test_graphql_pagination_more_than_6_episodes():
    # Simulate a show with 25 episodes returned in 2 pages of GraphQL responses
    session = MagicMock()

    page1_items = [
        {"id": f"225384613200{i:03d}", "title": f"Díl {i}", "playable": True}
        for i in range(1, 21)
    ]
    page2_items = [
        {"id": f"225384613200{i:03d}", "title": f"Díl {i}", "playable": True}
        for i in range(21, 26)
    ]

    resp1 = MagicMock()
    resp1.ok = True
    resp1.json.return_value = {
        "data": {
            "episodesPreviewFind": {
                "totalCount": 25,
                "items": page1_items,
            }
        }
    }

    resp2 = MagicMock()
    resp2.ok = True
    resp2.json.return_value = {
        "data": {
            "episodesPreviewFind": {
                "totalCount": 25,
                "items": page2_items,
            }
        }
    }

    session.get.side_effect = [resp1, resp2]

    # Patch limit inside fetch_all_episodes by calling it with limit 20 or testing standard pagination
    episodes = fetch_all_episodes(
        session, show_idec="225384613200026", query_hash="fakehash"
    )
    # All 25 episodes should be retrieved
    assert len(episodes) == 25
    assert episodes[0]["id"] == "225384613200001"
    assert episodes[-1]["id"] == "225384613200025"


def test_initial_add_backfill_prevents_notification_blast():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "test.duckdb")
        with DuckRepo(path) as repo:
            show_url = "https://www.ceskatelevize.cz/porady/16560311257-tobias-lolness/"
            repo.add_or_reactivate_show(show_url)

            # Record 20 existing archive episodes with backfill=True
            for i in range(1, 21):
                notifiable = repo.record_episode(
                    show_url=show_url,
                    url=f"https://www.ceskatelevize.cz/porady/16560311257-tobias-lolness/225384613200{i:03d}/",
                    name=f"Episode {i}",
                    idec=f"225384613200{i:03d}",
                    backfill=True,
                )
                assert notifiable is False, "Backfilled episodes must not be notifiable"

            # Now a brand new 21st episode airs
            new_notifiable = repo.record_episode(
                show_url=show_url,
                url="https://www.ceskatelevize.cz/porady/16560311257-tobias-lolness/225384613200021/",
                name="Episode 21",
                idec="225384613200021",
                backfill=False,
            )
            assert new_notifiable is True, "Newly aired episode must be notifiable"


def test_cooldown_timeout_expiration():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "test.duckdb")
        with DuckRepo(path) as repo:
            show_url = "https://www.ceskatelevize.cz/porady/123-show/"
            ep_url = "https://www.ceskatelevize.cz/porady/123-show/225384613200001/"

            # Episode discovered and notified
            assert repo.record_episode(show_url, ep_url, idec="225384613200001") is True
            repo.mark_episodes_notified([ep_url])

            # Immediate rescan within cooldown -> False
            assert (
                repo.record_episode(
                    show_url, ep_url, idec="225384613200001", cooldown_days=30
                )
                is False
            )

            # Manually simulate time passing: set last_notified_at to 31 days ago
            past = datetime.now(timezone.utc) - timedelta(days=31)
            repo.conn.execute(
                "UPDATE episodes SET last_notified_at = ? WHERE idec = ?",
                [past, "225384613200001"],
            )

            # Rescan after cooldown timeout passed (e.g. rerun broadcast) -> True
            assert (
                repo.record_episode(
                    show_url, ep_url, idec="225384613200001", cooldown_days=30
                )
                is True
            )
