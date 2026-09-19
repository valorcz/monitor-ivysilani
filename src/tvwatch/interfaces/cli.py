import argparse
import asyncio
import json
import os
import sys
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from tvwatch.core.config import CONFIG
from tvwatch.core.db import DuckRepo
from tvwatch.core.downloader import download_many
from tvwatch.core.logging import setup_logger
from tvwatch.core.net import assert_allowed_url
from tvwatch.core.scraper import sync_all_concurrent
from tvwatch.core.utils import (
    format_standardized_title,
    get_directory_size,
    is_playable,
    normalize_show_url,
)

console = Console()
err_console = Console(stderr=True)


def output_json(data: Any) -> None:
    try:
        encoded = json.dumps(data, indent=2, ensure_ascii=False, default=str)
        sys.stdout.buffer.write((encoded + "\n").encode("utf-8"))
        sys.stdout.flush()
    except BrokenPipeError:
        sys.exit(1)


def cmd_add(args: argparse.Namespace) -> None:
    setup_logger("EpisodeScraper", args.debug)
    try:
        assert_allowed_url(args.url)
    except ValueError as e:
        err_console.print(f"[bold red]✖ Error:[/] {e}")
        sys.exit(1)

    with DuckRepo(args.db) as repo:
        canonical_url = repo.add_or_reactivate_show(args.url)
        console.print(
            f"[bold green]✔[/] Added/Reactivated show: [bold cyan]{canonical_url}[/]"
        )


def cmd_disable(args: argparse.Namespace) -> None:
    setup_logger("EpisodeScraper", args.debug)
    with DuckRepo(args.db) as repo:
        if repo.disable_show(args.url):
            console.print(
                f"[bold yellow]⏸[/] Disabled tracking for show: [bold cyan]{args.url}[/]"
            )
        else:
            err_console.print(
                f"[bold red]✖[/] URL not found in database: [bold]{args.url}[/]"
            )
            sys.exit(1)


def cmd_remove(args: argparse.Namespace) -> None:
    setup_logger("EpisodeScraper", args.debug)
    with DuckRepo(args.db) as repo:
        if repo.delete_show(args.url):
            console.print(
                f"[bold red]🗑[/] Permanently removed show and episodes: [bold cyan]{args.url}[/]"
            )
        else:
            err_console.print(
                f"[bold red]✖[/] URL not found in database: [bold]{args.url}[/]"
            )
            sys.exit(1)


def cmd_list(args: argparse.Namespace) -> None:
    setup_logger("EpisodeScraper", args.debug)
    with DuckRepo(args.db) as repo:
        shows = repo.get_shows_summary(None if args.all else True)
        if args.json:
            output_json(
                [
                    {
                        "url": s["url"],
                        "active": s["is_active"],
                        "name": s["metadata"].get("name"),
                        "episodes_count": s["episodes_count"],
                    }
                    for s in shows
                ]
            )
            return

        if not shows:
            console.print(
                "[dim italic]No shows found.[/]"
                if args.all
                else "[dim italic]No active shows found. Add one with 'tvwatch add <url>'.[/]"
            )
            return

        table = Table(
            title="Watchlist Shows",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("Status", style="bold", no_wrap=True)
        table.add_column("Show Name", style="bold white")
        table.add_column("URL", style="cyan", overflow="fold")
        table.add_column("Episodes", justify="right", style="magenta")

        active_count = 0
        total_episodes = 0
        for s in shows:
            is_act = s["is_active"]
            if is_act:
                active_count += 1
            status_text = "[bold green]● Active[/]" if is_act else "[dim]○ Paused[/]"

            meta = s["metadata"] or {}
            raw_url = s["url"]
            name = meta.get("name") or (
                raw_url.split("/porady/")[1].rstrip("/")
                if "/porady/" in raw_url
                else raw_url
            )
            ep_count = s["episodes_count"]
            total_episodes += ep_count

            table.add_row(
                status_text,
                name,
                raw_url,
                str(ep_count),
            )

        footer_text = f"Total: {len(shows)} shows ({active_count} active), {total_episodes} tracked episodes"
        table.caption = f"[dim]{footer_text}[/]"
        console.print(table)


def cmd_episodes(args: argparse.Namespace) -> None:
    setup_logger("EpisodeScraper", args.debug)
    canonical_url = normalize_show_url(args.url)
    with DuckRepo(args.db) as repo:
        episodes = repo.get_show_episodes(canonical_url)

    if not episodes:
        if args.json:
            output_json([])
        else:
            console.print(f"[dim italic]No episodes found for show: {canonical_url}[/]")
        return

    if args.playable:
        episodes = [e for e in episodes if is_playable(e)]

    if args.json:
        output_json(episodes)
        return

    table = Table(
        title=f"Episodes: {canonical_url}",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    table.add_column("S#", style="dim cyan", no_wrap=True)
    table.add_column("Title", style="bold white")
    table.add_column("IDEC", style="dim", no_wrap=True)
    table.add_column("Broadcast", style="yellow", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Notified", justify="center", no_wrap=True)

    playable_count = 0
    for ep in episodes:
        meta = ep.get("metadata") or {}
        s_val = meta.get("season")
        idec_val = ep.get("idec") or "-"
        std_name = format_standardized_title(
            ep.get("name") or "Bez názvu", s_val, idec=idec_val
        )

        season_str = "-"
        if isinstance(s_val, dict) and s_val.get("title"):
            season_str = str(s_val.get("title"))
        elif s_val:
            season_str = str(s_val)

        bcast = "-"
        b_at = ep.get("broadcast_at")
        if b_at:
            if hasattr(b_at, "strftime"):
                bcast = b_at.strftime("%d.%m.%Y")
            elif isinstance(b_at, str):
                bcast = b_at[:10]

        playable = is_playable(ep)
        if playable:
            playable_count += 1
            card_avail = meta.get("cardLabels", {}).get("topLeft")
            status = (
                f"[bold green]✓ {card_avail}[/]"
                if card_avail
                else "[bold green]✓ Dostupné[/]"
            )
        else:
            status = "[dim red]✗ Vypršelo[/]"

        notified = "[green]✓[/]" if ep.get("last_notified_at") else "[dim]-[/]"

        table.add_row(
            season_str,
            std_name,
            idec_val,
            bcast,
            status,
            notified,
        )

    table.caption = (
        f"[dim]Showing {len(episodes)} episode(s) ({playable_count} playable)[/]"
    )
    console.print(table)


def cmd_status(args: argparse.Namespace) -> None:
    setup_logger("EpisodeScraper", args.debug)
    with DuckRepo(args.db) as repo:
        stats = repo.get_stats()

    storage_size = get_directory_size(CONFIG.DOWNLOAD_DIR)
    db_size = (
        f"{os.path.getsize(args.db) / (1024 * 1024):.2f} MB"
        if os.path.exists(args.db)
        else "N/A"
    )

    data = {
        **stats,
        "storage_size": storage_size,
        "download_dir": CONFIG.DOWNLOAD_DIR,
        "db_path": args.db,
        "db_size": db_size,
    }

    if args.json:
        output_json(data)
        return

    table = Table.grid(padding=(0, 2))
    table.add_column("Category", style="bold cyan")
    table.add_column("Metrics", style="white")

    table.add_row(
        "Shows",
        f"[bold]{stats['total_shows']}[/] total "
        f"([bold green]{stats['active_shows']}[/] active, [dim]{stats['inactive_shows']}[/] paused)",
    )
    table.add_row(
        "Episodes",
        f"[bold]{stats['total_episodes']}[/] tracked "
        f"([cyan]{stats['notified_episodes']}[/] notified)",
    )
    table.add_row(
        "Downloads",
        f"[bold green]{storage_size}[/] in [cyan]{CONFIG.DOWNLOAD_DIR}[/]",
    )
    table.add_row(
        "Database",
        f"[cyan]{args.db}[/] ({db_size})",
    )

    panel = Panel(
        table,
        title="[bold white]tvwatch System Status[/]",
        border_style="cyan",
        box=box.ROUNDED,
    )
    console.print(panel)


async def run_sync(repo: DuckRepo, logger, download_flag: bool = False):
    active_urls = repo.get_active_urls()
    if not active_urls:
        return []
    return await sync_all_concurrent(repo, logger)


async def run_download_with_progress(
    urls: list[str], logger
) -> list[tuple[str, bool, str]]:
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=None),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        total = len(urls)
        task = progress.add_task(f"Downloading (0/{total})...", total=total)

        async def _on_progress(idx: int, tot: int, url: str, status: bool | None):
            short_url = (
                url.split("/porady/")[1].rstrip("/") if "/porady/" in url else url
            )
            if status is None:
                progress.update(
                    task,
                    description=f"[bold blue]Downloading ({idx}/{tot}):[/] {short_url}",
                )
            else:
                progress.advance(task, 1)

        results = await download_many(urls, logger, progress_callback=_on_progress)
        progress.update(
            task,
            description=f"[bold green]Finished downloading {len(urls)} episode(s).[/]",
        )
        return results


def cmd_sync(args: argparse.Namespace) -> None:
    logger = setup_logger("EpisodeScraper", args.debug)
    with DuckRepo(args.db) as repo:
        if args.json:
            results = asyncio.run(run_sync(repo, logger, args.download))
            output_json([r.to_payload() for r in results])
            if results:
                new_urls = [
                    str(e.url) for r in results for e in r.new_episodes if e.url
                ]
                if args.download and new_urls:
                    asyncio.run(download_many(new_urls, logger))
                repo.mark_episodes_notified(new_urls)
            return

        with console.status(
            "[bold cyan]Synchronizing watchlist with Česká televize...[/]",
            spinner="dots",
        ):
            results = asyncio.run(run_sync(repo, logger, args.download))

        if not results:
            active_count = len(repo.get_active_urls())
            console.print(
                f"[bold green]✔[/] Watchlist is up to date. "
                f"No new episodes found across [bold]{active_count}[/] active show(s)."
            )
            return

        total_new = sum(len(r.new_episodes) for r in results)
        console.print(
            f"[bold green]★ Found {total_new} new episode(s) across {len(results)} show(s)![/]\n"
        )

        table = Table(
            title="Discovered Episodes",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("Show", style="bold white")
        table.add_column("Episode", style="green")
        table.add_column("Date", style="yellow")
        table.add_column("URL", style="cyan", overflow="fold")

        new_urls: list[str] = []
        for r in results:
            show_name = (
                r.tv_series.name
                if hasattr(r, "tv_series") and r.tv_series and r.tv_series.name
                else "Show"
            )
            for ep in r.new_episodes:
                if ep.url:
                    new_urls.append(str(ep.url))
                date_str = ""
                if hasattr(ep, "date") and ep.date:
                    date_str = getattr(ep.date, "label", "") or ""
                table.add_row(
                    show_name, ep.name or "Bez názvu", date_str, str(ep.url or "")
                )

        console.print(table)

        if args.download and new_urls:
            console.print(
                f"\n[bold blue]Initiating download of {len(new_urls)} newly discovered episode(s)...[/]"
            )
            asyncio.run(run_download_with_progress(new_urls, logger))

        repo.mark_episodes_notified(new_urls)


def cmd_download(args: argparse.Namespace) -> None:
    logger = setup_logger("EpisodeScraper", args.debug)
    for u in args.urls:
        try:
            assert_allowed_url(u)
        except ValueError as e:
            err_console.print(f"[bold red]✖ Invalid URL '{u}':[/] {e}")
            sys.exit(1)

    console.print(f"[bold blue]Starting download of {len(args.urls)} episode(s)...[/]")
    results = asyncio.run(run_download_with_progress(args.urls, logger))
    ok_count = sum(1 for _, ok, _ in results if ok)
    failed_count = len(results) - ok_count

    if failed_count == 0:
        console.print(
            f"[bold green]✔ All {ok_count} episode(s) downloaded successfully.[/]"
        )
    else:
        err_console.print(
            f"[bold yellow]⚠ Finished with errors: {ok_count} succeeded, {failed_count} failed.[/]"
        )


def cmd_standardize(args: argparse.Namespace) -> None:
    setup_logger("EpisodeScraper", args.debug)
    with DuckRepo(args.db) as repo:
        count = repo.standardize_all_episodes(args.url)
        console.print(
            f"[bold green]✔[/] Standardized [bold]{count}[/] episode name(s) in database."
        )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Manage and sync a watchlist of TV shows.")
    p.add_argument("--debug", action="store_true", help="Enable DEBUG logging")
    p.add_argument("--db", default=CONFIG.DB_PATH, help="Path to DuckDB database")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("add", help="Add a show URL")
    sp.add_argument("url", help="URL of the show on Česká televize")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("disable", help="Disable tracking for a show URL")
    sp.add_argument("url", help="URL of the show to disable")
    sp.set_defaults(func=cmd_disable)

    sp = sub.add_parser("remove", help="Permanently remove a show and its history")
    sp.add_argument("url", help="URL of the show to remove")
    sp.set_defaults(func=cmd_remove)

    sp = sub.add_parser("list", help="List shows (defaults to active shows)")
    sp.add_argument("--all", action="store_true", help="Include disabled shows")
    sp.add_argument("--json", action="store_true", help="Output JSON")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("episodes", help="List tracked episodes for a show")
    sp.add_argument("url", help="Show URL")
    sp.add_argument(
        "--playable", action="store_true", help="Only show available/playable episodes"
    )
    sp.add_argument("--json", action="store_true", help="Output JSON")
    sp.set_defaults(func=cmd_episodes)

    sp = sub.add_parser(
        "status", aliases=["stats"], help="Show database & storage metrics"
    )
    sp.add_argument("--json", action="store_true", help="Output JSON")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("sync", help="Crawl active shows and output new episodes")
    sp.add_argument(
        "--download", action="store_true", help="Download newly discovered episodes"
    )
    sp.add_argument("--json", action="store_true", help="Output JSON")
    sp.set_defaults(func=cmd_sync)

    sp = sub.add_parser("download", help="Download one or more episode URLs")
    sp.add_argument("urls", nargs="+", help="Episode page URLs to download")
    sp.set_defaults(func=cmd_download)

    sp = sub.add_parser(
        "standardize",
        help="Standardize episode names in database (SxxEyy - Title)",
    )
    sp.add_argument("--url", default=None, help="Optional show URL to filter by")
    sp.set_defaults(func=cmd_standardize)
    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
