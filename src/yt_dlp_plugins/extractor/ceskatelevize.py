import re
import urllib.parse

from yt_dlp.extractor.ceskatelevize import CeskaTelevizeIE as _OrigCeskaTelevizeIE
from yt_dlp.utils import (
    ExtractorError,
    float_or_none,
    traverse_obj,
)

STREAM_DATA_MEDIA_API_URL_PREFIX = (
    "https://api.ceskatelevize.cz/video/v1/playlist-vod/v1/stream-data/media/external/"
)
STREAM_DATA_API_URL_POSTFIX = "?canPlayDrm=true"
STREAM_DATA_BONUS_API_URL_PREFIX = (
    "https://api.ceskatelevize.cz/video/v1/playlist-vod/v1/stream-data/bonus/BO-"
)
STREAM_DATA_INDEX_API_URL_PREFIX = (
    "https://api.ceskatelevize.cz/video/v1/playlist-vod/v1/stream-data/index/"
)


class CeskaTelevizeIE(_OrigCeskaTelevizeIE, plugin_name="community"):
    """
    Upstream-compatible yt-dlp extractor plugin for Česká televize (ceskatelevize.cz).
    Addresses broken iframe hash endpoint in upstream core (yt-dlp/yt-dlp#6539) by
    using the modern Next.js REST API.
    """

    _VALID_URL = (
        r"https?://(?:www\.)?ceskatelevize\.cz/porady/(?:[^/?#&]+/)*(?P<id>[^/#?]+)"
    )

    def _real_extract(self, url):
        playlist_id = self._match_id(url)
        webpage, urlh = self._download_webpage_handle(url, playlist_id)
        parsed_url = urllib.parse.urlparse(urlh.url)
        site_name = self._og_search_property(
            "site_name", webpage, fatal=False, default="Česká televize"
        )
        playlist_title = self._og_search_title(webpage, default=None)
        if site_name and playlist_title:
            playlist_title = re.split(
                rf"\s*[—|]\s*{site_name}", playlist_title, maxsplit=1
            )[0]
        playlist_description = self._og_search_description(webpage, default=None)
        if playlist_description:
            playlist_description = playlist_description.replace("\xa0", " ")

        if "/porady/" not in parsed_url.path:
            raise ExtractorError('Only "porady" supported.')

        next_data = self._search_nextjs_data(webpage, playlist_id)
        idec = traverse_obj(
            next_data,
            (
                "props",
                "pageProps",
                "data",
                ("show", "mediaMeta", "programme", "episode"),
                "idec",
            ),
            get_all=False,
        )
        _type = "idec"

        if "/cast/" in traverse_obj(next_data, ("page")):
            index_id = traverse_obj(next_data, ("query", "indexId"))
            _type = "index"
        if not idec:
            idec = traverse_obj(
                next_data,
                ("props", "pageProps", "data", "videobonusDetail", "bonusId"),
                get_all=False,
            )
            _type = "bonus"
        if not idec:
            raise ExtractorError("Failed to find IDEC id")

        try:
            if _type == "idec":
                api_response = self._download_json(
                    STREAM_DATA_MEDIA_API_URL_PREFIX
                    + idec
                    + STREAM_DATA_API_URL_POSTFIX,
                    idec,
                    note="Getting stream data media api json",
                )
            elif _type == "bonus":
                api_response = self._download_json(
                    STREAM_DATA_BONUS_API_URL_PREFIX
                    + idec
                    + STREAM_DATA_API_URL_POSTFIX,
                    "BO-" + idec,
                    note="Getting stream data bonus api json",
                )
            elif _type == "index":
                api_response = self._download_json(
                    STREAM_DATA_INDEX_API_URL_PREFIX
                    + index_id
                    + STREAM_DATA_API_URL_POSTFIX,
                    f"{idec}/{index_id}",
                    note="Getting stream data bonus api json",
                )
        except ExtractorError as ex:
            self.to_screen(f"Error: {ex.msg}")
            not_available_msg = "This content is not available. Possibly georestricted or license expired."
            raise ExtractorError(not_available_msg, expected=True) from ex

        show_title = traverse_obj(
            next_data, ("props", "pageProps", "data", "mediaMeta", "show", "title")
        ) or traverse_obj(next_data, ("props", "pageProps", "data", "show", "title"))
        if not show_title:
            slug_m = re.search(r"/porady/(?:\d+-)?([^/?#]+)", parsed_url.path)
            show_title = slug_m.group(1).replace("-", " ") if slug_m else "Show"

        apollo_ep = (
            traverse_obj(
                next_data, ("props", "apolloState", f"EpisodePreview:{playlist_id}")
            )
            or {}
        )
        season_title = traverse_obj(apollo_ep, ("season", "title"))
        ep_raw_title = traverse_obj(apollo_ep, ("title",)) or playlist_title or ""

        entries = []
        for stream_index, stream in enumerate(api_response.get("streams", [])):
            stream_url = stream.get("url", "")
            if ".m3u8" in stream_url:
                stream_formats = self._extract_m3u8_formats(
                    stream_url, idec, "mp4", m3u8_id="hls", fatal=False
                )
            else:
                stream_formats = self._extract_mpd_formats(
                    stream_url, idec, fatal=False
                )

            if "drmOnly=true" in stream_url:
                for f in stream_formats:
                    f["has_drm"] = True

            title = api_response.get("title")
            duration = float_or_none(stream.get("duration"))
            thumbnail = api_response.get("previewImageUrl")

            subtitles = {}
            subs = stream.get("subtitles")
            if subs:
                subtitles = self.extract_subtitles(playlist_id, subs)

            final_title = playlist_title or title
            if len(api_response["streams"]) > 1:
                final_title = f"{final_title} {stream_index + 1}"

            entries.append(
                {
                    "id": playlist_id,
                    "title": final_title,
                    "description": playlist_description,
                    "thumbnail": thumbnail,
                    "duration": duration,
                    "formats": stream_formats,
                    "subtitles": subtitles,
                    "is_live": 0,
                    "series": show_title,
                    "season": season_title,
                    "_next_data": next_data,
                    "_raw_episode_title": ep_raw_title,
                    "_season_title": season_title,
                }
            )

        if len(entries) == 1:
            return entries[0]
        return self.playlist_result(
            entries, playlist_id, playlist_title, playlist_description
        )

    def _get_subtitles(self, episode_id, subs):
        url = None
        for sub in subs:
            if sub.get("language") == "ces":
                for file in sub.get("files", []):
                    if file.get("format") == "vtt":
                        url = file.get("url")
                        break
                break
        if url is None:
            return {}

        original_subtitles = self._download_webpage(
            url, episode_id, "Downloading subtitles"
        )
        vtt_subs = self._fix_subtitles(original_subtitles)
        return {
            "cs": [
                {
                    "ext": "vtt",
                    "data": vtt_subs,
                }
            ],
        }

    @staticmethod
    def _fix_subtitles(subtitles):
        """Convert millisecond-based subtitles to VTT."""

        def _msectotimecode(msec):
            components = []
            for divider in [1000, 60, 60, 100]:
                components.append(msec % divider)
                msec //= divider
            return f"{components[3]:02}:{components[2]:02}:{components[1]:02}.{components[0]:03}"

        def _fix_subtitle(subtitle):
            for line in subtitle.splitlines():
                m = re.match(r"^\s*([0-9]+);\s*([0-9]+)\s+([0-9]+)\s*$", line)
                if m:
                    yield m.group(1)
                    start, stop = (_msectotimecode(int(t)) for t in m.groups()[1:])
                    yield f"{start} --> {stop}"
                else:
                    yield line

        return "\r\n".join(_fix_subtitle(subtitles))
