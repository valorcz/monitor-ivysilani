import asyncio
import contextlib
import json
import logging
import re
import time
from datetime import datetime
from typing import Any

import requests
from lxml import html
from pydantic import ValidationError

from .config import CONFIG
from .db import DuckRepo
from .models import Episode, SyncResult, TVSeries
from .utils import (
    canonical_episode_url,
    extract_episode_id,
    format_standardized_title,
    normalize_show_url,
    redact_url_query,
)

logger = logging.getLogger(__name__)


def extract_show_id(url: str) -> str:
    """Extracts the show ID (sidp) from the iVysilani URL."""
    match = re.search(r"/porady/(\d+)", url)
    if not match:
        raise ValueError(f"Could not extract show ID from URL: {url}")
    return match.group(1)


def _extract_schema_data(content: bytes | str) -> dict[str, dict[str, Any] | None]:
    """
    Parses JSON-LD schema objects (TVSeries and ItemList) for backward compatibility
    and fallback SSR scraping.
    """
    if isinstance(content, str):
        content = content.encode("utf-8")
    tree = html.fromstring(content)
    script_elements = tree.cssselect("script[type='application/ld+json']")
    tv_series_data, item_list_data = None, None

    for script in script_elements:
        if not script.text:
            continue
        try:
            data = json.loads(script.text)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict):
                item_type = item.get("@type", "")
                if "TVSeries" in item_type or item_type == "TVSeries":
                    tv_series_data = item
                elif "ItemList" in item_type or item_type == "ItemList":
                    item_list_data = item

    return {"series": tv_series_data, "list": item_list_data}


def extract_next_data(html_content: str) -> dict[str, Any]:
    """
    Extracts the __NEXT_DATA__ JSON script from page HTML.
    """
    try:
        tree = html.fromstring(html_content)
        script_elements = tree.cssselect("script#__NEXT_DATA__")
        if script_elements and script_elements[0].text:
            return json.loads(script_elements[0].text)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Failed to parse __NEXT_DATA__: {e}")
    return {}


def extract_show_idec(next_data: dict[str, Any]) -> str | None:
    """
    Extracts the 15-digit internal show IDEC from __NEXT_DATA__.
    """
    props = next_data.get("props", {})
    show_obj = props.get("pageProps", {}).get("data", {}).get("show", {})
    idec = show_obj.get("idec")
    if idec:
        return str(idec)

    # Search in apolloState
    apollo = props.get("apolloState", {})
    for value in apollo.values():
        if (
            isinstance(value, dict)
            and value.get("__typename") == "Show"
            and value.get("idec")
        ):
            return str(value["idec"])

    return None


_GRAPHQL_HASH_CACHE: dict[str, tuple[str, float]] = {}
_HASH_CACHE_TTL_SECONDS = 43200  # 12 hours


def clear_graphql_hash_cache() -> None:
    """Clears the persisted query hash cache."""
    _GRAPHQL_HASH_CACHE.clear()


def fetch_dynamic_graphql_hash(
    session: requests.Session,
    show_url: str,
    operation_name: str = "GetEpisodes",
    force_refresh: bool = False,
) -> str:
    """
    Scrapes Next.js JS chunks to extract current Apollo Persisted Query SHA-256 hash.
    Caches resolved hash for 12 hours. Falls back to CONFIG.GRAPHQL_PERSISTED_HASH on failure.
    """
    now = time.time()
    if not force_refresh and operation_name in _GRAPHQL_HASH_CACHE:
        cached_hash, cached_at = _GRAPHQL_HASH_CACHE[operation_name]
        if now - cached_at < _HASH_CACHE_TTL_SECONDS:
            logger.debug(f"Using cached dynamic hash for {operation_name}")
            return cached_hash

    dily_url = f"{show_url.rstrip('/')}/dily/"
    headers = {"User-Agent": CONFIG.USER_AGENT}

    try:
        response = session.get(dily_url, headers=headers, timeout=CONFIG.HTTP_TIMEOUT)
        if response.ok:
            tree = html.fromstring(response.content)
            script_urls = [
                s.get("src") for s in tree.cssselect("script[src]") if s.get("src")
            ]

            for s_url in script_urls:
                if not s_url.endswith(".js"):
                    continue
                try:
                    js_content = session.get(
                        s_url, headers=headers, timeout=CONFIG.HTTP_TIMEOUT
                    ).text
                except requests.RequestException:
                    continue

                if operation_name in js_content:
                    op_index = js_content.find(operation_name)
                    hash_matches = [
                        (m.group(1), m.start())
                        for m in re.finditer(r'["\']([a-f0-9]{64})["\']', js_content)
                    ]
                    if hash_matches:
                        hash_matches.sort(key=lambda x: abs(x[1] - op_index))
                        best_hash = hash_matches[0][0]
                        logger.debug(f"Found dynamic hash {best_hash} in {s_url}")
                        _GRAPHQL_HASH_CACHE[operation_name] = (best_hash, now)
                        return best_hash

    except Exception as e:  # noqa: BLE001
        logger.debug(f"Error resolving dynamic hash: {e}")

    logger.debug(f"Using fallback persisted query hash for {operation_name}")
    _GRAPHQL_HASH_CACHE[operation_name] = (CONFIG.GRAPHQL_PERSISTED_HASH, now)
    return CONFIG.GRAPHQL_PERSISTED_HASH


def fetch_all_episodes(
    session: requests.Session, show_idec: str, query_hash: str
) -> list[dict]:
    """
    Fetches all playable episodes via the GraphQL API using the persisted query hash.
    Paginates automatically until all playable episodes are returned.
    """
    base_url = "https://api.ceskatelevize.cz/graphql/"
    limit = 50
    offset = 0
    all_episodes = []

    headers = {
        "User-Agent": CONFIG.USER_AGENT,
        "Accept": "application/json",
    }

    while True:
        variables = {
            "limit": limit,
            "offset": offset,
            "idec": show_idec,
            "orderBy": "oldest",
            "onlyPlayable": True,
        }

        extensions = {"persistedQuery": {"version": 1, "sha256Hash": query_hash}}

        params = {
            "operationName": "GetEpisodes",
            "variables": json.dumps(variables),
            "extensions": json.dumps(extensions),
        }

        try:
            response = session.get(
                base_url, params=params, headers=headers, timeout=CONFIG.HTTP_TIMEOUT
            )
            response.raise_for_status()
            data = response.json()
            if "errors" in data and any(
                "PersistedQuery" in str(err) for err in data.get("errors", [])
            ):
                logger.warning(
                    "GraphQL persisted query hash rejected by server. Invalidating cache."
                )
                clear_graphql_hash_cache()
                break
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"GraphQL query failed at offset {offset} for show IDEC {show_idec}: {e}"
            )
            clear_graphql_hash_cache()
            break

        episodes_data = data.get("data", {}).get("episodesPreviewFind", {})
        items = episodes_data.get("items", [])
        total_count = episodes_data.get("totalCount", 0)

        if not items:
            break

        all_episodes.extend(items)
        offset += len(items)
        if offset >= total_count:
            break

    logger.info(f"Fetched {len(all_episodes)} playable episode(s) via GraphQL.")
    return all_episodes


def fallback_extract_episodes(
    next_data: dict[str, Any], html_content: str, canonical_show_url: str
) -> list[dict]:
    """
    Fallback extraction from Next.js apolloState and JSON-LD when GraphQL API is unavailable.
    """
    episodes = []
    seen_ids = set()

    # 1. From Next.js apolloState
    apollo = next_data.get("props", {}).get("apolloState", {})
    for val in apollo.values():
        if isinstance(val, dict) and val.get("__typename") == "EpisodePreview":
            ep_id = val.get("id")
            if ep_id and ep_id not in seen_ids:
                seen_ids.add(ep_id)
                episodes.append(val)

    # 2. From JSON-LD ItemList
    schema = _extract_schema_data(html_content)
    item_list = schema.get("list") or {}
    for el in item_list.get("itemListElement", []):
        raw = el.get("item", el).copy()
        raw_url = el.get("url") or raw.get("url", "")
        ep_id = extract_episode_id(raw_url)
        if ep_id and ep_id not in seen_ids:
            seen_ids.add(ep_id)
            episodes.append(
                {
                    "id": ep_id,
                    "title": raw.get("name") or el.get("name", "Unknown Title"),
                    "playable": True,
                    "url": raw_url,
                }
            )

    return episodes


def sync_one_show(
    show_url: str,
    repo: DuckRepo,
    logger=None,
    backfill: bool = False,
) -> SyncResult | None:
    """
    Main sync pipeline for a single show.
    Fetches show metadata, all playable episodes, updates DuckDB,
    and returns a SyncResult containing new episodes eligible for notification.
    """
    log = logger or logging.getLogger(__name__)
    canonical_url = normalize_show_url(show_url)
    log.info(f"Syncing show: {redact_url_query(canonical_url)}")

    session = requests.Session()
    headers = {
        "User-Agent": CONFIG.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        resp = session.get(canonical_url, headers=headers, timeout=CONFIG.HTTP_TIMEOUT)
        resp.raise_for_status()
        html_content = resp.text
    except Exception as e:  # noqa: BLE001
        log.error(f"Failed to fetch show page {redact_url_query(canonical_url)}: {e}")
        return None

    # Parse metadata & show IDEC
    next_data = extract_next_data(html_content)
    show_idec = extract_show_idec(next_data)

    schema_data = _extract_schema_data(html_content)
    tv_series_meta = schema_data.get("series") or {}

    show_title = None
    if next_data:
        show_obj = (
            next_data.get("props", {})
            .get("pageProps", {})
            .get("data", {})
            .get("show", {})
        )
        show_title = show_obj.get("title")

    if not show_title:
        show_title = tv_series_meta.get("name")

    series = TVSeries(
        url=canonical_url,
        name=show_title,
        metadata=tv_series_meta or {"name": show_title},
    )

    # Persist show in repository
    repo.insert_show(canonical_url, metadata=tv_series_meta or {"name": show_title})

    # Fetch episodes
    episodes_raw = []
    if show_idec:
        query_hash = fetch_dynamic_graphql_hash(session, canonical_url)
        episodes_raw = fetch_all_episodes(session, show_idec, query_hash)

    if not episodes_raw:
        log.debug(f"Falling back to SSR/JSON-LD episode extraction for {canonical_url}")
        episodes_raw = fallback_extract_episodes(next_data, html_content, canonical_url)

    # Upsert episodes and identify new ones for notification
    new_episodes: list[Episode] = []
    active_playable_ids = set()
    for ep in episodes_raw:
        ep_id = ep.get("id") or extract_episode_id(ep.get("url", ""))
        if not ep_id:
            continue

        ep_url = canonical_episode_url(canonical_url, ep_id)
        if ep.get("playable", True):
            active_playable_ids.add(ep_url)
            active_playable_ids.add(ep_id)

        raw_name = ep.get("title") or ep.get("name", "Unknown Title")
        ep_name = format_standardized_title(raw_name, ep.get("season"), idec=ep_id)

        broadcast_at = None
        date_info = ep.get("date")
        if isinstance(date_info, dict) and date_info.get("datetime"):
            with contextlib.suppress(ValueError, TypeError):
                broadcast_at = datetime.fromisoformat(date_info["datetime"])

        # Record in DuckDB
        should_notify = repo.record_episode(
            show_url=canonical_url,
            url=ep_url,
            name=ep_name,
            metadata=ep,
            idec=ep_id,
            broadcast_at=broadcast_at,
            backfill=backfill,
        )

        try:
            episode_model = Episode(
                url=ep_url,
                name=ep_name,
                idec=ep_id,
                metadata=ep,
            )
        except ValidationError as ex:
            log.warning(f"Validation error for episode {ep_url}: {ex}")
            continue

        if should_notify:
            new_episodes.append(episode_model)

    # Expire old episodes that are no longer playable on ČT
    if episodes_raw:
        expired_count = repo.mark_unplayable_except(canonical_url, active_playable_ids)
        if expired_count > 0:
            log.info(
                f"Marked {expired_count} expired episode(s) as unplayable for {redact_url_query(canonical_url)}"
            )

    # Standardize all existing episode names in DB for this show
    repo.standardize_all_episodes(canonical_url)

    if new_episodes:
        log.info(
            f"Detected {len(new_episodes)} new/notifiable episode(s) for {redact_url_query(canonical_url)}"
        )
    else:
        log.debug(f"No new episodes for {redact_url_query(canonical_url)}")

    return SyncResult(
        source_url=canonical_url, tv_series=series, new_episodes=new_episodes
    )


def sync_all(repo: DuckRepo, logger=None, backfill: bool = False) -> list[SyncResult]:
    """
    Synchronously syncs all active shows in the repository.
    """
    log = logger or logging.getLogger(__name__)
    results: list[SyncResult] = []
    active_urls = repo.get_active_urls()

    if not active_urls:
        log.info("No active shows to sync.")
        return results

    for url in active_urls:
        res = sync_one_show(url, repo, logger=log, backfill=backfill)
        if res and res.new_episodes:
            results.append(res)

    return results


async def sync_all_concurrent(
    target: Any,
    second_arg: Any = None,
    max_concurrency: int = 4,
    logger=None,
    backfill: bool = False,
) -> list[SyncResult]:
    """
    Runs sync_one_show concurrently using asyncio.to_thread with a semaphore.
    Supports both signatures:
      - sync_all_concurrent(repo: DuckRepo, logger=None, max_concurrency=4)
      - sync_all_concurrent(urls: list[str], repo: DuckRepo)
    """
    log = logger or logging.getLogger(__name__)

    if isinstance(target, list):
        urls = target
        repo = second_arg
    else:
        repo = target
        if second_arg and not isinstance(second_arg, (int, float)):
            log = second_arg
        urls = repo.get_active_urls()

    if not urls:
        log.info("No active shows to sync.")
        return []

    semaphore = asyncio.Semaphore(max_concurrency)

    async def run_for_url(url: str) -> SyncResult | None:
        async with semaphore:
            return await asyncio.to_thread(sync_one_show, url, repo, log, backfill)

    tasks = [asyncio.create_task(run_for_url(u)) for u in urls]
    finished = await asyncio.gather(*tasks, return_exceptions=True)

    results: list[SyncResult] = []
    for item in finished:
        if isinstance(item, Exception):
            log.error(f"Error during sync: {item}", exc_info=item)
            continue
        if item and item.new_episodes:
            results.append(item)

    return results
