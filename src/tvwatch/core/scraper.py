import asyncio
from typing import List, Dict, Any, Optional
import json
from lxml import html
import requests
from .net import robust_get
from .models import TVSeries, Episode, SyncResult
from .db import DuckRepo
from .utils import redact_url_query


def _extract_schema_data(content: bytes) -> Dict[str, Optional[Dict[str, Any]]]:
    tree = html.fromstring(content)
    script_elements = tree.cssselect("div#__next script")
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
            if isinstance(item, dict) and item.get("@context") == "https://schema.org":
                if item.get("@type") == "TVSeries":
                    tv_series_data = item
                elif item.get("@type") == "ItemList":
                    item_list_data = item

    return {"series": tv_series_data, "list": item_list_data}


def sync_one_show(url: str, repo: DuckRepo, logger) -> Optional[SyncResult]:
    try:
        resp = robust_get(url)
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch {redact_url_query(url)} after retries: {e}")
        return None

    data = _extract_schema_data(resp.content)
    tv_series_meta = data["series"] or {}
    item_list = data["list"] or {}

    # TV series model (url + optional name/metadata)
    series = TVSeries(url=url, name=tv_series_meta.get("name"), metadata=tv_series_meta)
    if tv_series_meta:
        repo.update_show_metadata(url, tv_series_meta)

    # Episodes → Pydantic validation & normalization
    new_eps: List[Episode] = []
    for el in item_list.get("itemListElement", []):
        raw = el.get("item", el).copy()
        ep_url = el.get("url") or raw.get("url")
        ep_name = raw.get("name") or el.get("name")
        if not ep_url:
            continue
        raw["url"] = ep_url
        if "position" in el:
            raw["position"] = el["position"]

        # validate with Pydantic (enforces https + allowlist)
        try:
            ep = Episode(
                url=raw["url"], name=ep_name, metadata=raw, position=raw.get("position")
            )
        except Exception as ex:
            logger.warning(
                f"Skipping invalid episode under {redact_url_query(url)}: {ex}"
            )
            continue

        if repo.insert_new_episode(show_url=url, name=ep.name, metadata=ep.metadata):
            new_eps.append(ep)

    if not tv_series_meta and not new_eps:
        logger.debug(f"No metadata or new episodes found for {redact_url_query(url)}")

    return SyncResult(source_url=url, tv_series=series, new_episodes=new_eps)


def sync_all(repo: DuckRepo, logger) -> List[SyncResult]:
    results: List[SyncResult] = []
    active_urls = repo.get_active_urls()
    if not active_urls:
        logger.info("No active shows to sync. Use 'add' to start tracking.")
        return results

    for url in active_urls:
        logger.info(f"Syncing: {redact_url_query(url)}")
        r = sync_one_show(url, repo, logger)
        if r and r.new_episodes:
            logger.info(
                f"Detected {len(r.new_episodes)} new episode(s) for {redact_url_query(url)}"
            )
            results.append(r)
    return results


async def sync_all_concurrent(
    repo: DuckRepo, logger, max_concurrency: int = 4
) -> List[SyncResult]:
    """
    Run sync_one_show concurrently with a maximum concurrency limit.
    Uses asyncio.to_thread so sync_one_show can remain synchronous.
    """

    semaphore = asyncio.Semaphore(max_concurrency)
    results: List[SyncResult] = []

    async def run_for_url(url: str):
        async with semaphore:
            logger.info("Starting sync: %s", url)
            # run sync_one_show in a worker thread
            result = await asyncio.to_thread(sync_one_show, url, repo, logger)
            if result and result.new_episodes:
                logger.info(
                    "Finished sync: %s -> new episodes: %d",
                    url,
                    len(result.new_episodes),
                )
            return result

    active_urls = repo.get_active_urls()
    if not active_urls:
        logger.info("No active shows to sync.")
        return results

    tasks = [asyncio.create_task(run_for_url(url)) for url in active_urls]
    finished = await asyncio.gather(*tasks, return_exceptions=False)

    results = []
    for item in finished:
        if isinstance(item, Exception):
            logger.error("Error during sync: %s", item)
            continue
        results.append(item)
    return results
    # Filter out None results
    # return [r for r in finished if r]
