from tvwatch.core.scraper import _extract_schema_data
from tvwatch.core.models import Episode


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
    """.encode("utf-8")
    data = _extract_schema_data(html)
    assert data["series"]["name"] == "Test Serial"
    assert len(data["list"]["itemListElement"]) == 2

    # Validate an episode via Pydantic
    e = Episode(url=data["list"]["itemListElement"][0]["item"]["url"])
    assert str(e.url).endswith("/1")
