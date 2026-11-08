"""
tvwatch metadata enrichment plugin for yt-dlp.
Enriches Česká televize info_dict with Plex/Jellyfin structured naming fields:
- clean_show_dir
- clean_season_dir
- clean_filename
"""

from tvwatch.core.utils import compute_plex_naming
from yt_dlp_plugins.extractor.ceskatelevize import CeskaTelevizeIE


class CeskaTelevizeTvwatchIE(CeskaTelevizeIE, plugin_name="tvwatch"):
    """
    Subclass of CeskaTelevizeIE providing tvwatch standardized naming metadata.
    """

    def _real_extract(self, url):
        res = super()._real_extract(url)
        if isinstance(res, dict) and res.get("_type") == "playlist":
            for entry in res.get("entries", []):
                self._enrich_entry(entry)
            return res
        elif isinstance(res, dict):
            self._enrich_entry(res)
            return res
        return res

    def _enrich_entry(self, entry: dict) -> None:
        show_title = entry.get("series")
        raw_ep_title = entry.get("_raw_episode_title") or entry.get("title")
        season_title = entry.get("_season_title") or entry.get("season")
        ep_id = entry.get("id")

        naming = compute_plex_naming(
            show_title=show_title,
            raw_episode_title=raw_ep_title,
            season_val=season_title,
            episode_id=ep_id,
        )
        entry.update(naming)

        # Remove temporary internal keys
        entry.pop("_next_data", None)
        entry.pop("_raw_episode_title", None)
        entry.pop("_season_title", None)
