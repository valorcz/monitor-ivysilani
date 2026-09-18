from __future__ import annotations
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timezone, timedelta
import json
import duckdb
from .utils import normalize_show_url, canonical_episode_url, extract_episode_id
from .config import CONFIG


class DuckRepo:
    """
    Encapsulates DuckDB access and schema.
    Stores/loads JSON from Pydantic-ready dicts.
    """

    def __init__(self, db_path: str, initialize: bool = True):
        self.conn = duckdb.connect(db_path)
        if initialize:
            self.init_schema()

    def __enter__(self) -> "DuckRepo":
        return self

    def __exit__(self, exc_type, exc, tb):
        self.conn.close()

    def init_schema(self) -> None:
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

        # Migrations for existing databases
        for col_def in [
            ("idec", "VARCHAR"),
            ("last_notified_at", "TIMESTAMPTZ"),
            ("broadcast_at", "TIMESTAMPTZ"),
        ]:
            try:
                self.conn.execute(
                    f"ALTER TABLE episodes ADD COLUMN IF NOT EXISTS {col_def[0]} {col_def[1]}"
                )
            except Exception:
                pass

    # --- TV shows ---
    def add_or_reactivate_show(self, url: str) -> str:
        canonical_url = normalize_show_url(url)
        now = datetime.now(timezone.utc)
        self.conn.execute(
            """
            INSERT INTO tv_shows (url, is_active, added_at)
            VALUES (?, true, ?)
            ON CONFLICT (url) DO UPDATE SET is_active = true
        """,
            [canonical_url, now],
        )
        return canonical_url

    def insert_show(self, url: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Helper to add or update show metadata."""
        canonical_url = normalize_show_url(url)
        self.add_or_reactivate_show(canonical_url)
        if metadata:
            self.update_show_metadata(canonical_url, metadata)
        return canonical_url

    def disable_show(self, url: str) -> bool:
        canonical_url = normalize_show_url(url)
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

    def list_shows(self, active: Optional[bool] = None) -> List[Tuple[str, bool]]:
        if active is None:
            q = "SELECT url, is_active FROM tv_shows ORDER BY url"
            rows = self.conn.execute(q).fetchall()
        else:
            q = "SELECT url, is_active FROM tv_shows WHERE is_active = ? ORDER BY url"
            rows = self.conn.execute(q, [active]).fetchall()
        return [(r[0], bool(r[1])) for r in rows]

    def get_active_urls(self) -> List[str]:
        return [
            r[0]
            for r in self.conn.execute(
                "SELECT url FROM tv_shows WHERE is_active = true"
            ).fetchall()
        ]

    def get_active_shows(self) -> List[dict]:
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

    def update_show_metadata(self, url: str, metadata: Dict[str, Any]) -> None:
        canonical_url = normalize_show_url(url)
        now = datetime.now(timezone.utc)
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
        name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        idec: Optional[str] = None,
        broadcast_at: Optional[datetime] = None,
        cooldown_days: Optional[int] = None,
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
        now = datetime.now(timezone.utc)
        meta_dict = metadata or {}
        meta_json = json.dumps(meta_dict, ensure_ascii=False)
        effective_cooldown = (
            cooldown_days
            if cooldown_days is not None
            else CONFIG.NOTIFICATION_COOLDOWN_DAYS
        )

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

        if existing is None:
            # Brand new episode
            initial_notified = now if backfill else None
            self.conn.execute(
                """
                INSERT INTO episodes (url, show_url, idec, name, metadata, first_discovered_at, last_notified_at, broadcast_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
        old_url, first_discovered, last_notified = existing
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
        if now - last_notified > cooldown_delta:
            # Cooldown has passed (e.g. re-aired / rerun)
            return True

        return False

    def insert_new_episode(
        self,
        show_url: str,
        url: str,
        name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
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

    def mark_episodes_notified(self, urls: List[str]) -> None:
        """
        Marks the provided episode URLs as having been notified at the current timestamp.
        """
        if not urls:
            return
        now = datetime.now(timezone.utc)
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
        self.conn.execute(
            """
            INSERT INTO guild_config (key, value) VALUES (?, ?)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
            [key, value],
        )

    def get_guild_value(self, key: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT value FROM guild_config WHERE key = ?", [key]
        ).fetchone()
        return row[0] if row else None
