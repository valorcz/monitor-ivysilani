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
    format_discord_timestamp,
    format_standardized_title,
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


async def execute_batch_download_with_progress(
    interaction: discord.Interaction,
    view: discord.ui.View,
    button: discord.ui.Button | None,
    urls: list[str],
) -> list[tuple[str, bool, str]]:
    total = len(urls)
    if button:
        button.label = f"Stahuje se (0/{total})..."
        button.disabled = True
        button.style = discord.ButtonStyle.secondary
    await interaction.response.edit_message(view=view)

    start_time = datetime.now()
    embed = discord.Embed(
        title="Hromadné stahování epizod",
        color=3447003,  # Blue
    )
    embed.add_field(
        name="Stav",
        value=f"Zahajování stahování (0/{total})...",
        inline=True,
    )
    embed.add_field(name="Průběh", value=f"0/{total} dokončeno", inline=True)
    embed.add_field(
        name="Cílové úložiště",
        value=f"`{CONFIG.DOWNLOAD_DIR}`",
        inline=True,
    )
    embed.add_field(
        name="Zahájeno",
        value=f"{format_discord_timestamp(start_time, 'T')} ({format_discord_timestamp(start_time, 'R')})",
        inline=False,
    )
    followup_msg = await interaction.followup.send(embed=embed)

    completed_ok = 0
    completed_failed = 0

    async def _progress(idx: int, tot: int, url: str, status: bool | None):
        nonlocal completed_ok, completed_failed
        if status is None:
            # Started downloading episode idx
            if button:
                button.label = f"Stahuje se ({idx}/{tot})..."
                try:
                    await interaction.edit_original_response(view=view)
                except Exception:
                    pass

            embed.set_field_at(
                0, name="Stav", value=f"Stahuje se {idx} z {tot}", inline=True
            )
            if len(embed.fields) > 4:
                embed.set_field_at(
                    4, name="Aktuální díl", value=f"<{url}>", inline=False
                )
            else:
                embed.add_field(
                    name="Aktuální díl", value=f"<{url}>", inline=False
                )
            try:
                await followup_msg.edit(embed=embed)
            except Exception:
                pass
        else:
            # Finished downloading episode idx
            if status:
                completed_ok += 1
            else:
                completed_failed += 1
            done_count = completed_ok + completed_failed
            embed.set_field_at(
                1,
                name="Průběh",
                value=f"{done_count}/{tot} ({completed_ok} úspěšně, {completed_failed} chyb)",
                inline=True,
            )
            try:
                await followup_msg.edit(embed=embed)
            except Exception:
                pass

    results = await download_many(urls, logger, progress_callback=_progress)
    elapsed = (datetime.now() - start_time).total_seconds()
    ok = sum(1 for _, s, _ in results if s)
    failed = len(results) - ok

    if button:
        button.label = f"Staženo ({ok}/{len(results)})"
        button.style = (
            discord.ButtonStyle.success
            if failed == 0
            else discord.ButtonStyle.secondary
        )
        try:
            await interaction.edit_original_response(view=view)
        except Exception:
            pass

    embed.title = "Hromadné stahování dokončeno"
    embed.color = (
        3066993 if failed == 0 else (15158332 if ok == 0 else 15105570)
    )
    embed.set_field_at(0, name="Stav", value="Dokončeno", inline=True)
    embed.set_field_at(
        1,
        name="Výsledek",
        value=f"{ok}/{len(results)} úspěšně staženo ({failed} chyb)",
        inline=True,
    )
    if len(embed.fields) > 4:
        embed.set_field_at(
            4, name="Celkový čas", value=f"{elapsed:.1f} s", inline=False
        )
    else:
        embed.add_field(
            name="Celkový čas", value=f"{elapsed:.1f} s", inline=False
        )
    try:
        await followup_msg.edit(embed=embed)
    except Exception:
        pass

    return results


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
        await execute_batch_download_with_progress(
            interaction, view=self, button=btn, urls=self.ep_urls
        )


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
        active_shows_meta = {
            s["url"]: s.get("metadata") or {} for s in repo.get_active_shows()
        }
    if not shows:
        await interaction.response.send_message(
            "Žádné evidované pořady v databázi.", ephemeral=True
        )
        return

    embed = discord.Embed(
        title="Evidované pořady",
        description=f"Celkem evidováno: **{len(shows)}** pořad(ů)\n\n",
        color=0x2B2D31,
    )

    lines = []
    for u, active in shows:
        meta = active_shows_meta.get(u) or {}
        show_name = meta.get("name") or (
            u.split("/porady/")[1].rstrip("/") if "/porady/" in u else u
        )
        status_text = "Aktivní sledování" if active else "Pozastaveno"
        lines.append(f"[**{show_name}**](<{u}>)\n-# Stav: {status_text}")

    embed.description += "\n\n".join(lines)
    await interaction.response.send_message(embed=embed)


class EpisodesView(discord.ui.View):
    def __init__(self, canonical_url: str, episodes: list[dict]):
        super().__init__(timeout=300)
        self.canonical_url = canonical_url
        self.all_episodes = episodes
        self.page = 0
        self.per_page = 10
        self.current_filter = (
            "playable" if any(self._is_playable(e) for e in episodes) else "all"
        )

        # Discover unique seasons
        self.seasons = []
        seen_seasons = set()
        for ep in episodes:
            s_obj = (ep.get("metadata") or {}).get("season")
            if isinstance(s_obj, dict):
                s_title = s_obj.get("title")
            elif isinstance(s_obj, str):
                s_title = s_obj
            else:
                s_title = None
            if s_title and s_title not in seen_seasons:
                seen_seasons.add(s_title)
                self.seasons.append(s_title)

        self._build_components()

    @staticmethod
    def _is_playable(ep: dict) -> bool:
        meta = ep.get("metadata") or {}
        card_labels = meta.get("cardLabels") or {}
        if card_labels.get("center") and "nemá práva" in str(
            card_labels.get("center")
        ).lower():
            return False
        if "playable" in meta:
            return bool(meta["playable"])
        if "isPlayable" in meta:
            return bool(meta["isPlayable"])
        return False

    def _get_filtered_episodes(self) -> list[dict]:
        if self.current_filter == "playable":
            return [e for e in self.all_episodes if self._is_playable(e)]
        elif self.current_filter == "all":
            return self.all_episodes
        else:
            return [
                e
                for e in self.all_episodes
                if ((e.get("metadata") or {}).get("season") or {}).get("title")
                == self.current_filter
                or (e.get("metadata") or {}).get("season") == self.current_filter
            ]

    def _build_components(self):
        self.clear_items()
        filtered = self._get_filtered_episodes()
        total_pages = max(1, (len(filtered) + self.per_page - 1) // self.per_page)
        if self.page >= total_pages:
            self.page = total_pages - 1

        # 1. Season / Playability Dropdown (Row 0)
        select_options = []
        playable_count = sum(1 for e in self.all_episodes if self._is_playable(e))
        select_options.append(
            discord.SelectOption(
                label=f"Pouze dostupné ({playable_count})",
                value="playable",
                default=(self.current_filter == "playable"),
                description="Zobrazit pouze epizody, které lze přehrát a stáhnout",
            )
        )
        select_options.append(
            discord.SelectOption(
                label=f"Všechny epizody ({len(self.all_episodes)})",
                value="all",
                default=(self.current_filter == "all"),
                description="Zobrazit kompletní archiv pořadu",
            )
        )
        for s in self.seasons[:23]:
            s_count = sum(
                1
                for e in self.all_episodes
                if ((e.get("metadata") or {}).get("season") or {}).get("title")
                == s
                or (e.get("metadata") or {}).get("season") == s
            )
            select_options.append(
                discord.SelectOption(
                    label=f"{s} ({s_count})",
                    value=s,
                    default=(self.current_filter == s),
                )
            )

        select = discord.ui.Select(
            placeholder="Filtrovat podle řady / dostupnosti...",
            options=select_options,
            row=0,
        )
        select.callback = self._on_select_filter
        self.add_item(select)

        # 2. Pagination buttons (Row 1)
        prev_btn = discord.ui.Button(
            label="Předchozí",
            style=discord.ButtonStyle.secondary,
            disabled=(self.page <= 0),
            row=1,
        )
        prev_btn.callback = self._on_prev
        self.add_item(prev_btn)

        page_btn = discord.ui.Button(
            label=f"{self.page + 1}/{total_pages}",
            style=discord.ButtonStyle.secondary,
            disabled=True,
            row=1,
        )
        self.add_item(page_btn)

        next_btn = discord.ui.Button(
            label="Další",
            style=discord.ButtonStyle.secondary,
            disabled=(self.page >= total_pages - 1),
            row=1,
        )
        next_btn.callback = self._on_next
        self.add_item(next_btn)

        # 3. Download Playable Button (Row 1)
        playable_in_filtered = [
            e["url"] for e in filtered if self._is_playable(e)
        ]
        dl_btn = discord.ui.Button(
            label=f"Stáhnout dostupné ({len(playable_in_filtered)})",
            style=discord.ButtonStyle.primary,
            disabled=(len(playable_in_filtered) == 0),
            row=1,
        )
        dl_btn.callback = self._on_download
        self.add_item(dl_btn)

    async def _on_select_filter(self, interaction: discord.Interaction):
        self.current_filter = interaction.data["values"][0]  # type: ignore
        self.page = 0
        self._build_components()
        await interaction.response.edit_message(
            embed=self.get_embed(), view=self
        )

    async def _on_prev(self, interaction: discord.Interaction):
        if self.page > 0:
            self.page -= 1
        self._build_components()
        await interaction.response.edit_message(
            embed=self.get_embed(), view=self
        )

    async def _on_next(self, interaction: discord.Interaction):
        self.page += 1
        self._build_components()
        await interaction.response.edit_message(
            embed=self.get_embed(), view=self
        )

    async def _on_download(self, interaction: discord.Interaction):
        filtered = self._get_filtered_episodes()
        playable_urls = [e["url"] for e in filtered if self._is_playable(e)]
        if not playable_urls:
            await interaction.response.send_message(
                "Žádné dostupné epizody ke stažení.", ephemeral=True
            )
            return

        dl_buttons = [
            item
            for item in self.children
            if isinstance(item, discord.ui.Button)
            and "Stáhnout" in (item.label or "")
        ]
        btn = dl_buttons[0] if dl_buttons else None
        await execute_batch_download_with_progress(
            interaction, view=self, button=btn, urls=playable_urls
        )

    def get_embed(self) -> discord.Embed:
        filtered = self._get_filtered_episodes()
        total = len(filtered)
        start = self.page * self.per_page
        end = start + self.per_page
        chunk = filtered[start:end]

        filter_label = (
            "Pouze dostupné"
            if self.current_filter == "playable"
            else (
                "Všechny epizody"
                if self.current_filter == "all"
                else self.current_filter
            )
        )

        embed = discord.Embed(
            title="Seznam epizod",
            color=0x2B2D31,
        )
        embed.description = (
            f"**Pořad:** <{self.canonical_url}>\n"
            f"**Filtr:** {filter_label} (zobrazeno {len(chunk)} z {total})\n\n"
        )

        if not chunk:
            embed.description += "*Žádné epizody neodpovídají zvolenému filtru.*"
            return embed

        lines = []
        for ep in chunk:
            s_val = (ep.get("metadata") or {}).get("season")
            idec_val = ep.get("idec")
            std_name = format_standardized_title(
                ep.get("name") or "Bez názvu", s_val, idec=idec_val
            )
            ep_url = ep.get("url") or self.canonical_url

            card_avail = (
                (ep.get("metadata") or {})
                .get("cardLabels", {})
                .get("topLeft")
            )
            if self._is_playable(ep):
                avail = (
                    card_avail.replace("\xa0", " ")
                    if card_avail
                    else "Dostupné"
                )
            else:
                avail = "Vypršelo"

            bcast = "-"
            b_at = ep.get("broadcast_at")
            if b_at:
                if isinstance(b_at, str):
                    bcast = b_at[:10]
                elif hasattr(b_at, "strftime"):
                    bcast = b_at.strftime("%d.%m.%Y")

            lines.append(
                f"[**{std_name}**](<{ep_url}>)\n-# {avail} • Vysíláno: {bcast}"
            )

        embed.description += "\n\n".join(lines)
        if total > self.per_page:
            total_pages = (total + self.per_page - 1) // self.per_page
            embed.set_footer(text=f"Stránka {self.page + 1} z {total_pages}")

        return embed

    def get_content(self) -> str:
        return self.get_embed().description or ""


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

    view = EpisodesView(canonical_url=canonical_url, episodes=episodes)
    await interaction.response.send_message(
        embed=view.get_embed(), view=view
    )


@bot.tree.command(
    name="download", description="Stáhne konkrétní epizodu podle URL"
)
async def download_cmd(interaction: discord.Interaction, url: str):
    try:
        assert_allowed_url(url)
    except Exception as e:
        await interaction.response.send_message(
            f"Neplatná URL adresa: {e}", ephemeral=True
        )
        return

    start_time = datetime.now()
    embed = discord.Embed(
        title="Stahování epizody",
        color=3447003,  # Blue
    )
    embed.add_field(name="URL", value=f"<{url}>", inline=False)
    embed.add_field(name="Stav", value="Probíhá stahování...", inline=True)
    embed.add_field(
        name="Cílové úložiště", value=f"`{CONFIG.DOWNLOAD_DIR}`", inline=True
    )
    embed.add_field(
        name="Zahájeno",
        value=f"{format_discord_timestamp(start_time, 'T')} ({format_discord_timestamp(start_time, 'R')})",
        inline=False,
    )
    await interaction.response.send_message(embed=embed)

    logger.info(f"Ad-hoc download requested via Discord for: {url}")
    _, success, err = await download_one(url, logger)
    elapsed = (datetime.now() - start_time).total_seconds()

    if success:
        embed.color = 3066993  # Green
        embed.set_field_at(1, name="Stav", value="Dokončeno", inline=True)
        embed.add_field(
            name="Doba stahování", value=f"{elapsed:.1f} s", inline=True
        )
        embed.add_field(
            name="Dokončeno",
            value=f"{format_discord_timestamp(datetime.now(), 'T')}",
            inline=False,
        )
        await interaction.edit_original_response(embed=embed)
    else:
        embed.color = 15158332  # Red
        embed.set_field_at(1, name="Stav", value="Selhalo", inline=True)
        embed.add_field(name="Doba běhu", value=f"{elapsed:.1f} s", inline=True)
        err_snippet = err[:500] if err else "Neznámá chyba při stahování."
        embed.add_field(
            name="Chyba",
            value=f"```text\n{err_snippet}\n```",
            inline=False,
        )
        await interaction.edit_original_response(embed=embed)


@bot.tree.command(
    name="status", description="Zobrazí statistiky sledování a úložiště"
)
async def status_cmd(interaction: discord.Interaction):
    db_path = guild_db_path(interaction.guild_id)
    with DuckRepo(db_path) as repo:
        stats = repo.get_stats()

    embed = discord.Embed(
        title="Statistiky systému",
        color=0x2B2D31,
    )
    embed.add_field(
        name="Pořady",
        value=(
            f"• Aktivní: **{stats['active_shows']}**\n"
            f"• Pozastavené: **{stats['inactive_shows']}**\n"
            f"• Celkem: **{stats['total_shows']}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Epizody",
        value=(
            f"• Celkem evidováno: **{stats['total_episodes']}**\n"
            f"• Notifikováno: **{stats['notified_episodes']}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Úložiště",
        value=(
            f"• Využití: **{get_download_dir_size()}**\n"
            f"• Složka: `{CONFIG.DOWNLOAD_DIR}`"
        ),
        inline=False,
    )
    await interaction.response.send_message(embed=embed)


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

