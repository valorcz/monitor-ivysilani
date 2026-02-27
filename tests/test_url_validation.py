import pytest
from tvwatch.core.models import Episode, TVSeries


def test_episode_allows_ct_url():
    ep = Episode(url="https://ceskatelevize.cz/porady/x/1")
    assert str(ep.url).startswith("https://ceskatelevize.cz/porady/")


@pytest.mark.parametrize(
    "url",
    [
        "http://ceskatelevize.cz/porady/123",  # not https
        "https://ceskatelevize.cz/sport/123",  # wrong path
        "https://example.com/porady/123",  # wrong host
    ],
)
def test_episode_rejects_non_matching(url):
    with pytest.raises(Exception):
        Episode(url=url)


def test_tvseries_validation():
    with pytest.raises(Exception):
        TVSeries(url="https://example.com/something")
    ok = TVSeries(url="https://ceskatelevize.cz/porady/abc/", name="Test")
    assert ok.name == "Test"
