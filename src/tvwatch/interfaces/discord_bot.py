import os
import glob
import sys
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks

from tvwatch.core.config import CONFIG
from tvwatch.core.logging import setup_logger
from tvwatch.core.db import DuckRepo
from tvwatch.core.scraper import sync_all
from tvwatch.core.downloader import download_many
from tvwatch.core.scraper import sync_all_concurrent


logger = setup_logger("DiscordBot")


def ensure_dirs():
    os.makedirs(CONFIG.DATA_DIR, exist_ok=True)
    os.makedirs(CONFIG.DOWNLOAD_DIR, exist_ok=True)


def guild_db_path(guild_id: int) -> str:
    return os.path.join(CONFIG.DATA_DIR, f"guild_{guild_id}.duckdb")


class DownloadAllView(discord.ui.View):
    def __init__(self, ep_urls: list):
        super().__init__(timeout=None)
        self.ep_urls = ep_urls
        label = (
            "⬇️ Stáhnout" if len(ep_urls) == 1 else f"⬇️ Stáhnout vše ({len(ep_urls)})"
        )
        btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)
        btn.callback = self.download_all
        self.add_item(btn)

    async def download_all(self, interaction: discord.Interaction):
        btn: discord.ui.Button = self.children[0]  # type: ignore
        btn.label = "⏳ Stahuje se..."
        btn.style = discord.ButtonStyle.secondary
        btn.disabled = True
        await interaction.response.edit_message(view=self)

        results = await download_many(self.ep_urls, logger)
        ok = sum(1 for _, s, _ in results if s)
        btn.label = f"✅ Staženo ({ok}/{len(results)})"
        btn.style = discord.ButtonStyle.success
        await interaction.edit_original_response(view=self)


async def dispatch_notifications(new_data: list, target):
    for show in new_data:
        show_name = show.get("tv_series", {}).get("name", "Neznámý seriál")
        episodes = show.get("new_episodes", [])
        if not episodes:
            continue
        ep_urls = [ep.get("url") for ep in episodes if ep.get("url")]
        embed = discord.Embed(title=f"📺 {show_name}", color=5814783)
        if episodes and "image" in episodes[0]:
            embed.set_thumbnail(url=episodes[0]["image"])
        lines = ["**Nové epizody k dispozici:**\n"]
        for ep in episodes:
            name = ep.get("name", "Bez názvu")
            url = ep.get("url", "")
            lines.append(f"• [{name}]({url})")
        embed.description = "\n".join(lines)
        view = DownloadAllView(ep_urls=ep_urls)
        await target.send(embed=embed, view=view)


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
        for db_path in glob.glob(os.path.join(CONFIG.DATA_DIR, "guild_*.duckdb")):
            gid = os.path.splitext(os.path.basename(db_path))[0].split("_", 1)[1]
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
                # new = await asyncio.to_thread(sync_all, repo, logger)
                new = await sync_all_concurrent(repo, logger, max_concurrency=4)
                if new:
                    payload = [r.to_payload() for r in new]
                    await dispatch_notifications(payload, target=channel)

    @sync_loop.before_loop
    async def before_sync_loop(self):
        await self.wait_until_ready()


bot = TVScraperBot()


# ---- Autocomplete for /disable url ----
async def disable_url_autocomplete(interaction: discord.Interaction, current: str):
    db_path = guild_db_path(interaction.guild_id)
    with DuckRepo(db_path) as repo:
        shows = repo.list_shows(active=True)
    suggestions = [u for u, _ in shows if current.lower() in u.lower()]
    return [app_commands.Choice(name=u, value=u) for u in suggestions[:25]]


@bot.tree.command(
    name="set_channel", description="Nastaví tento kanál pro automatická upozornění"
)
@commands.has_permissions(administrator=True)
async def set_channel_cmd(interaction: discord.Interaction):
    db_path = guild_db_path(interaction.guild_id)
    with DuckRepo(db_path) as repo:
        repo.set_guild_value("notification_channel", str(interaction.channel_id))
    await interaction.response.send_message(
        f"✅ Automatická upozornění budou chodit sem: <#{interaction.channel_id}>"
    )


@bot.tree.command(name="add", description="Přidá nový seriál ke sledování")
async def add_cmd(interaction: discord.Interaction, url: str):
    db_path = guild_db_path(interaction.guild_id)
    with DuckRepo(db_path) as repo:
        repo.add_or_reactivate_show(url)
    await interaction.response.send_message(f"✅ Přidáno ke sledování:\n{url}")


@bot.tree.command(name="disable", description="Přestane seriál sledovat")
@app_commands.autocomplete(url=disable_url_autocomplete)
async def disable_cmd(interaction: discord.Interaction, url: str):
    db_path = guild_db_path(interaction.guild_id)
    with DuckRepo(db_path) as repo:
        ok = repo.disable_show(url)
    msg = (
        f"⏸️ Sledování pozastaveno:\n{url}"
        if ok
        else f"URL v databázi nenalezeno:\n{url}"
    )
    await interaction.response.send_message(msg)


@bot.tree.command(name="list", description="Zobrazí aktuálně sledované seriály")
async def list_cmd(interaction: discord.Interaction):
    db_path = guild_db_path(interaction.guild_id)
    with DuckRepo(db_path) as repo:
        shows = repo.list_shows(active=True)
    if not shows:
        await interaction.response.send_message("Žádné aktivně sledované seriály.")
        return
    lines = [f"• {url}" for url, _ in shows]
    await interaction.response.send_message(
        "**Aktivně sledované:**\n" + "\n".join(lines)
    )


@bot.tree.command(name="sync", description="Okamžitě zkontroluje nové epizody")
async def sync_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    db_path = guild_db_path(interaction.guild_id)
    with DuckRepo(db_path) as repo:
        new = await asyncio.to_thread(sync_all, repo, logger)
    if not new:
        await interaction.followup.send("✅ Kontrola dokončena. Žádné nové epizody.")
        return
    payload = [r.to_payload() for r in new]
    await dispatch_notifications(payload, target=interaction.followup)


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
