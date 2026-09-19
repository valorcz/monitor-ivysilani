import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse, urlunparse


def redact_url_query(u: str) -> str:
    """
    Drop query parameters from logs (they might include tokens).
    """
    p = urlparse(u)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, "", p.fragment))


def normalize_show_url(url: str) -> str:
    """
    Normalizes a show URL to canonical form:
    https://www.ceskatelevize.cz/porady/<slug>/
    Strips query parameters, fragment, and subpaths like /dily, /bonus, /tvurci.
    """
    match = re.search(r"ceskatelevize\.cz/porady/([^/?#]+)", url)
    if not match:
        # Fallback to stripped scheme and netloc if pattern does not match
        p = urlparse(url)
        path = p.path.rstrip("/") + "/"
        netloc = p.netloc.lower()
        if netloc == "ceskatelevize.cz":
            netloc = "www.ceskatelevize.cz"
        return f"https://{netloc}{path}"
    slug = match.group(1).rstrip("/")
    return f"https://www.ceskatelevize.cz/porady/{slug}/"


def canonical_episode_url(show_url: str, episode_id: str) -> str:
    """
    Constructs the canonical episode URL:
    https://www.ceskatelevize.cz/porady/<slug>/<episode_id>/
    """
    base = normalize_show_url(show_url).rstrip("/")
    ep_id = str(episode_id).strip("/")
    return f"{base}/{ep_id}/"


def extract_episode_id(url: str) -> str | None:
    """
    Extracts the episode ID (usually 15 digits) from an episode URL.
    """
    match = re.search(r"/porady/[^/?#]+/(\d{10,20})/?", url)
    if match:
        return match.group(1)
    # Generic trailing number
    match_fallback = re.search(r"/(\d{10,20})/?$", url)
    return match_fallback.group(1) if match_fallback else None


CZECH_MONTHS = {
    "ledna",
    "února",
    "unora",
    "března",
    "brezna",
    "dubna",
    "května",
    "kvetna",
    "června",
    "cervna",
    "července",
    "cervence",
    "srpna",
    "září",
    "zari",
    "října",
    "rijna",
    "listopadu",
    "prosince",
}


def roman_to_int(roman: str) -> int | None:
    """
    Translates a Roman numeral string (e.g. 'VI', 'iv', 'XII') to an integer (e.g. 6, 4, 12).
    Returns None if the string is empty or contains non-Roman characters.
    """
    if not roman:
        return None
    roman_map = {
        "I": 1,
        "V": 5,
        "X": 10,
        "L": 50,
        "C": 100,
        "D": 500,
        "M": 1000,
    }
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


def parse_season_number(season_val: Any) -> int | None:
    """
    Parses the season number from a ČT season string or object.
    Supports Roman numerals ('VI. řada', 'I. řada') and Arabic numerals ('1. řada', 'Season 2').
    """
    if not season_val:
        return None
    if isinstance(season_val, dict):
        season_str = season_val.get("title") or season_val.get("name") or ""
    else:
        season_str = str(season_val)

    season_str = season_str.replace("\xa0", " ").strip()
    if not season_str:
        return None

    m_roman = re.search(
        r"(?:^|\b(?:řada|rada|season|série|serie)\s*)([IVXLCDM]+)\.?(?:\s*(?:řada|rada|season|série|serie)|$|\b)",
        season_str,
        re.IGNORECASE,
    )
    if m_roman:
        val = roman_to_int(m_roman.group(1))
        if val is not None:
            return val

    m_arabic = re.search(
        r"(?:^|\b(?:řada|rada|season|série|serie)\s*)(\d+)\.?(?:\s*(?:řada|rada|season|série|serie)|$|\b)",
        season_str,
        re.IGNORECASE,
    )
    if m_arabic:
        try:
            return int(m_arabic.group(1))
        except ValueError:
            pass

    return None


def parse_episode_title_and_number(title: str) -> tuple[int | None, str]:
    """
    Parses an episode title string, extracting the episode number (if present)
    and the clean episode title.
    Examples:
      '10/26 Kde se vzal Měsíc' -> (10, 'Kde se vzal Měsíc')
      '1. díl - Zrození'       -> (1, 'Zrození')
      '8. srpna 2005'          -> (None, '8. srpna 2005')
    """
    if not title:
        return None, ""
    clean = re.sub(r"\s+", " ", title.replace("\xa0", " ")).strip()
    if re.match(r"^S\d+(?:E\d+)?(?:\s*-\s*.*)?$", clean, re.IGNORECASE):
        return None, clean

    # 'E10 - Title' or 'E10'
    m_ep = re.match(r"^E(\d+)(?:[\s:\-–—]+(.*))?$", clean, re.IGNORECASE)
    if m_ep:
        return int(m_ep.group(1)), (m_ep.group(2) or "").strip()

    # '10/26 Title' or '10/26'
    m = re.match(r"^(\d+)/\d+(?:[\s:\-–—]+(.*))?$", clean)
    if m:
        return int(m.group(1)), (m.group(2) or "").strip()

    # '10. díl - Title' or '10. díl' or 'Díl 10 - Title'
    m = re.match(
        r"^(?:(\d+)\.\s*(?:díl|dil|epizoda|část|cast)|(?:díl|dil|epizoda|část|cast)\s*(\d+)\.?)(?:[\s:\-–—]+(.*))?$",
        clean,
        re.IGNORECASE,
    )
    if m:
        num = int(m.group(1) or m.group(2))
        return num, (m.group(3) or "").strip()

    # '10. Title' (excluding dates like '8. srpna 2005')
    m = re.match(r"^(\d+)\.[\s\-–—]+(.*)$", clean)
    if m:
        num_str, rest = m.group(1), m.group(2).strip()
        first_word = rest.split()[0].lower() if rest else ""
        if first_word not in CZECH_MONTHS:
            return int(num_str), rest

    return None, clean


def format_standardized_title(
    raw_title: str,
    season_val: Any = None,
    idec: str | None = None,
) -> str:
    """
    Formats a title into a standardized Plex/Kodi format:
      - Both season and episode: 'S06E10 - Kde se vzal Měsíc'
      - Season only: 'S06 - Kde se vzal Měsíc'
      - Episode only: 'E10 - Kde se vzal Měsíc'
      - Neither: preserves cleaned original title
    """
    if not raw_title:
        return ""
    clean = re.sub(r"\s+", " ", raw_title.replace("\xa0", " ")).strip()
    if re.match(r"^S\d+(?:E\d+)?(?:\s*-\s*.*)?$", clean, re.IGNORECASE):
        return clean

    season_num = parse_season_number(season_val)
    ep_num, clean_title = parse_episode_title_and_number(clean)

    # Fallback to 15-digit IDEC episode index if title didn't contain episode number
    if ep_num is None and idec and len(idec) == 15 and idec.isdigit():
        try:
            ep_num = int(idec[-4:])
        except ValueError:
            pass

    prefix = ""
    if season_num is not None and ep_num is not None:
        prefix = f"S{season_num:02d}E{ep_num:02d}"
    elif season_num is not None:
        prefix = f"S{season_num:02d}"
    elif ep_num is not None:
        prefix = f"E{ep_num:02d}"

    if prefix and clean_title:
        return f"{prefix} - {clean_title}"
    elif prefix:
        return prefix
    return clean_title


def format_ascii_table(headers: list[str], rows: list[list[str]]) -> str:
    """
    Renders an ASCII table with borders and padded columns suitable for Discord codeblocks.
    """
    if not headers and not rows:
        return ""

    num_cols = len(headers) if headers else (len(rows[0]) if rows else 0)
    col_widths = [len(h) for h in headers] if headers else [0] * num_cols

    string_rows = []
    for r in rows:
        str_r = [str(c) for c in r]
        if len(str_r) < num_cols:
            str_r.extend([""] * (num_cols - len(str_r)))
        for i, cell in enumerate(str_r[:num_cols]):
            col_widths[i] = max(col_widths[i], len(cell))
        string_rows.append(str_r)

    sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    lines = [sep]

    if headers:
        header_line = (
            "|"
            + "|".join(f" {h.ljust(col_widths[i])} " for i, h in enumerate(headers))
            + "|"
        )
        lines.append(header_line)
        lines.append(sep)

    for r in string_rows:
        row_line = (
            "|"
            + "|".join(f" {r[i].ljust(col_widths[i])} " for i in range(num_cols))
            + "|"
        )
        lines.append(row_line)

    lines.append(sep)
    return "\n".join(lines)


def format_discord_timestamp(dt: datetime | float, style: str = "R") -> str:
    """
    Formats a datetime or timestamp into a Discord dynamic timestamp tag, e.g. <t:1726700000:R>.
    Styles:
      'R': Relative ('in 2 hours', '3 days ago')
      'f': Short Date/Time ('September 18, 2026 10:30 PM')
      'F': Long Date/Time ('Friday, September 18, 2026 10:30 PM')
      'd': Short Date ('18/09/2026')
      'D': Long Date ('18 September 2026')
      't': Short Time ('22:30')
    """
    if isinstance(dt, datetime):
        ts = int(dt.timestamp())
    else:
        ts = int(dt)
    return f"<t:{ts}:{style}>"


def is_playable(ep: dict) -> bool:
    """
    Determines if an episode is currently playable / available for download.
    Checks cardLabels (e.g. 'nemá práva') and metadata flags 'playable' / 'isPlayable'.
    """
    meta = ep.get("metadata") or {}
    card_labels = meta.get("cardLabels") or {}
    if (
        card_labels.get("center")
        and "nemá práva" in str(card_labels.get("center")).lower()
    ):
        return False
    if "playable" in meta:
        return bool(meta["playable"])
    if "isPlayable" in meta:
        return bool(meta["isPlayable"])
    return False


def get_directory_size(path: str) -> str:
    """
    Computes total size of files inside directory in human-readable units (MB / GB).
    """
    import os

    if not os.path.exists(path):
        return "0 MB"
    total_bytes = sum(
        os.path.getsize(os.path.join(dirpath, f))
        for dirpath, _, filenames in os.walk(path)
        for f in filenames
    )
    if total_bytes > 1024**3:
        return f"{total_bytes / (1024**3):.2f} GB"
    return f"{total_bytes / (1024**2):.1f} MB"
