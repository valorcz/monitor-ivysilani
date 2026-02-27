from urllib.parse import urlparse, urlunparse

def redact_url_query(u: str) -> str:
    """
    Drop query parameters from logs (they might include tokens).
    """
    p = urlparse(u)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, "", p.fragment))