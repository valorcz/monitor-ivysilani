from dataclasses import dataclass, field
import logging
import time
from typing import Any
import requests

from tvwatch.core.config import CONFIG
from tvwatch.core.scraper import extract_next_data, extract_programme_id, extract_show_idec

logger = logging.getLogger(__name__)

CANARY_SHOW_URL = "https://www.ceskatelevize.cz/porady/1097181328-udalosti/"


@dataclass
class HealthStatus:
    ok: bool
    html_ok: bool
    graphql_ok: bool
    programme_id: str | None = None
    episodes_found: int = 0
    latency_ms: float = 0.0
    details: list[str] = field(default_factory=list)


def probe_upstream_health(
    session: requests.Session | None = None,
    canary_url: str = CANARY_SHOW_URL,
    timeout: int = 10,
) -> HealthStatus:
    """
    Performs a live canary assertion against ČT iVysílání.
    Validates:
      1. Webpage HTTP status and HTML structure (__NEXT_DATA__).
      2. Extraction of programmeId from Next.js payload.
      3. Execution of modern GraphQL query against api.ceskatelevize.cz.
      4. Integrity of returned episode fields (id, title, playable).
    """
    start = time.monotonic()
    s = session or requests.Session()
    headers = {
        "User-Agent": CONFIG.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    html_ok = False
    graphql_ok = False
    programme_id = None
    episodes_found = 0
    details: list[str] = []

    # 1. Probe HTML & Next.js extraction
    try:
        resp = s.get(canary_url, headers=headers, timeout=timeout)
        if not resp.ok:
            details.append(
                f"Webpage request failed with HTTP {resp.status_code} ({canary_url})"
            )
        else:
            next_data = extract_next_data(resp.text)
            if not next_data:
                details.append("No __NEXT_DATA__ found in canary webpage HTML.")
            else:
                programme_id = extract_programme_id(next_data)
                show_idec = extract_show_idec(next_data)
                if not programme_id and not show_idec:
                    details.append(
                        "Failed to extract programmeId or IDEC from __NEXT_DATA__."
                    )
                else:
                    html_ok = True
    except requests.RequestException as e:
        details.append(f"Network error requesting canary webpage: {e}")
    except Exception as e:  # noqa: BLE001
        details.append(f"Unexpected error parsing canary webpage: {e}")

    # 2. Probe GraphQL API
    if programme_id:
        query = f"""
        query {{
          programme(programmeId: "{programme_id}") {{
            episodes(limit: 5, offset: 0, onlyPlayable: true) {{
              totalCount
              items {{
                id
                title
                playable
              }}
            }}
          }}
        }}
        """
        gql_headers = {
            "User-Agent": CONFIG.USER_AGENT,
            "Accept": "application/json",
        }
        try:
            gql_resp = s.post(
                "https://api.ceskatelevize.cz/graphql/?client=iVysilaniWeb",
                json={"query": query},
                headers=gql_headers,
                timeout=timeout,
            )
            if not gql_resp.ok:
                details.append(
                    f"GraphQL endpoint returned HTTP {gql_resp.status_code}"
                )
            else:
                data = gql_resp.json()
                if "errors" in data and data["errors"]:
                    err_msgs = [e.get("message", str(e)) for e in data["errors"]]
                    details.append(f"GraphQL returned schema errors: {err_msgs}")
                else:
                    items = (
                        data.get("data", {})
                        .get("programme", {})
                        .get("episodes", {})
                        .get("items", [])
                    )
                    if not items:
                        details.append(
                            "GraphQL returned 0 episodes for active canary show."
                        )
                    else:
                        first = items[0]
                        if not first.get("id") or not first.get("title"):
                            details.append(
                                f"Episode structure missing expected fields (id/title): {first}"
                            )
                        else:
                            graphql_ok = True
                            episodes_found = len(items)
        except requests.RequestException as e:
            details.append(f"Network error contacting GraphQL API: {e}")
        except Exception as e:  # noqa: BLE001
            details.append(f"Unexpected error validating GraphQL response: {e}")
    else:
        details.append("Skipping GraphQL check because programmeId was not extracted.")

    latency_ms = (time.monotonic() - start) * 1000
    is_ok = html_ok and graphql_ok

    return HealthStatus(
        ok=is_ok,
        html_ok=html_ok,
        graphql_ok=graphql_ok,
        programme_id=programme_id,
        episodes_found=episodes_found,
        latency_ms=latency_ms,
        details=details,
    )
