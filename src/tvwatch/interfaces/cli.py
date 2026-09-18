import argparse
import asyncio
import json
import sys

from tvwatch.core.config import CONFIG
from tvwatch.core.db import DuckRepo
from tvwatch.core.downloader import download_many
from tvwatch.core.logging import setup_logger
from tvwatch.core.scraper import sync_all_concurrent


def output_json(data) -> None:
    try:
        encoded = json.dumps(data, indent=2, ensure_ascii=False)
        sys.stdout.buffer.write((encoded + "\n").encode("utf-8"))
        sys.stdout.flush()
    except BrokenPipeError:
        sys.exit(1)


def cmd_add(args):
    logger = setup_logger("EpisodeScraper", args.debug)
    with DuckRepo(args.db) as repo:
        repo.add_or_reactivate_show(args.url)
        logger.info(f"Added/Reactivated show: {args.url}")


def cmd_disable(args):
    logger = setup_logger("EpisodeScraper", args.debug)
    with DuckRepo(args.db) as repo:
        if repo.disable_show(args.url):
            logger.info(f"Disabled tracking: {args.url}")
        else:
            logger.warning(f"URL not found in DB: {args.url}")


def cmd_list(args):
    setup_logger("EpisodeScraper", args.debug)
    with DuckRepo(args.db) as repo:
        shows = repo.list_shows(None if args.all else True)
        if args.json:
            output_json([{"url": u, "active": a} for (u, a) in shows])
        else:
            if not shows:
                print("No shows found." if args.all else "No active shows.")
                return
            for url, active in shows:
                print(f"- {url} [{'active' if active else 'disabled'}]")


# async def run_sync(repo, logger, download: bool):
#    results = await sync_all_concurrent(repo, logger)
#    return results


async def run_sync(repo, logger, download_flag=False):
    logger.info("Starting synchronization...")
    active_urls = repo.get_active_urls()

    if not active_urls:
        logger.info("No active shows to sync. Add some to your watchlist first!")
        return []

    logger.info(f"Found {len(active_urls)} active show(s). Syncing now...")
    results = await sync_all_concurrent(repo, logger)

    if results:
        total_new = sum(len(r.new_episodes) for r in results)
        logger.info(f"Found {total_new} new episode(s) across {len(results)} show(s)!")
    else:
        logger.info("No new episodes found.")

    return results


def cmd_sync(args):
    logger = setup_logger("EpisodeScraper", args.debug)
    with DuckRepo(args.db) as repo:
        results = asyncio.run(run_sync(repo, logger, args.download))
        output_json([r.to_payload() for r in results])

        if results:
            new_urls = [str(e.url) for r in results for e in r.new_episodes if e.url]
            if args.download and new_urls:
                logger.info(
                    f"Downloading {len(new_urls)} newly discovered episode(s)..."
                )
                asyncio.run(download_many(new_urls, logger))
            repo.mark_episodes_notified(new_urls)


def cmd_download(args):
    logger = setup_logger("EpisodeScraper", args.debug)
    asyncio.run(download_many(args.urls, logger))


def build_parser():
    p = argparse.ArgumentParser(description="Manage and sync a watchlist of TV shows.")
    p.add_argument("--debug", action="store_true", help="Enable DEBUG logging")
    p.add_argument("--db", default=CONFIG.DB_PATH, help="Path to DuckDB database")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("add", help="Add a show URL")
    sp.add_argument("url")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("disable", help="Disable tracking for a show URL")
    sp.add_argument("url")
    sp.set_defaults(func=cmd_disable)

    sp = sub.add_parser("list", help="List shows (defaults to active shows)")
    sp.add_argument("--all", action="store_true", help="Include disabled shows")
    sp.add_argument("--json", action="store_true", help="Output JSON")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("sync", help="Crawl active shows and output new episodes")
    sp.add_argument(
        "--download", action="store_true", help="Download newly discovered episodes"
    )
    sp.set_defaults(func=cmd_sync)

    sp = sub.add_parser("download", help="Download one or more episode URLs")
    sp.add_argument("urls", nargs="+", help="Episode page URLs to download")
    sp.set_defaults(func=cmd_download)
    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
