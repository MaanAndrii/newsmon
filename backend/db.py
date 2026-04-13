from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent / "newsmon.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;

            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                is_active INTEGER NOT NULL DEFAULT 1,
                ai_enabled INTEGER NOT NULL DEFAULT 1,
                last_message_at TEXT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                color TEXT NOT NULL DEFAULT '#64748b',
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phrase TEXT NOT NULL,
                category_id INTEGER NULL REFERENCES categories(id) ON DELETE SET NULL,
                min_score INTEGER NOT NULL DEFAULT 0 CHECK(min_score BETWEEN 0 AND 10),
                is_regex INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(phrase, category_id)
            );

            CREATE INDEX IF NOT EXISTS idx_keywords_category ON keywords(category_id);
            CREATE INDEX IF NOT EXISTS idx_sources_active ON sources(is_active);
            CREATE INDEX IF NOT EXISTS idx_categories_default ON categories(is_default);

            CREATE TABLE IF NOT EXISTS integrations (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                claude_api_key TEXT NULL,
                claude_model TEXT NULL DEFAULT 'claude-haiku-4-5-20251001',
                telegram_api_id TEXT NULL,
                telegram_api_hash TEXT NULL,
                telegram_bot_token TEXT NULL,
                telegram_bot_chat_id TEXT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                tg_message_id INTEGER NOT NULL,
                published_at TEXT NOT NULL,
                text TEXT NULL,
                media_type TEXT NULL,
                ai_score INTEGER NULL CHECK(ai_score BETWEEN 0 AND 10),
                ai_category TEXT NULL,
                ai_status TEXT NOT NULL DEFAULT 'pending',
                manual_override INTEGER NOT NULL DEFAULT 0,
                workflow_status TEXT NOT NULL DEFAULT 'new',
                telegram_url TEXT NULL,
                raw_json TEXT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(source_id, tg_message_id)
            );

            CREATE INDEX IF NOT EXISTS idx_messages_source_date ON messages(source_id, published_at DESC);
            CREATE INDEX IF NOT EXISTS idx_messages_date ON messages(published_at DESC);
            CREATE INDEX IF NOT EXISTS idx_messages_ai_score ON messages(ai_score);
            CREATE INDEX IF NOT EXISTS idx_messages_workflow ON messages(workflow_status);

            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                text,
                content='messages',
                content_rowid='id'
            );

            CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, text) VALUES (new.id, COALESCE(new.text, ''));
            END;
            CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.id, COALESCE(old.text, ''));
            END;
            CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.id, COALESCE(old.text, ''));
                INSERT INTO messages_fts(rowid, text) VALUES (new.id, COALESCE(new.text, ''));
            END;

            CREATE TABLE IF NOT EXISTS ai_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'pending',
                retries INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                media_type TEXT NOT NULL,
                file_name TEXT NULL,
                mime_type TEXT NULL,
                size_bytes INTEGER NULL,
                local_path TEXT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                pattern TEXT NOT NULL,
                is_enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS alert_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id INTEGER NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
                message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                matched_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
                note TEXT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                login TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'reader',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                last_login TEXT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        _ensure_column(conn, "sources", "ai_enabled", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "sources", "last_message_at", "TEXT NULL")
        _ensure_column(conn, "integrations", "claude_api_key", "TEXT NULL")
        _ensure_column(conn, "integrations", "claude_model", "TEXT NULL DEFAULT 'claude-haiku-4-5-20251001'")
        _ensure_column(conn, "integrations", "telegram_bot_token", "TEXT NULL")
        _ensure_column(conn, "integrations", "telegram_bot_chat_id", "TEXT NULL")


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, ddl: str) -> None:
    existing = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    column_names = {row[1] for row in existing}
    if column_name not in column_names:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")


class Repository:
    def list_messages(self, limit: int = 100) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT m.id, m.source_id, s.name AS source_name, s.url AS source_url,
                       m.tg_message_id, m.published_at, m.text, m.media_type,
                       m.ai_score, m.ai_category, m.ai_status, m.workflow_status,
                       m.telegram_url, m.created_at
                FROM messages m
                JOIN sources s ON s.id = m.source_id
                WHERE m.ai_status = 'done'
                ORDER BY datetime(m.published_at) DESC, m.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_last_tg_message_id(self, source_id: int) -> int:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(tg_message_id), 0) AS max_id FROM messages WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return int(row["max_id"] or 0)

    def upsert_message(
        self,
        source_id: int,
        tg_message_id: int,
        published_at: str,
        text: str | None,
        media_type: str | None,
        telegram_url: str | None,
        raw_json: str | None,
        enqueue_ai: bool = True,
    ) -> int:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO messages(
                    source_id, tg_message_id, published_at, text, media_type, telegram_url, raw_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(source_id, tg_message_id) DO UPDATE SET
                    published_at=excluded.published_at,
                    text=excluded.text,
                    media_type=excluded.media_type,
                    telegram_url=excluded.telegram_url,
                    raw_json=excluded.raw_json,
                    updated_at=datetime('now')
                """,
                (source_id, tg_message_id, published_at, text, media_type, telegram_url, raw_json),
            )
            row = conn.execute(
                "SELECT id FROM messages WHERE source_id = ? AND tg_message_id = ?",
                (source_id, tg_message_id),
            ).fetchone()
            message_id = int(row["id"])
            if enqueue_ai:
                conn.execute(
                    """
                    INSERT INTO ai_queue(message_id, status, updated_at)
                    VALUES (?, 'pending', datetime('now'))
                    ON CONFLICT(message_id) DO NOTHING
                    """,
                    (message_id,),
                )
        return message_id

    def clear_all_messages(self) -> int:
        with get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM messages").fetchone()
            deleted = int(row["cnt"] or 0)
            conn.execute("DELETE FROM ai_queue")
            conn.execute("DELETE FROM messages")
        return deleted

    def list_sources(self, sort_by: str = "created_desc") -> list[dict[str, Any]]:
        order_by = {
            "alpha": "LOWER(name) ASC",
            "last_message_desc": "last_message_at DESC, LOWER(name) ASC",
            "last_message_asc": "last_message_at ASC, LOWER(name) ASC",
            "created_desc": "id DESC",
        }.get(sort_by, "id DESC")
        with get_connection() as conn:
            rows = conn.execute(
                f"SELECT id, name, url, is_active, ai_enabled, last_message_at, created_at FROM sources ORDER BY {order_by}"
            ).fetchall()
        return [dict(r) for r in rows]

    def create_source(self, name: str, url: str) -> dict[str, Any]:
        with get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO sources(name, url) VALUES (?, ?)",
                (name, url),
            )
            row = conn.execute(
                "SELECT id, name, url, is_active, ai_enabled, last_message_at, created_at FROM sources WHERE id = ?",
                (cur.lastrowid,),
            ).fetchone()
        return dict(row)

    def update_source(self, source_id: int, is_active: bool | None, ai_enabled: bool | None) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute("SELECT id FROM sources WHERE id = ?", (source_id,)).fetchone()
            if not row:
                return None
            if is_active is not None:
                conn.execute("UPDATE sources SET is_active = ? WHERE id = ?", (int(is_active), source_id))
            if ai_enabled is not None:
                conn.execute("UPDATE sources SET ai_enabled = ? WHERE id = ?", (int(ai_enabled), source_id))
            updated = conn.execute(
                "SELECT id, name, url, is_active, ai_enabled, last_message_at, created_at FROM sources WHERE id = ?",
                (source_id,),
            ).fetchone()
        return dict(updated)

    def delete_source(self, source_id: int) -> bool:
        with get_connection() as conn:
            cur = conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        return cur.rowcount > 0

    def update_source_last_message(self, source_id: int, last_message_at: str | None) -> None:
        with get_connection() as conn:
            conn.execute(
                "UPDATE sources SET last_message_at = ? WHERE id = ?",
                (last_message_at, source_id),
            )

    def list_categories(self) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, name, color, is_default, created_at FROM categories ORDER BY id DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def create_category(self, name: str, color: str, is_default: bool) -> dict[str, Any]:
        with get_connection() as conn:
            if is_default:
                conn.execute("UPDATE categories SET is_default = 0")
            cur = conn.execute(
                "INSERT INTO categories(name, color, is_default) VALUES (?, ?, ?)",
                (name, color, int(is_default)),
            )
            row = conn.execute(
                "SELECT id, name, color, is_default, created_at FROM categories WHERE id = ?",
                (cur.lastrowid,),
            ).fetchone()
        return dict(row)

    def delete_category(self, category_id: int) -> bool:
        with get_connection() as conn:
            cur = conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        return cur.rowcount > 0

    def list_keywords(self) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT k.id, k.phrase, k.min_score, k.is_regex, k.is_active, k.created_at,
                       c.id AS category_id, c.name AS category_name
                FROM keywords k
                LEFT JOIN categories c ON c.id = k.category_id
                ORDER BY k.id DESC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def create_keyword(
        self,
        phrase: str,
        category_id: int | None,
        min_score: int,
        is_regex: bool,
    ) -> dict[str, Any]:
        with get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO keywords(phrase, category_id, min_score, is_regex) VALUES (?, ?, ?, ?)",
                (phrase, category_id, min_score, int(is_regex)),
            )
            row = conn.execute(
                """
                SELECT k.id, k.phrase, k.min_score, k.is_regex, k.is_active, k.created_at,
                       c.id AS category_id, c.name AS category_name
                FROM keywords k
                LEFT JOIN categories c ON c.id = k.category_id
                WHERE k.id = ?
                """,
                (cur.lastrowid,),
            ).fetchone()
        return dict(row)

    def delete_keyword(self, keyword_id: int) -> bool:
        with get_connection() as conn:
            cur = conn.execute("DELETE FROM keywords WHERE id = ?", (keyword_id,))
        return cur.rowcount > 0

    def get_integrations(self) -> dict[str, Any]:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT claude_api_key, claude_model, telegram_api_id, telegram_api_hash,
                       telegram_bot_token, telegram_bot_chat_id, updated_at
                FROM integrations WHERE id = 1
                """
            ).fetchone()
            if row is None:
                conn.execute("INSERT INTO integrations(id) VALUES (1)")
                row = conn.execute(
                    """
                    SELECT claude_api_key, claude_model, telegram_api_id, telegram_api_hash,
                           telegram_bot_token, telegram_bot_chat_id, updated_at
                    FROM integrations WHERE id = 1
                    """
                ).fetchone()
        return dict(row)

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return default
        return str(row["value"])

    def set_setting(self, key: str, value: str) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO settings(key, value, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=datetime('now')
                """,
                (key, value),
            )

    def save_integrations(self, payload: dict[str, Any]) -> dict[str, Any]:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO integrations(
                    id, claude_api_key, claude_model, telegram_api_id, telegram_api_hash,
                    telegram_bot_token, telegram_bot_chat_id, updated_at
                )
                VALUES (1, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                    claude_api_key=excluded.claude_api_key,
                    claude_model=excluded.claude_model,
                    telegram_api_id=excluded.telegram_api_id,
                    telegram_api_hash=excluded.telegram_api_hash,
                    telegram_bot_token=excluded.telegram_bot_token,
                    telegram_bot_chat_id=excluded.telegram_bot_chat_id,
                    updated_at=datetime('now')
                """,
                (
                    payload.get("claude_api_key"),
                    payload.get("claude_model"),
                    payload.get("telegram_api_id"),
                    payload.get("telegram_api_hash"),
                    payload.get("telegram_bot_token"),
                    payload.get("telegram_bot_chat_id"),
                ),
            )
            row = conn.execute(
                """
                SELECT claude_api_key, claude_model, telegram_api_id, telegram_api_hash,
                       telegram_bot_token, telegram_bot_chat_id, updated_at
                FROM integrations WHERE id = 1
                """
            ).fetchone()
        return dict(row)

    def claim_ai_queue_pending(self, limit: int = 20) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT q.message_id, m.text
                FROM ai_queue q
                JOIN messages m ON m.id = q.message_id
                WHERE q.status = 'pending'
                ORDER BY q.id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            ids = [int(r["message_id"]) for r in rows]
            for message_id in ids:
                conn.execute(
                    "UPDATE ai_queue SET status = 'processing', updated_at = datetime('now') WHERE message_id = ?",
                    (message_id,),
                )
        return [dict(r) for r in rows]

    def mark_ai_result(self, message_id: int, score: int, category: str | None) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE messages
                SET ai_score = ?, ai_category = ?, ai_status = 'done', updated_at = datetime('now')
                WHERE id = ?
                """,
                (score, category, message_id),
            )
            conn.execute(
                "UPDATE ai_queue SET status = 'done', last_error = NULL, updated_at = datetime('now') WHERE message_id = ?",
                (message_id,),
            )

    def mark_ai_error(self, message_id: int, error: str) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE ai_queue
                SET retries = retries + 1,
                    status = 'error',
                    last_error = ?,
                    updated_at = datetime('now')
                WHERE message_id = ?
                """,
                (error[:500], message_id),
            )
            conn.execute(
                "UPDATE messages SET ai_status = 'error', updated_at = datetime('now') WHERE id = ?",
                (message_id,),
            )
