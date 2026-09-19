from tvwatch.core.models import Episode
from tvwatch.core.scraper import _extract_schema_data


def test_extracts_series_and_items_from_jsonld():
    html = """
    <div id="__next">
      <script type="application/ld+json">
      {"@context": "https://schema.org", "@type": "TVSeries", "name": "Test Serial"}
      </script>
      <script type="application/ld+json">
      {"@context": "https://schema.org", "@type": "ItemList",
       "itemListElement": [
          {"@type": "ListItem", "position": 1,
           "item": {"@type": "TVEpisode", "name": "Díl 1", "url": "https://ceskatelevize.cz/porady/x/1"}},
          {"@type": "ListItem", "position": 2,
           "item": {"@type": "TVEpisode", "name": "Díl 2", "url": "https://ceskatelevize.cz/porady/x/2"}}
       ]}
      </script>
    </div>
    """.encode()
    data = _extract_schema_data(html)
    assert data["series"]["name"] == "Test Serial"
    assert len(data["list"]["itemListElement"]) == 2

    # Validate an episode via Pydantic
    e = Episode(url=data["list"]["itemListElement"][0]["item"]["url"])
    assert str(e.url).endswith("/1")


def test_dynamic_graphql_hash_caching():
    from unittest.mock import MagicMock

    from tvwatch.core.scraper import (
        clear_graphql_hash_cache,
        fetch_dynamic_graphql_hash,
    )

    clear_graphql_hash_cache()
    session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.ok = False
    session.get.return_value = mock_resp

    # First call uses network and falls back to config default
    hash1 = fetch_dynamic_graphql_hash(
        session, "https://ceskatelevize.cz/porady/123-test/"
    )
    assert len(hash1) == 64
    assert session.get.call_count == 1

    # Second call should return cached hash without network call
    hash2 = fetch_dynamic_graphql_hash(
        session, "https://ceskatelevize.cz/porady/123-test/"
    )
    assert hash2 == hash1
    assert session.get.call_count == 1  # No additional network call

    # After clearing cache, network call should happen again
    clear_graphql_hash_cache()
    fetch_dynamic_graphql_hash(session, "https://ceskatelevize.cz/porady/123-test/")
    assert session.get.call_count == 2
