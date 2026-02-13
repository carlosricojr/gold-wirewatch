from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .models import FeedItem, ScoreResult


class Storage:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_items (
                  item_key TEXT PRIMARY KEY,
                  source TEXT NOT NULL,
                  title TEXT NOT NULL,
                  url TEXT NOT NULL,
                  published_at TEXT,
                  fetched_at TEXT NOT NULL,
                  relevance REAL NOT NULL,
                  severity REAL NOT NULL,
                  reasons TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  kind TEXT NOT NULL,
                  payload TEXT NOT NULL,
                  created_at TEXT NOT NULL
                )
                """
            )

    def is_seen(self, item_key: str) -> bool:
        query = "SELECT 1 FROM seen_items WHERE item_key = ?"
        with self._conn() as conn:
            row = conn.execute(query, (item_key,)).fetchone()
        return row is not None

    def save_item(self, item_key: str, item: FeedItem, score: ScoreResult) -> None:
        sql = """
            INSERT OR IGNORE INTO seen_items
            (
              item_key, source, title, url, published_at,
              fetched_at, relevance, severity, reasons
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._conn() as conn:
            conn.execute(
                sql,
                (
                    item_key,
                    item.source,
                    item.title,
                    item.url,
                    item.published_at.isoformat() if item.published_at else None,
                    item.fetched_at.isoformat(),
                    score.relevance_score,
                    score.severity_score,
                    " | ".join(score.reasons),
                ),
            )

    def save_event(self, kind: str, payload: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO events (kind, payload, created_at) VALUES (?, ?, ?)",
                (kind, payload, datetime.now(UTC).isoformat()),
            )

    def has_recent_event(self, kind: str, within_seconds: int) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT created_at FROM events WHERE kind = ? ORDER BY id DESC LIMIT 1",
                (kind,),
            ).fetchone()
        if row is None:
            return False
        created_at = datetime.fromisoformat(str(row[0]))
        age = datetime.now(UTC) - created_at.astimezone(UTC)
        return age.total_seconds() <= within_seconds

    def latest_items(self, minutes: int = 120) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return list(
                conn.execute(
                    """
                    SELECT * FROM seen_items
                    WHERE fetched_at >= datetime('now', ?)
                    ORDER BY fetched_at DESC
                    """,
                    (f"-{minutes} minutes",),
                ).fetchall()
            )
