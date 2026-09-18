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


def test_concurrent_episode_recording():
    import concurrent.futures

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "test_concurrent.duckdb")
        with DuckRepo(path) as repo:
            show_url = "https://www.ceskatelevize.cz/porady/123-test/"
            repo.add_or_reactivate_show(show_url)

            def worker(worker_id: int):
                errors = []
                for i in range(20):
                    ep_id = f"225384613200{i:03d}"
                    ep_url = f"{show_url}{ep_id}/"
                    try:
                        repo.record_episode(
                            show_url=show_url,
                            url=ep_url,
                            name=f"Episode {i}",
                            idec=ep_id,
                            backfill=(worker_id % 2 == 0),
                        )
                        repo.mark_episodes_notified([ep_url])
                        repo.get_active_shows()
                    except Exception as e:
                        errors.append(e)
                return errors

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(worker, wid) for wid in range(8)]
                all_errors = []
                for f in concurrent.futures.as_completed(futures):
                    all_errors.extend(f.result())

            assert all_errors == [], f"Encountered concurrency errors: {all_errors}"


def test_delete_show_and_episodes():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "test_delete.duckdb")
        with DuckRepo(path) as repo:
            show_url = "https://www.ceskatelevize.cz/porady/test-show/"
            repo.add_or_reactivate_show(show_url)
            repo.record_episode(
                show_url=show_url,
                url=f"{show_url}1001/",
                name="Ep 1",
                idec="1001",
            )
            assert len(repo.get_show_episodes(show_url)) == 1
            assert repo.delete_show(show_url) is True
            assert len(repo.get_show_episodes(show_url)) == 0
            assert repo.list_shows() == []


def test_get_stats():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "test_stats.duckdb")
        with DuckRepo(path) as repo:
            show1 = "https://www.ceskatelevize.cz/porady/show-1/"
            show2 = "https://www.ceskatelevize.cz/porady/show-2/"
            repo.add_or_reactivate_show(show1)
            repo.add_or_reactivate_show(show2)
            repo.disable_show(show2)

            ep1 = f"{show1}101/"
            repo.record_episode(show_url=show1, url=ep1, name="Ep 1", idec="101")
            repo.mark_episodes_notified([ep1])

            stats = repo.get_stats()
            assert stats["total_shows"] == 2
            assert stats["active_shows"] == 1
            assert stats["inactive_shows"] == 1
            assert stats["total_episodes"] == 1
            assert stats["notified_episodes"] == 1


def test_mark_unplayable_except():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "test_unplayable.duckdb")
        with DuckRepo(path) as repo:
            show = "https://www.ceskatelevize.cz/porady/show-1/"
            repo.add_or_reactivate_show(show)
            ep1 = f"{show}101/"
            ep2 = f"{show}102/"
            repo.record_episode(
                show_url=show,
                url=ep1,
                name="Ep 1",
                idec="101",
                metadata={"playable": True},
            )
            repo.record_episode(
                show_url=show,
                url=ep2,
                name="Ep 2",
                idec="102",
                metadata={"playable": True},
            )

            # ep2 expires; active is only ep1
            marked = repo.mark_unplayable_except(show, {ep1})
            assert marked == 1

            eps = repo.get_show_episodes(show)
            by_url = {e["url"]: e for e in eps}
            assert by_url[ep1]["metadata"]["playable"] is True
            assert by_url[ep2]["metadata"]["playable"] is False


def test_standardize_all_episodes():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "test_std_names.duckdb")
        with DuckRepo(path) as repo:
            show = "https://www.ceskatelevize.cz/porady/show-1/"
            repo.add_or_reactivate_show(show)
            ep1 = f"{show}225384613200001/"
            repo.record_episode(
                show_url=show,
                url=ep1,
                name="Stínka - první část",
                idec="225384613200001",
                metadata={"season": {"title": "1. řada"}},
            )

            count = repo.standardize_all_episodes(show)
            assert count == 1
            eps = repo.get_show_episodes(show)
            assert eps[0]["name"] == "S01E01 - Stínka - první část"


