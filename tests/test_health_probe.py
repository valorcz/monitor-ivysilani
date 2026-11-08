import tempfile
from unittest.mock import MagicMock

from tvwatch.core.db import DuckRepo
from tvwatch.core.health import probe_upstream_health


def test_health_probe_success():
    session = MagicMock()

    # Mock HTML response
    html_resp = MagicMock()
    html_resp.ok = True
    html_resp.status_code = 200
    html_resp.text = """
    <html>
      <script id="__NEXT_DATA__" type="application/json">
        {"props": {"pageProps": {"data": {"programme": {"programmeId": "648831feef264d9bd6016a1c", "title": "Události"}}}}}
      </script>
    </html>
    """

    # Mock GraphQL response
    gql_resp = MagicMock()
    gql_resp.ok = True
    gql_resp.status_code = 200
    gql_resp.json.return_value = {
        "data": {
            "programme": {
                "episodes": {
                    "totalCount": 10,
                    "items": [
                        {
                            "id": "226411000101108",
                            "title": "8. listopadu",
                            "playable": True,
                        }
                    ],
                }
            }
        }
    }

    session.get.return_value = html_resp
    session.post.return_value = gql_resp

    status = probe_upstream_health(session=session)
    assert status.ok is True
    assert status.html_ok is True
    assert status.graphql_ok is True
    assert status.programme_id == "648831feef264d9bd6016a1c"
    assert status.episodes_found == 1
    assert len(status.details) == 0


def test_health_probe_detects_http_failure():
    session = MagicMock()
    html_resp = MagicMock()
    html_resp.ok = False
    html_resp.status_code = 503
    session.get.return_value = html_resp

    status = probe_upstream_health(session=session)
    assert status.ok is False
    assert status.html_ok is False
    assert status.graphql_ok is False
    assert any("503" in d for d in status.details)


def test_health_probe_detects_missing_next_data():
    session = MagicMock()
    html_resp = MagicMock()
    html_resp.ok = True
    html_resp.status_code = 200
    html_resp.text = "<html><body>No data here</body></html>"
    session.get.return_value = html_resp

    status = probe_upstream_health(session=session)
    assert status.ok is False
    assert status.html_ok is False
    assert any("__NEXT_DATA__" in d for d in status.details)


def test_health_probe_detects_graphql_schema_error():
    session = MagicMock()
    html_resp = MagicMock()
    html_resp.ok = True
    html_resp.status_code = 200
    html_resp.text = """
    <html>
      <script id="__NEXT_DATA__" type="application/json">
        {"props": {"pageProps": {"data": {"programme": {"programmeId": "648831feef264d9bd6016a1c"}}}}}
      </script>
    </html>
    """

    gql_resp = MagicMock()
    gql_resp.ok = True
    gql_resp.status_code = 200
    gql_resp.json.return_value = {
        "errors": [{"message": "GRAPHQL_VALIDATION_FAILED"}]
    }

    session.get.return_value = html_resp
    session.post.return_value = gql_resp

    status = probe_upstream_health(session=session)
    assert status.ok is False
    assert status.html_ok is True
    assert status.graphql_ok is False
    assert any("GRAPHQL_VALIDATION_FAILED" in d for d in status.details)


def test_health_probe_detects_zero_episodes():
    session = MagicMock()
    html_resp = MagicMock()
    html_resp.ok = True
    html_resp.status_code = 200
    html_resp.text = """
    <html>
      <script id="__NEXT_DATA__" type="application/json">
        {"props": {"pageProps": {"data": {"programme": {"programmeId": "648831feef264d9bd6016a1c"}}}}}
      </script>
    </html>
    """

    gql_resp = MagicMock()
    gql_resp.ok = True
    gql_resp.status_code = 200
    gql_resp.json.return_value = {
        "data": {"programme": {"episodes": {"totalCount": 0, "items": []}}}
    }

    session.get.return_value = html_resp
    session.post.return_value = gql_resp

    status = probe_upstream_health(session=session)
    assert status.ok is False
    assert status.html_ok is True
    assert status.graphql_ok is False
    assert any("0 episodes" in d for d in status.details)


def test_db_health_alert_cooldown_and_recovery():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = f"{tmpdir}/guild.duckdb"
        with DuckRepo(db_path) as repo:
            # 1. Initially should send alert
            assert repo.should_send_health_alert(cooldown_hours=24) is True

            # 2. Record alert dispatched
            repo.record_health_alert()
            assert repo.should_send_health_alert(cooldown_hours=24) is False

            # 3. Health recovery dispatched
            recovered = repo.record_health_recovery()
            assert recovered is True  # transitioned from failing -> ok
            assert repo.should_send_health_alert(cooldown_hours=24) is True

            # 4. Repeated recovery when already ok does not trigger again
            recovered_again = repo.record_health_recovery()
            assert recovered_again is False


def test_scraper_discrepancy_anomaly():
    from unittest.mock import patch
    from tvwatch.core.scraper import sync_one_show

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = f"{tmpdir}/guild.duckdb"
        with DuckRepo(db_path) as repo:
            # Mock requests to return HTML claiming 5 playable episodes, but GraphQL returns 0
            fake_html = """
            <html>
              <script id="__NEXT_DATA__" type="application/json">
                {"props": {"pageProps": {"data": {"programme": {"programmeId": "648831feef264d9bd6016a1c", "title": "Test Show", "playableEpisodeCount": 5}}}}}
              </script>
            </html>
            """
            with patch("requests.Session.get") as mock_get, patch(
                "tvwatch.core.scraper.fetch_all_episodes"
            ) as mock_fetch:
                mock_resp = MagicMock()
                mock_resp.ok = True
                mock_resp.status_code = 200
                mock_resp.text = fake_html
                mock_get.return_value = mock_resp
                mock_fetch.return_value = []  # 0 episodes returned

                res = sync_one_show("https://www.ceskatelevize.cz/porady/123-test/", repo=repo)
                assert res is not None
                assert len(res.anomalies) > 0
                assert any("Rozpor ve struktuře" in a for a in res.anomalies)
