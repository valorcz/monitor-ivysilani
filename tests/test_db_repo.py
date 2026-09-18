import os
import tempfile
from tvwatch.core.db import DuckRepo


def test_add_list_disable_flow():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "test.duckdb")
        with DuckRepo(path) as repo:
            canonical = repo.add_or_reactivate_show(
                "https://ceskatelevize.cz/porady/s1/"
            )
            assert canonical == "https://www.ceskatelevize.cz/porady/s1/"
            assert repo.list_shows(active=True) == [
                ("https://www.ceskatelevize.cz/porady/s1/", True)
            ]
            assert repo.disable_show("https://ceskatelevize.cz/porady/s1/") is True
            assert repo.list_shows(None) == [
                ("https://www.ceskatelevize.cz/porady/s1/", False)
            ]


def test_episode_recording_and_cooldown():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "test.duckdb")
        with DuckRepo(path) as repo:
            show_url = "https://www.ceskatelevize.cz/porady/123-test/"
            ep_url = "https://www.ceskatelevize.cz/porady/123-test/225384613200001/"

            # 1. First time discovery -> should notify
            assert (
                repo.record_episode(
                    show_url=show_url,
                    url=ep_url,
                    name="Episode 1",
                    idec="225384613200001",
                )
                is True
            )

            # 2. Rescanned before notification was sent -> still should notify (last_notified is None)
            assert (
                repo.record_episode(
                    show_url=show_url,
                    url=ep_url,
                    name="Episode 1",
                    idec="225384613200001",
                )
                is True
            )

            # 3. Mark as notified
            repo.mark_episodes_notified([ep_url])

            # 4. Immediate rescan after notification -> should NOT notify (within cooldown)
            assert (
                repo.record_episode(
                    show_url=show_url,
                    url=ep_url,
                    name="Episode 1",
                    idec="225384613200001",
                    cooldown_days=30,
                )
                is False
            )

            # 5. Variation of URL (without trailing slash) should map to same episode and NOT notify
            ep_url_variant = (
                "https://www.ceskatelevize.cz/porady/123-test/225384613200001"
            )
            assert (
                repo.record_episode(
                    show_url=show_url,
                    url=ep_url_variant,
                    name="Episode 1",
                    idec="225384613200001",
                    cooldown_days=30,
                )
                is False
            )
