import re
import urllib.parse

from .common import InfoExtractor

from ..utils import (
    ExtractorError,
    float_or_none,
    traverse_obj,
)

STREAM_DATA_MEDIA_API_URL_PREFIX = "https://api.ceskatelevize.cz/video/v1/playlist-vod/v1/stream-data/media/external/"
STREAM_DATA_API_URL_POSTFIX = "?canPlayDrm=true"
STREAM_DATA_BONUS_API_URL_PREFIX = "https://api.ceskatelevize.cz/video/v1/playlist-vod/v1/stream-data/bonus/BO-"
STREAM_DATA_INDEX_API_URL_PREFIX = "https://api.ceskatelevize.cz/video/v1/playlist-vod/v1/stream-data/index/"


class CeskaTelevizeIE(InfoExtractor):
    _VALID_URL = r'https?://(?:www\.)?ceskatelevize\.cz/porady/(?:[^/?#&]+/)*(?P<id>[^/#?]+)'

    def _real_extract(self, url):
        playlist_id = self._match_id(url)
        webpage, urlh = self._download_webpage_handle(url, playlist_id)
        parsed_url = urllib.parse.urlparse(urlh.url)
        site_name = self._og_search_property('site_name', webpage, fatal=False, default='Česká televize')
        playlist_title = self._og_search_title(webpage, default=None)
        if site_name and playlist_title:
            playlist_title = re.split(rf'\s*[—|]\s*{site_name}', playlist_title, maxsplit=1)[0]
        playlist_description = self._og_search_description(webpage, default=None)
        if playlist_description:
            playlist_description = playlist_description.replace('\xa0', ' ')

        if '/porady/' not in parsed_url.path:
            raise ExtractorError('Only "porady" supported.')

        next_data = self._search_nextjs_data(webpage, playlist_id)
        idec = traverse_obj(next_data, ('props', 'pageProps', 'data', ('show', 'mediaMeta'), 'idec'), get_all=False)
        _type = "idec"

        if "/cast/" in traverse_obj(next_data, ('page')):
            indexId = traverse_obj(next_data, ('query', 'indexId'))
            _type = "index"
        if not idec:
            idec = traverse_obj(next_data, ('props', 'pageProps', 'data', 'videobonusDetail', 'bonusId'), get_all=False)
            _type = "bonus"
        if not idec:
            raise ExtractorError('Failed to find IDEC id')

        try:
            if _type == "idec":
                api_response = self._download_json(
                    STREAM_DATA_MEDIA_API_URL_PREFIX + idec + STREAM_DATA_API_URL_POSTFIX,
                    idec, note='Getting stream data media api json')
            elif _type == "bonus":
                api_response = self._download_json(
                    STREAM_DATA_BONUS_API_URL_PREFIX + idec + STREAM_DATA_API_URL_POSTFIX,
                    "BO-" + idec, note='Getting stream data bonus api json')
            elif _type == "index":
                api_response = self._download_json(
                    STREAM_DATA_INDEX_API_URL_PREFIX + indexId + STREAM_DATA_API_URL_POSTFIX,
                    idec + '/' + indexId, note='Getting stream data bonus api json')
        except ExtractorError as ex:
            self.to_screen("Error: %s" % ex.msg)
            NOT_AVAILABLE_STRING = 'This content is not available. Possibly georestricted or license expired.'
            raise ExtractorError(NOT_AVAILABLE_STRING, expected=True)

        # Extract metadata for Plex / Jellyfin standardized naming
        show_title = (
            traverse_obj(next_data, ('props', 'pageProps', 'data', 'mediaMeta', 'show', 'title'))
            or traverse_obj(next_data, ('props', 'pageProps', 'data', 'show', 'title'))
        )
        if not show_title:
            slug_m = re.search(r'/porady/(?:\d+-)?([^/?#]+)', parsed_url.path)
            show_title = slug_m.group(1).replace('-', ' ') if slug_m else 'Show'

        apollo_ep = traverse_obj(next_data, ('props', 'apolloState', f'EpisodePreview:{playlist_id}')) or {}
        season_title = traverse_obj(apollo_ep, ('season', 'title'))
        ep_raw_title = traverse_obj(apollo_ep, ('title',)) or playlist_title or ''

        season_num = None
        if season_title:
            m_roman = re.search(r'(?:^|\b(?:řada|rada|season|série|serie)\s*)([IVXLCDM]+)\.?', season_title, re.IGNORECASE)
            if m_roman:
                season_num = self._roman_to_int(m_roman.group(1))
            if season_num is None:
                m_arabic = re.search(r'(?:^|\b(?:řada|rada|season|série|serie)\s*)(\d+)\.?', season_title, re.IGNORECASE)
                if m_arabic:
                    season_num = int(m_arabic.group(1))

        clean_norm = re.sub(r'\s+', ' ', ep_raw_title.replace('\xa0', ' ')).strip()
        ep_num = None
        clean_ep_title = clean_norm

        m_ep = re.match(r'^(\d+)/\d+(?:[\s:\-–—]+(.*))?$', clean_norm)
        if m_ep:
            ep_num = int(m_ep.group(1))
            clean_ep_title = (m_ep.group(2) or '').strip()
        else:
            m_ep2 = re.match(r'^(?:(\d+)\.\s*(?:díl|dil|epizoda|část|cast)|(?:díl|dil|epizoda|část|cast)\s*(\d+)\.?)(?:[\s:\-–—]+(.*))?$', clean_norm, re.IGNORECASE)
            if m_ep2:
                ep_num = int(m_ep2.group(1) or m_ep2.group(2))
                clean_ep_title = (m_ep2.group(3) or '').strip()

        if ep_num is None and playlist_id and re.match(r'^\d{15}$', playlist_id):
            part_num = int(playlist_id[-4:])
            if 1 <= part_num <= 999:
                ep_num = part_num

        if show_title and clean_ep_title.endswith(f' - {show_title}'):
            clean_ep_title = clean_ep_title[:-len(f' - {show_title}')].strip()
        if show_title and clean_ep_title.startswith(f'{show_title} - '):
            clean_ep_title = clean_ep_title[len(f'{show_title} - '):].strip()

        clean_show_dir = self._sanitize_slug(show_title)
        effective_season = season_num if season_num is not None else 1
        clean_season_dir = f'season_{effective_season:02d}'
        ep_slug = self._sanitize_slug(clean_ep_title)
        if ep_num is not None:
            tag = f's{effective_season:02d}e{ep_num:02d}'
        else:
            tag = f's{effective_season:02d}'

        parts = [clean_show_dir, tag]
        if ep_slug:
            parts.append(ep_slug)
        clean_filename = '_'.join(parts)

        entries = []
        for stream_index, stream in enumerate(api_response['streams']):
            stream_formats = self._extract_mpd_formats(
                stream['url'], idec,
                # mpd_id=f'dash-{format_id}', fatal=False)
                fatal=False)
            if 'drmOnly=true' in stream['url']:
                for f in stream_formats:
                    f['has_drm'] = True

            title = api_response['title']

            duration = float_or_none(stream.get('duration'))
            thumbnail = api_response.get('previewImageUrl')

            subtitles = {}
            subs = stream.get('subtitles')
            if subs:
                subtitles = self.extract_subtitles(playlist_id, subs)

            final_title = playlist_title or title
            if len(api_response['streams']) > 1:
                final_title = "%s %d" % (final_title, stream_index+1)

            entries.append({
                'id': playlist_id,
                'title': final_title,
                'description': playlist_description,
                'thumbnail': thumbnail,
                'duration': duration,
                'formats': stream_formats,
                'subtitles': subtitles,
                'is_live': 0,
                'series': show_title,
                'season_number': effective_season,
                'episode_number': ep_num,
                'episode': clean_ep_title,
                'clean_show_dir': clean_show_dir,
                'clean_season_dir': clean_season_dir,
                'clean_filename': clean_filename,
            })

        if len(entries) == 1:
            return entries[0]
        return self.playlist_result(entries, playlist_id, playlist_title, playlist_description)

    @staticmethod
    def _sanitize_slug(text):
        if not text:
            return ''
        import unicodedata
        text = unicodedata.normalize('NFC', text.replace('\xa0', ' '))
        text = text.lower()
        text = re.sub(r'[/\\\?%*:|\"<>—–\-_.,;()\[\]{}!]+|\s+', '_', text)
        text = re.sub(r'_+', '_', text).strip('_')
        return text

    @staticmethod
    def _roman_to_int(roman):
        if not roman:
            return None
        roman_map = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
        val = 0
        prev_val = 0
        for ch in reversed(roman.strip().upper()):
            if ch not in roman_map:
                return None
            curr = roman_map[ch]
            if curr < prev_val:
                val -= curr
            else:
                val += curr
            prev_val = curr
        return val if val > 0 else None

    def _get_subtitles(self, episode_id, subs):
        url = None
        for sub in subs:
            if sub['language'] == 'ces':
                for file in sub['files']:
                    if file['format'] == 'vtt':
                        url = file['url']
                        break
                break
        if url is None:
            return {}

        original_subtitles = self._download_webpage(
            url, episode_id, 'Downloading subtitles')
        vtt_subs = self._fix_subtitles(original_subtitles)
        return {
            'cs': [{
                'ext': 'vtt',
                'data': vtt_subs,
            }],
        }

    @staticmethod
    def _fix_subtitles(subtitles):
        """ Convert millisecond-based subtitles to VTT """

        def _msectotimecode(msec):
            """ Helper utility to convert milliseconds to timecode """
            components = []
            for divider in [1000, 60, 60, 100]:
                components.append(msec % divider)
                msec //= divider
            return '{3:02}:{2:02}:{1:02}.{0:03}'.format(*components)

        def _fix_subtitle(subtitle):
            for line in subtitle.splitlines():
                m = re.match(r'^\s*([0-9]+);\s*([0-9]+)\s+([0-9]+)\s*$', line)
                if m:
                    yield m.group(1)
                    start, stop = (_msectotimecode(int(t)) for t in m.groups()[1:])
                    yield f'{start} --> {stop}'
                else:
                    yield line

        return '\r\n'.join(_fix_subtitle(subtitles))
