from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_name TEXT NOT NULL DEFAULT '',
    account_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL UNIQUE,
    cover_url TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    content_text TEXT NOT NULL DEFAULT '',
    read_count INTEGER,
    like_count INTEGER,
    source TEXT NOT NULL DEFAULT 'wechat',
    received_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_account ON articles(account_name);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    received_at TEXT NOT NULL,
    article_count INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    last_checked TEXT,
    article_count INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_article(self, article: dict[str, Any]) -> tuple[int, bool]:
        fields = {
            "account_name": article.get("account_name") or "",
            "account_id": article.get("account_id") or "",
            "title": article.get("title") or "",
            "summary": article.get("summary") or "",
            "url": article["url"],
            "cover_url": article.get("cover_url") or "",
            "published_at": article.get("published_at"),
            "content_text": article.get("content_text") or "",
            "read_count": article.get("read_count"),
            "like_count": article.get("like_count"),
            "source": article.get("source") or "wechat",
            "received_at": article["received_at"],
            "updated_at": article["updated_at"],
            "raw_json": json.dumps(article.get("raw") or {}, ensure_ascii=False),
        }
        with self.connect() as conn:
            existing = conn.execute("SELECT id FROM articles WHERE url = ?", (fields["url"],)).fetchone()
            conn.execute(
                """
                INSERT INTO articles (
                    account_name, account_id, title, summary, url, cover_url,
                    published_at, content_text, read_count, like_count, source,
                    received_at, updated_at, raw_json
                ) VALUES (
                    :account_name, :account_id, :title, :summary, :url, :cover_url,
                    :published_at, :content_text, :read_count, :like_count, :source,
                    :received_at, :updated_at, :raw_json
                )
                ON CONFLICT(url) DO UPDATE SET
                    account_name = CASE WHEN excluded.account_name <> '' THEN excluded.account_name ELSE articles.account_name END,
                    account_id = CASE WHEN excluded.account_id <> '' THEN excluded.account_id ELSE articles.account_id END,
                    title = CASE WHEN excluded.title <> '' THEN excluded.title ELSE articles.title END,
                    summary = CASE WHEN excluded.summary <> '' THEN excluded.summary ELSE articles.summary END,
                    cover_url = CASE WHEN excluded.cover_url <> '' THEN excluded.cover_url ELSE articles.cover_url END,
                    published_at = COALESCE(excluded.published_at, articles.published_at),
                    content_text = CASE WHEN excluded.content_text <> '' THEN excluded.content_text ELSE articles.content_text END,
                    read_count = COALESCE(excluded.read_count, articles.read_count),
                    like_count = COALESCE(excluded.like_count, articles.like_count),
                    source = excluded.source,
                    updated_at = excluded.updated_at,
                    raw_json = excluded.raw_json
                """,
                fields,
            )
            row = conn.execute("SELECT id FROM articles WHERE url = ?", (fields["url"],)).fetchone()
            return int(row["id"]), existing is None

    def add_event(self, event_type: str, received_at: str, article_count: int, payload: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO events(event_type, received_at, article_count, payload_json) VALUES (?, ?, ?, ?)",
                (event_type, received_at, article_count, json.dumps(payload, ensure_ascii=False)),
            )

    def list_articles(self, limit: int = 50, offset: int = 0, query: str = "") -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        params: list[Any] = []
        where = ""
        if query.strip():
            where = "WHERE account_name LIKE ? OR title LIKE ? OR summary LIKE ? OR content_text LIKE ?"
            value = f"%{query.strip()}%"
            params.extend([value, value, value, value])
        params.extend([limit, offset])
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT id, account_name, account_id, title, summary, url, cover_url,
                           published_at, content_text, read_count, like_count, source,
                           received_at, updated_at
                    FROM articles {where}
                    ORDER BY COALESCE(published_at, received_at) DESC, id DESC
                    LIMIT ? OFFSET ?""",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def counts(self) -> dict[str, int]:
        with self.connect() as conn:
            article_count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            account_count = conn.execute(
                "SELECT COUNT(DISTINCT account_name) FROM articles WHERE account_name <> ''"
            ).fetchone()[0]
            event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        return {"articles": article_count, "accounts": account_count, "events": event_count}

    def export_articles(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT id, account_name, account_id, title, summary, url, cover_url,
                          published_at, content_text, read_count, like_count, source,
                          received_at, updated_at
                   FROM articles
                   ORDER BY COALESCE(published_at, received_at) DESC, id DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    # ── Feed management ──────────────────────────────────────────────

    def add_feed(self, url: str, name: str = "", created_at: str = "") -> tuple[int, bool]:
        from .parser import now_iso
        created_at = created_at or now_iso()
        with self.connect() as conn:
            existing = conn.execute("SELECT id FROM feeds WHERE url = ?", (url,)).fetchone()
            if existing:
                return int(existing["id"]), False
            conn.execute(
                "INSERT INTO feeds(url, name, active, created_at) VALUES (?, ?, 1, ?)",
                (url, name, created_at),
            )
            row = conn.execute("SELECT id FROM feeds WHERE url = ?", (url,)).fetchone()
            return int(row["id"]), True

    def list_feeds(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, url, name, last_checked, article_count, active, created_at FROM feeds ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_active_feeds(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, url, name, last_checked FROM feeds WHERE active = 1 ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def remove_feed(self, feed_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
            return cursor.rowcount > 0

    def update_feed_check(self, feed_id: int, checked_at: str, new_articles: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE feeds SET last_checked = ?, article_count = article_count + ? WHERE id = ?",
                (checked_at, new_articles, feed_id),
            )

    def toggle_feed(self, feed_id: int, active: bool) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE feeds SET active = ? WHERE id = ?", (1 if active else 0, feed_id)
            )
            return cursor.rowcount > 0

