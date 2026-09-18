import re
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
