from __future__ import annotations

import contextlib
import json
import threading
from datetime import UTC, datetime, timedelta
from typing import Any, Self

import duckdb

from .config import CONFIG
from .utils import (
    canonical_episode_url,
    extract_episode_id,
    format_standardized_title,
    normalize_show_url,
)


class DuckRepo:
    """
    Encapsulates DuckDB access and schema.
    Stores/loads JSON from Pydantic-ready dicts.
    Thread-safe for concurrent operations across worker threads.
    """

    def __init__(self, db_path: str, initialize: bool = True):
        self.conn = duckdb.connect(db_path)
        self._lock = threading.RLock()
        if initialize:
            self.init_schema()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb):
        self.conn.close()

    def init_schema(self) -> None:
        with self._lock:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tv_shows (
                  url VARCHAR PRIMARY KEY,
                  is_active BOOLEAN DEFAULT true,
                  metadata JSON,
                  added_at TIMESTAMPTZ,
                  last_seen_at TIMESTAMPTZ
                );
            """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episodes (
                  url VARCHAR PRIMARY KEY,
                  show_url VARCHAR,
                  idec VARCHAR,
                  name VARCHAR,
                  metadata JSON,
                  first_discovered_at TIMESTAMPTZ,
                  last_notified_at TIMESTAMPTZ,
                  broadcast_at TIMESTAMPTZ
                );
            """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_config (
                  key VARCHAR PRIMARY KEY,
                  value VARCHAR
                );
            """
            )

            # Check if migration has already been executed
            migrated = self.get_guild_value("schema_migrated_v1")
            if not migrated:
                # Migrations for existing databases
                for col_def in [
                    ("idec", "VARCHAR"),
                    ("last_notified_at", "TIMESTAMPTZ"),
                    ("broadcast_at", "TIMESTAMPTZ"),
                ]:
                    with contextlib.suppress(duckdb.Error):
                        self.conn.execute(
                            f"ALTER TABLE episodes ADD COLUMN IF NOT EXISTS {col_def[0]} {col_def[1]}"
                        )

                # Backfill legacy rows with extracted idec and preserve notified state
                with contextlib.suppress(duckdb.Error):
                    self.conn.execute(
                        "UPDATE episodes SET idec = regexp_extract(url, '/([0-9]{10,20})/?$', 1) WHERE idec IS NULL"
                    )

                with contextlib.suppress(duckdb.Error):
                    self.conn.execute(
                        "UPDATE episodes SET last_notified_at = first_discovered_at WHERE last_notified_at IS NULL AND first_discovered_at IS NOT NULL"
                    )

                self.set_guild_value("schema_migrated_v1", "1")

    # --- TV shows ---
    def add_or_reactivate_show(self, url: str) -> str:
        canonical_url = normalize_show_url(url)
        now = datetime.now(UTC)
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO tv_shows (url, is_active, added_at)
                VALUES (?, true, ?)
                ON CONFLICT (url) DO UPDATE SET is_active = true
            """,
                [canonical_url, now],
            )
        return canonical_url

    def insert_show(self, url: str, metadata: dict[str, Any] | None = None) -> str:
        """Helper to add or update show metadata."""
        canonical_url = normalize_show_url(url)
        with self._lock:
            self.add_or_reactivate_show(canonical_url)
            if metadata:
                self.update_show_metadata(canonical_url, metadata)
        return canonical_url

    def disable_show(self, url: str) -> bool:
        canonical_url = normalize_show_url(url)
        with self._lock:
            row = self.conn.execute(
                "UPDATE tv_shows SET is_active = false WHERE url = ? RETURNING url",
                [canonical_url],
            ).fetchone()
            if not row and canonical_url != url:
                row = self.conn.execute(
                    "UPDATE tv_shows SET is_active = false WHERE url = ? RETURNING url",
                    [url],
                ).fetchone()
            return bool(row)

    def list_shows(self, active: bool | None = None) -> list[tuple[str, bool]]:
        with self._lock:
            if active is None:
                q = "SELECT url, is_active FROM tv_shows ORDER BY url"
                rows = self.conn.execute(q).fetchall()
            else:
                q = "SELECT url, is_active FROM tv_shows WHERE is_active = ? ORDER BY url"
                rows = self.conn.execute(q, [active]).fetchall()
            return [(r[0], bool(r[1])) for r in rows]

    def get_active_urls(self) -> list[str]:
        with self._lock:
            return [
                r[0]
                for r in self.conn.execute(
                    "SELECT url FROM tv_shows WHERE is_active = true"
                ).fetchall()
            ]

    def get_active_shows(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT url, metadata FROM tv_shows WHERE is_active = true"
            ).fetchall()
            return [
                {
                    "url": r[0],
                    "metadata": json.loads(r[1])
                    if r[1] and isinstance(r[1], str)
                    else r[1],
                }
                for r in rows
            ]

    def update_show_metadata(self, url: str, metadata: dict[str, Any]) -> None:
        canonical_url = normalize_show_url(url)
        now = datetime.now(UTC)
        with self._lock:
            self.conn.execute(
                """
                UPDATE tv_shows SET metadata = ?, last_seen_at = ? WHERE url = ?
            """,
                [json.dumps(metadata, ensure_ascii=False), now, canonical_url],
            )

    # --- Episodes ---
    def record_episode(
        self,
        show_url: str,
        url: str,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
        idec: str | None = None,
        broadcast_at: datetime | None = None,
        cooldown_days: int | None = None,
        backfill: bool = False,
    ) -> bool:
        """
        Inserts or updates an episode and determines if it is eligible for notification.
        Returns True if the episode should trigger a notification.
        """
        canonical_show = normalize_show_url(show_url)
        ep_id = idec or extract_episode_id(url)
        canonical_ep_url = (
            canonical_episode_url(canonical_show, ep_id) if ep_id else url
        )
        now = datetime.now(UTC)
        meta_dict = metadata or {}
        meta_json = json.dumps(meta_dict, ensure_ascii=False)
        effective_cooldown = (
            cooldown_days
            if cooldown_days is not None
            else CONFIG.NOTIFICATION_COOLDOWN_DAYS
        )

        with self._lock:
            # Check existing episode by canonical URL or IDEC
            existing = None
            if ep_id:
                existing = self.conn.execute(
                    "SELECT url, first_discovered_at, last_notified_at FROM episodes WHERE idec = ? OR url = ?",
                    [ep_id, canonical_ep_url],
                ).fetchone()
            else:
                existing = self.conn.execute(
                    "SELECT url, first_discovered_at, last_notified_at FROM episodes WHERE url = ?",
                    [canonical_ep_url],
                ).fetchone()

            if not existing or len(existing) < 3:
                # Brand new episode
                initial_notified = now if backfill else None
                self.conn.execute(
                    """
                    INSERT INTO episodes (url, show_url, idec, name, metadata, first_discovered_at, last_notified_at, broadcast_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (url) DO UPDATE SET
                        show_url = EXCLUDED.show_url,
                        idec = COALESCE(episodes.idec, EXCLUDED.idec),
                        name = COALESCE(EXCLUDED.name, episodes.name),
                        metadata = EXCLUDED.metadata,
                        broadcast_at = COALESCE(EXCLUDED.broadcast_at, episodes.broadcast_at)
                """,
                    [
                        canonical_ep_url,
                        canonical_show,
                        ep_id,
                        name,
                        meta_json,
                        now,
                        initial_notified,
                        broadcast_at,
                    ],
                )
                # Notify if not backfilled
                return not backfill

            # Episode exists already in database
            old_url, _, last_notified = (
                existing[0],
                existing[1],
                existing[2],
            )
            # Update metadata and canonical fields if needed
            self.conn.execute(
                """
                UPDATE episodes
                SET show_url = ?, idec = COALESCE(idec, ?), name = COALESCE(?, name),
                    metadata = ?, broadcast_at = COALESCE(?, broadcast_at)
                WHERE url = ?
            """,
                [canonical_show, ep_id, name, meta_json, broadcast_at, old_url],
            )

            if backfill:
                return False

            if last_notified is None:
                # Episode was discovered previously (e.g. backfilled) but never notified
                return True

            # Check notification cooldown / timeout
            if effective_cooldown <= 0:
                # 0 or negative cooldown means never re-notify
                return False

            cooldown_delta = timedelta(days=effective_cooldown)
            return now - last_notified > cooldown_delta

    def insert_new_episode(
        self,
        show_url: str,
        url: str,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """
        Backwards-compatible wrapper around record_episode.
        """
        return self.record_episode(
            show_url=show_url,
            url=url,
            name=name,
            metadata=metadata,
        )

    def mark_episodes_notified(self, urls: list[str]) -> None:
        """
        Marks the provided episode URLs as having been notified at the current timestamp.
        """
        if not urls:
            return
        now = datetime.now(UTC)
        with self._lock:
            for u in urls:
                # Match both raw URL and canonical URL
                ep_id = extract_episode_id(u)
                if ep_id:
                    self.conn.execute(
                        "UPDATE episodes SET last_notified_at = ? WHERE url = ? OR idec = ?",
                        [now, u, ep_id],
                    )
                else:
                    self.conn.execute(
                        "UPDATE episodes SET last_notified_at = ? WHERE url = ?",
                        [now, u],
                    )

    # --- Guild config ---
    def set_guild_value(self, key: str, value: str) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO guild_config (key, value) VALUES (?, ?)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
                [key, value],
            )

    def get_guild_value(self, key: str) -> str | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM guild_config WHERE key = ?", [key]
            ).fetchone()
            return row[0] if row else None

    def get_show_episodes(self, show_url: str) -> list[dict]:
        """
        Returns all tracked episodes for a specific show.
        """
        canonical_url = normalize_show_url(show_url)
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT url, idec, name, metadata, first_discovered_at, last_notified_at, broadcast_at
                FROM episodes
                WHERE show_url = ?
                ORDER BY url
            """,
                [canonical_url],
            ).fetchall()
            return [
                {
                    "url": r[0],
                    "idec": r[1],
                    "name": r[2],
                    "metadata": json.loads(r[3])
                    if r[3] and isinstance(r[3], str)
                    else (r[3] or {}),
                    "first_discovered_at": r[4],
                    "last_notified_at": r[5],
                    "broadcast_at": r[6],
                }
                for r in rows
            ]

    def delete_show(self, show_url: str) -> bool:
        """
        Permanently purges a show and its associated episodes from the database.
        """
        canonical_url = normalize_show_url(show_url)
        with self._lock:
            self.conn.execute(
                "DELETE FROM episodes WHERE show_url = ?",
                [canonical_url],
            )
            row = self.conn.execute(
                "DELETE FROM tv_shows WHERE url = ? RETURNING url",
                [canonical_url],
            ).fetchone()
            if not row and canonical_url != show_url:
                row = self.conn.execute(
                    "DELETE FROM tv_shows WHERE url = ? RETURNING url",
                    [show_url],
                ).fetchone()
            return bool(row)

    def mark_unplayable_except(
        self, show_url: str, active_ep_identifiers: set[str]
    ) -> int:
        """
        Marks all episodes of a show that are NOT in active_ep_identifiers as unplayable
        (playable=False in metadata).
        Returns the number of episodes marked unplayable.
        """
        canonical_show = normalize_show_url(show_url)
        count = 0
        with self._lock:
            rows = self.conn.execute(
                "SELECT url, idec, metadata FROM episodes WHERE show_url = ?",
                [canonical_show],
            ).fetchall()
            for r in rows:
                url, idec, meta_raw = r[0], r[1], r[2]
                if url not in active_ep_identifiers and (
                    not idec or idec not in active_ep_identifiers
                ):
                    meta = (
                        json.loads(meta_raw)
                        if meta_raw and isinstance(meta_raw, str)
                        else (meta_raw or {})
                    )
                    if meta.get("playable") is not False:
                        meta["playable"] = False
                        self.conn.execute(
                            "UPDATE episodes SET metadata = ? WHERE url = ?",
                            [json.dumps(meta, ensure_ascii=False), url],
                        )
                        count += 1
        return count

    def standardize_all_episodes(self, show_url: str | None = None) -> int:
        """
        Standardizes the 'name' column for episodes in the database using format_standardized_title.
        If show_url is provided, only standardizes episodes for that show.
        Returns the number of episodes updated.
        """
        query = "SELECT url, idec, name, metadata FROM episodes"
        params = []
        if show_url:
            query += " WHERE show_url = ?"
            params.append(normalize_show_url(show_url))

        count = 0
        with self._lock:
            rows = self.conn.execute(query, params).fetchall()
            for r in rows:
                url, idec, cur_name, meta_raw = r[0], r[1], r[2], r[3]
                meta = (
                    json.loads(meta_raw)
                    if meta_raw and isinstance(meta_raw, str)
                    else (meta_raw or {})
                )
                raw_title = meta.get("title") or meta.get("name") or cur_name or ""
                season = meta.get("season")
                std_name = format_standardized_title(raw_title, season, idec=idec)
                if std_name and std_name != cur_name:
                    self.conn.execute(
                        "UPDATE episodes SET name = ? WHERE url = ?",
                        [std_name, url],
                    )
                    count += 1
        return count

    def get_stats(self) -> dict:
        """
        Returns summary metrics for the database.
        """
        with self._lock:
            total_shows = self.conn.execute("SELECT COUNT(*) FROM tv_shows").fetchone()[
                0
            ]
            active_shows = self.conn.execute(
                "SELECT COUNT(*) FROM tv_shows WHERE is_active = true"
            ).fetchone()[0]
            inactive_shows = total_shows - active_shows
            total_episodes = self.conn.execute(
                "SELECT COUNT(*) FROM episodes"
            ).fetchone()[0]
            notified_episodes = self.conn.execute(
                "SELECT COUNT(*) FROM episodes WHERE last_notified_at IS NOT NULL"
            ).fetchone()[0]
            return {
                "total_shows": total_shows,
                "active_shows": active_shows,
                "inactive_shows": inactive_shows,
                "total_episodes": total_episodes,
                "notified_episodes": notified_episodes,
            }
