from __future__ import annotations
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timezone
import json
import duckdb

class DuckRepo:
    """
    Encapsulates DuckDB access and schema.
    Stores/loads JSON from Pydantic-ready dicts.
    """
    def __init__(self, db_path: str):
        self.conn = duckdb.connect(db_path)
        self.init_schema()

    def __enter__(self) -> "DuckRepo":
        return self

    def __exit__(self, exc_type, exc, tb):
        self.conn.close()

    def init_schema(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS tv_shows (
              url VARCHAR PRIMARY KEY,
              is_active BOOLEAN DEFAULT true,
              metadata JSON,
              added_at TIMESTAMPTZ,
              last_seen_at TIMESTAMPTZ
            );
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
              url VARCHAR PRIMARY KEY,
              show_url VARCHAR,
              name VARCHAR,
              metadata JSON,
              first_discovered_at TIMESTAMPTZ
            );
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS guild_config (
              key VARCHAR PRIMARY KEY,
              value VARCHAR
            );
        """)

    # --- TV shows ---
    def add_or_reactivate_show(self, url: str) -> None:
        now_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
        self.conn.execute("""
            INSERT INTO tv_shows (url, is_active, added_at)
            VALUES (?, true, ?)
            ON CONFLICT (url) DO UPDATE SET is_active = true
        """, [url, now_iso])

    def disable_show(self, url: str) -> bool:
        row = self.conn.execute(
            "UPDATE tv_shows SET is_active = false WHERE url = ? RETURNING url", [url]
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
        return [r[0] for r in self.conn.execute(
            "SELECT url FROM tv_shows WHERE is_active = true"
        ).fetchall()]

    def update_show_metadata(self, url: str, metadata: Dict[str, Any]) -> None:
        now_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
        self.conn.execute("""
            UPDATE tv_shows SET metadata = ?, last_seen_at = ? WHERE url = ?
        """, [json.dumps(metadata, ensure_ascii=False), now_iso, url])

    # --- Episodes ---
    def insert_new_episode(self, show_url: str, name: Optional[str], metadata: Dict[str, Any]) -> bool:
        ep_url = metadata.get("url")
        if not ep_url:
            return False
        now_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
        self.conn.execute("""
            INSERT INTO episodes (url, show_url, name, metadata, first_discovered_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (url) DO NOTHING
            RETURNING url
        """, [ep_url, show_url, name, json.dumps(metadata, ensure_ascii=False), now_iso])
        return bool(self.conn.fetchone())

    # --- Guild config ---
    def set_guild_value(self, key: str, value: str) -> None:
        self.conn.execute("""
            INSERT INTO guild_config (key, value) VALUES (?, ?)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, [key, value])

    def get_guild_value(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM guild_config WHERE key = ?", [key]).fetchone()
        return row[0] if row else None