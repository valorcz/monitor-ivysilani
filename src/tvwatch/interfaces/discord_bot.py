import asyncio
from datetime import datetime
import glob
import os
import sys

import discord
from discord import app_commands
from discord.ext import commands, tasks

from tvwatch.core.config import CONFIG
from tvwatch.core.db import DuckRepo
from tvwatch.core.downloader import download_many, download_one
from tvwatch.core.logging import setup_logger
from tvwatch.core.net import assert_allowed_url
from tvwatch.core.scraper import sync_all_concurrent, sync_one_show
from tvwatch.core.utils import (
    format_ascii_table,
    format_discord_timestamp,
    normalize_show_url,
)

logger = setup_logger("DiscordBot")


def ensure_dirs():
    os.makedirs(CONFIG.DATA_DIR, exist_ok=True)
    os.makedirs(CONFIG.DOWNLOAD_DIR, exist_ok=True)


def guild_db_path(guild_id: int | None) -> str:
    if guild_id is None:
        return os.path.join(CONFIG.DATA_DIR, "guild_default.duckdb")
    return os.path.join(CONFIG.DATA_DIR, f"guild_{guild_id}.duckdb")


def get_download_dir_size() -> str:
    if not os.path.exists(CONFIG.DOWNLOAD_DIR):
        return "0 MB"
    total_bytes = sum(
        os.path.getsize(os.path.join(dirpath, f))
        for dirpath, _, filenames in os.walk(CONFIG.DOWNLOAD_DIR)
        for f in filenames
    )
    if total_bytes > 1024**3:
        return f"{total_bytes / (1024**3):.2f} GB"
    return f"{total_bytes / (1024**2):.1f} MB"


class DownloadAllView(discord.ui.View):
    def __init__(self, ep_urls: list):
        super().__init__(timeout=None)
        self.ep_urls = ep_urls
        label = (
            "Stáhnout" if len(ep_urls) == 1 else f"Stáhnout vše ({len(ep_urls)})"
        )
        btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)
        btn.callback = self.download_all
        self.add_item(btn)

    async def download_all(self, interaction: discord.Interaction):
        btn: discord.ui.Button = self.children[0]  # type: ignore
        btn.label = "Stahuje se..."
        btn.style = discord.ButtonStyle.secondary
        btn.disabled = True
        await interaction.response.edit_message(view=self)

        results = await download_many(self.ep_urls, logger)
        ok = sum(1 for _, s, _ in results if s)
        btn.label = f"Staženo ({ok}/{len(results)})"
        btn.style = discord.ButtonStyle.success
        await interaction.edit_original_response(view=self)


async def dispatch_notifications(
    new_data: list, target, repo: DuckRepo | None = None
):
    all_notified_urls = []
    for show in new_data:
        show_name = show.get("tv_series", {}).get("name", "Neznámý seriál")
        episodes = show.get("new_episodes", [])
        if not episodes:
            continue
        ep_urls = [ep.get("url") for ep in episodes if ep.get("url")]
        all_notified_urls.extend(ep_urls)
        embed = discord.Embed(title=show_name, color=5814783)
        if episodes and "image" in episodes[0]:
            embed.set_thumbnail(url=episodes[0]["image"])
        lines = ["**Nové epizody k dispozici:**\n"]
        for ep in episodes:
            name = ep.get("name", "Bez názvu")
            url = ep.get("url", "")
            date_str = ""
            date_info = ep.get("date")
            if isinstance(date_info, dict) and date_info.get("datetime"):
                try:
                    dt = datetime.fromisoformat(
                        date_info["datetime"].replace("Z", "+00:00")
                    )
                    date_str = f" ({format_discord_timestamp(dt, 'R')})"
                except Exception:
                    pass
            lines.append(f"• [{name}]({url}){date_str}")
        embed.description = "\n".join(lines)
        view = DownloadAllView(ep_urls=ep_urls)
        await target.send(embed=embed, view=view)

    if repo and all_notified_urls:
        repo.mark_episodes_notified(all_notified_urls)


class TVScraperBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        self.sync_loop.start()

    @tasks.loop(hours=1)
    async def sync_loop(self):
        logger.info("Running scheduled multi-server sync...")
        for db_path in glob.glob(
            os.path.join(CONFIG.DATA_DIR, "guild_*.duckdb")
        ):
            gid = (
                os.path.splitext(os.path.basename(db_path))[0].split("_", 1)[1]
            )
            try:
                guild_id = int(gid)
            except ValueError:
                continue
            with DuckRepo(db_path) as repo:
                channel_id_str = repo.get_guild_value("notification_channel")
                if not channel_id_str:
                    logger.warning(
                        f"Guild {guild_id}: no notification channel configured"
                    )
                    continue
                channel = self.get_channel(int(channel_id_str))
                if not channel:
                    logger.error(
                        f"Guild {guild_id}: cannot access channel {channel_id_str}"
                    )
                    continue
                new = await sync_all_concurrent(repo, logger, max_concurrency=4)
                if new:
                    payload = [r.to_payload() for r in new]
                    await dispatch_notifications(
                        payload, target=channel, repo=repo
                    )

    @sync_loop.before_loop
    async def before_sync_loop(self):
        await self.wait_until_ready()


bot = TVScraperBot()


# ---- Autocomplete helpers ----
async def active_shows_autocomplete(
    interaction: discord.Interaction, current: str
):
    db_path = guild_db_path(interaction.guild_id)
    with DuckRepo(db_path) as repo:
        shows = repo.list_shows(active=True)
    suggestions = [u for u, _ in shows if current.lower() in u.lower()]
    return [app_commands.Choice(name=u, value=u) for u in suggestions[:25]]


async def all_shows_autocomplete(
    interaction: discord.Interaction, current: str
):
    db_path = guild_db_path(interaction.guild_id)
    with DuckRepo(db_path) as repo:
        shows = repo.list_shows(active=None)
    suggestions = [u for u, _ in shows if current.lower() in u.lower()]
    return [app_commands.Choice(name=u, value=u) for u in suggestions[:25]]


@bot.tree.command(
    name="set_channel",
    description="Nastaví tento kanál pro automatická upozornění",
)
@commands.has_permissions(administrator=True)
async def set_channel_cmd(interaction: discord.Interaction):
    db_path = guild_db_path(interaction.guild_id)
    with DuckRepo(db_path) as repo:
        repo.set_guild_value(
            "notification_channel", str(interaction.channel_id)
        )
    await interaction.response.send_message(
        f"Automatická upozornění budou chodit sem: <#{interaction.channel_id}>"
    )


@bot.tree.command(name="add", description="Přidá nový pořad ke sledování")
async def add_cmd(interaction: discord.Interaction, url: str):
    db_path = guild_db_path(interaction.guild_id)
    with DuckRepo(db_path) as repo:
        canonical_url = repo.add_or_reactivate_show(url)
        if not CONFIG.NOTIFY_ON_INITIAL_ADD:
            await asyncio.to_thread(
                sync_one_show, canonical_url, repo, logger, backfill=True
            )
    await interaction.response.send_message(
        f"Přidáno ke sledování:\n{canonical_url}"
    )


@bot.tree.command(name="disable", description="Pozastaví sledování pořadu")
@app_commands.autocomplete(url=active_shows_autocomplete)
async def disable_cmd(interaction: discord.Interaction, url: str):
    db_path = guild_db_path(interaction.guild_id)
    with DuckRepo(db_path) as repo:
        ok = repo.disable_show(url)
    msg = (
        f"Sledování pozastaveno:\n{url}"
        if ok
        else f"URL v databázi nenalezeno:\n{url}"
    )
    await interaction.response.send_message(msg)


@bot.tree.command(
    name="remove",
    description="Trvale odstraní pořad a jeho historii z databáze",
)
@app_commands.autocomplete(url=all_shows_autocomplete)
async def remove_cmd(interaction: discord.Interaction, url: str):
    db_path = guild_db_path(interaction.guild_id)
    with DuckRepo(db_path) as repo:
        ok = repo.delete_show(url)
    msg = (
        f"Pořad trvale odstraněn z databáze:\n{url}"
        if ok
        else f"URL v databázi nenalezeno:\n{url}"
    )
    await interaction.response.send_message(msg)


@bot.tree.command(name="list", description="Zobrazí aktuálně sledované pořady")
async def list_cmd(interaction: discord.Interaction):
    db_path = guild_db_path(interaction.guild_id)
    with DuckRepo(db_path) as repo:
        shows = repo.list_shows(active=None)
    if not shows:
        await interaction.response.send_message(
            "Žádné evidované pořady v databázi."
        )
        return

    headers = ["Pořad", "Stav"]
    rows = [
        [
            u.split("/porady/")[1].rstrip("/")
            if "/porady/" in u
            else u[:35],
            "Aktivní" if active else "Pozastaveno",
        ]
        for u, active in shows
    ]
    table = format_ascii_table(headers, rows)
    msg = f"**Evidované pořady:**\n```text\n{table}\n```"
    if len(msg) > 2000:
        lines = [
            f"• {u} ({'Aktivní' if active else 'Pozastaveno'})"
            for u, active in shows
        ]
        msg = "**Evidované pořady:**\n" + "\n".join(lines)
    await interaction.response.send_message(msg)


@bot.tree.command(
    name="episodes", description="Zobrazí evidované epizody pro vybraný pořad"
)
@app_commands.autocomplete(url=all_shows_autocomplete)
async def episodes_cmd(interaction: discord.Interaction, url: str):
    canonical_url = normalize_show_url(url)
    db_path = guild_db_path(interaction.guild_id)
    with DuckRepo(db_path) as repo:
        episodes = repo.get_show_episodes(canonical_url)

    if not episodes:
        await interaction.response.send_message(
            f"Pro pořad `{canonical_url}` nebyly nalezeny žádné uložené epizody."
        )
        return

    headers = ["Epizoda", "Vysíláno", "Notifikováno"]
    rows = []
    ep_urls = []
    for ep in episodes:
        ep_urls.append(ep["url"])
        bcast = "-"
        if ep.get("broadcast_at"):
            b_at = ep["broadcast_at"]
            if isinstance(b_at, str):
                bcast = b_at[:10]
            elif hasattr(b_at, "strftime"):
                bcast = b_at.strftime("%d.%m.%Y")
        notified = "Ano" if ep.get("last_notified_at") else "Ne"
        name = ep.get("name") or "Bez názvu"
        if len(name) > 35:
            name = name[:32] + "..."
        rows.append([name, bcast, notified])

    table = format_ascii_table(headers, rows)
    content = f"**Epizody pro pořad** <{canonical_url}> ({len(episodes)}):\n```text\n{table}\n```"
    view = DownloadAllView(ep_urls=ep_urls) if ep_urls else None

    if len(content) <= 2000:
        await interaction.response.send_message(content, view=view)
    else:
        # If table exceeds Discord 2000-char message limit, display top 15 + note
        short_rows = rows[:15]
        short_table = format_ascii_table(headers, short_rows)
        short_content = (
            f"**Epizody pro pořad** <{canonical_url}> (zobrazeno 15 z {len(episodes)}):\n"
            f"```text\n{short_table}\n```"
        )
        await interaction.response.send_message(short_content, view=view)


@bot.tree.command(
    name="download", description="Stáhne konkrétní epizodu podle URL"
)
async def download_cmd(interaction: discord.Interaction, url: str):
    try:
        assert_allowed_url(url)
    except Exception as e:
        await interaction.response.send_message(f"Neplatná URL adresa: {e}")
        return

    await interaction.response.defer()
    logger.info(f"Ad-hoc download requested via Discord for: {url}")
    _, success, err = await download_one(url, logger)
    if success:
        await interaction.followup.send(
            f"Stahování dokončeno úspěšně:\n{url}"
        )
    else:
        await interaction.followup.send(
            f"Stahování selhalo:\n{url}\nChyba: {err[:500]}"
        )


@bot.tree.command(
    name="status", description="Zobrazí statistiky sledování a úložiště"
)
async def status_cmd(interaction: discord.Interaction):
    db_path = guild_db_path(interaction.guild_id)
    with DuckRepo(db_path) as repo:
        stats = repo.get_stats()

    headers = ["Metrika", "Hodnota"]
    rows = [
        ["Aktivní pořady", str(stats["active_shows"])],
        ["Pozastavené pořady", str(stats["inactive_shows"])],
        ["Celkem evidováno pořadů", str(stats["total_shows"])],
        ["Celkem evidováno epizod", str(stats["total_episodes"])],
        ["Z toho notifikováno", str(stats["notified_episodes"])],
        ["Využití úložiště", get_download_dir_size()],
    ]
    table = format_ascii_table(headers, rows)
    await interaction.response.send_message(
        f"**Statistiky systému:**\n```text\n{table}\n```"
    )


@bot.tree.command(name="sync", description="Okamžitě zkontroluje nové epizody")
async def sync_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    db_path = guild_db_path(interaction.guild_id)
    with DuckRepo(db_path) as repo:
        new = await sync_all_concurrent(repo, logger, max_concurrency=4)
        if not new:
            await interaction.followup.send(
                "Kontrola dokončena. Žádné nové epizody."
            )
            return
        payload = [r.to_payload() for r in new]
        await dispatch_notifications(
            payload, target=interaction.followup, repo=repo
        )


def main():
    ensure_dirs()
    token = CONFIG.DISCORD_BOT_TOKEN
    if not token:
        logger.error("Missing DISCORD_BOT_TOKEN (.env)")
        sys.exit(1)
    logger.info("Starting Discord Bot Daemon...")
    bot.run(token)


if __name__ == "__main__":
    main()

