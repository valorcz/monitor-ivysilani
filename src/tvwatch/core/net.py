import re
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from .config import CONFIG

_ALLOWED_RE = re.compile(CONFIG.ALLOWED_URL_RE)

def assert_allowed_url(url: str) -> None:
    if not _ALLOWED_RE.match(url or ""):
        raise ValueError("URL not allowed by policy")

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    reraise=True,
)
def robust_get(url: str, **kwargs) -> requests.Response:
    assert_allowed_url(url)
    kwargs.setdefault("timeout", CONFIG.HTTP_TIMEOUT)
    headers = kwargs.pop("headers", {})
    headers.setdefault("User-Agent", CONFIG.USER_AGENT)
    return requests.get(url, headers=headers, **kwargs)

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    reraise=True,
)
def robust_post(url: str, **kwargs) -> requests.Response:
    assert_allowed_url(url)
    kwargs.setdefault("timeout", CONFIG.HTTP_TIMEOUT)
    return requests.post(url, **kwargs)