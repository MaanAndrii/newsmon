from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
    _KYIV_TZ: Any = ZoneInfo("Europe/Kyiv")
except Exception:
    _KYIV_TZ = timezone.utc

DB_PATH = Path(__file__).resolve().parent / "newsmon.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
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
                telegram_unknown_forward_enabled INTEGER NOT NULL DEFAULT 0,
                telegram_unknown_forward_primary TEXT NULL,
                telegram_unknown_forward_reserve TEXT NULL,
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
            CREATE INDEX IF NOT EXISTS idx_messages_ai_status_date ON messages(ai_status, published_at DESC);

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

            CREATE INDEX IF NOT EXISTS idx_ai_queue_status ON ai_queue(status);

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                pattern TEXT NOT NULL,
                alert_type TEXT NOT NULL DEFAULT 'new_message',
                source_id INTEGER NULL REFERENCES sources(id) ON DELETE SET NULL,
                min_score INTEGER NULL CHECK(min_score BETWEEN 0 AND 10),
                target_chat_id TEXT NOT NULL DEFAULT '',
                is_ai_keyword INTEGER NOT NULL DEFAULT 0,
                is_enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS dashboard_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_key TEXT NOT NULL UNIQUE,
                ip TEXT NOT NULL,
                first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
                last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
                active_seconds INTEGER NOT NULL DEFAULT 0,
                heartbeat_count INTEGER NOT NULL DEFAULT 0,
                user_agent TEXT NULL,
                language TEXT NULL,
                timezone TEXT NULL,
                screen TEXT NULL,
                last_path TEXT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_dashboard_sessions_last_seen ON dashboard_sessions(last_seen_at DESC);

            CREATE TABLE IF NOT EXISTS alert_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id INTEGER NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
                message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                matched_keyword TEXT NULL,
                delivered_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(alert_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS debug_api_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                call_type TEXT NOT NULL,
                called_at TEXT NOT NULL DEFAULT (datetime('now')),
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_debug_api_calls_at ON debug_api_calls(called_at DESC);

            CREATE TABLE IF NOT EXISTS debug_event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                logged_at TEXT NOT NULL DEFAULT (datetime('now')),
                event_type TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS debug_run_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ran_at TEXT NOT NULL,
                updated INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                ingested INTEGER NOT NULL DEFAULT 0,
                ai_processed INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'ok',
                error TEXT NULL
            );

            CREATE TABLE IF NOT EXISTS digests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                digest_date TEXT NOT NULL UNIQUE,
                content TEXT NOT NULL DEFAULT '',
                message_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ok',
                generated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_digests_date ON digests(digest_date DESC);
            """
        )
        _ensure_column(conn, "sources", "ai_enabled", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "sources", "last_message_at", "TEXT NULL")
        _ensure_column(conn, "sources", "tg_peer_id", "INTEGER NULL")
        _ensure_column(conn, "sources", "tg_access_hash", "INTEGER NULL")
        _ensure_column(conn, "integrations", "claude_api_key", "TEXT NULL")
        _ensure_column(conn, "integrations", "claude_model", "TEXT NULL DEFAULT 'claude-haiku-4-5-20251001'")
        _ensure_column(conn, "integrations", "telegram_bot_token", "TEXT NULL")
        _ensure_column(conn, "integrations", "telegram_bot_chat_id", "TEXT NULL")
        _ensure_column(conn, "integrations", "telegram_unknown_forward_enabled", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "integrations", "telegram_unknown_forward_primary", "TEXT NULL")
        _ensure_column(conn, "integrations", "telegram_unknown_forward_reserve", "TEXT NULL")
        _ensure_column(conn, "alerts", "alert_type", "TEXT NOT NULL DEFAULT 'new_message'")
        _ensure_column(conn, "alerts", "source_id", "INTEGER NULL REFERENCES sources(id) ON DELETE SET NULL")
        _ensure_column(conn, "alerts", "min_score", "INTEGER NULL CHECK(min_score BETWEEN 0 AND 10)")
        _ensure_column(conn, "alerts", "target_chat_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "alerts", "is_ai_keyword", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "messages", "content_hash", "TEXT NULL")
        _ensure_column(conn, "messages", "is_dedup", "INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_content_hash ON messages(content_hash)"
        )
        _ensure_column(conn, "digests", "model_name", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "digests", "tokens_in", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "digests", "tokens_out", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "integrations", "grok_api_key", "TEXT NULL")
        _ensure_column(conn, "integrations", "grok_model", "TEXT NULL")
        _ensure_column(conn, "integrations", "gemini_api_key", "TEXT NULL")
        _ensure_column(conn, "integrations", "gemini_model", "TEXT NULL")
        _ensure_column(conn, "integrations", "claude_model_2", "TEXT NULL")
        _ensure_column(conn, "integrations", "claude_model_3", "TEXT NULL")
        _ensure_column(conn, "integrations", "grok_model_2", "TEXT NULL")
        _ensure_column(conn, "integrations", "grok_model_3", "TEXT NULL")
        _ensure_column(conn, "integrations", "gemini_model_2", "TEXT NULL")
        _ensure_column(conn, "integrations", "gemini_model_3", "TEXT NULL")


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, ddl: str) -> None:
    existing = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    column_names = {row[1] for row in existing}
    if column_name not in column_names:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")


class Repository:
    def list_messages(
        self,
        limit: int = 100,
        search_query: str | None = None,
        category: str | None = None,
        source_id: int | None = None,
        keyword: str | None = None,
        min_score: int | None = None,
        max_score: int | None = None,
        content_hash: str | None = None,
        include_dedup: bool = False,
        published_date: str | None = None,
    ) -> list[dict[str, Any]]:
        where_parts = [
            "m.ai_status = 'done'",
            "m.text IS NOT NULL",
            "m.text != ''",
        ]
        if not include_dedup:
            where_parts.append(
                "(m.content_hash IS NULL OR m.content_hash = '' OR "
                "NOT EXISTS (SELECT 1 FROM messages md "
                "WHERE md.content_hash = m.content_hash "
                "AND md.id < m.id "
                "AND md.ai_status = 'done'))"
            )
        params: list[Any] = []
        search_raw = (search_query or "").strip()
        category_raw = (category or "").strip()
        keyword_raw = (keyword or "").strip()
        content_hash_raw = (content_hash or "").strip()
        if search_raw:
            where_parts.append(
                "m.id IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?)"
            )
            params.append(search_raw)
        if category_raw:
            where_parts.append("m.ai_category = ?")
            params.append(category_raw)
        if source_id is not None and int(source_id) > 0:
            where_parts.append("m.source_id = ?")
            params.append(int(source_id))
        if keyword_raw:
            where_parts.append("LOWER(COALESCE(m.text, '')) LIKE LOWER(?)")
            params.append(f"%{keyword_raw}%")
        if min_score is not None:
            where_parts.append("m.ai_score >= ?")
            params.append(int(min_score))
        if max_score is not None:
            where_parts.append("m.ai_score <= ?")
            params.append(int(max_score))
        if content_hash_raw:
            where_parts.append("m.content_hash = ?")
            params.append(content_hash_raw)
        published_date_raw = (published_date or "").strip()
        if published_date_raw:
            where_parts.append("DATE(datetime(m.published_at, '+3 hours')) = ?")
            params.append(published_date_raw)
        params.append(limit)
        query = f"""
            SELECT m.id, m.source_id, s.name AS source_name, s.url AS source_url,
                   m.tg_message_id, m.published_at, m.text, m.media_type,
                   m.ai_score, m.ai_category, m.ai_status, m.workflow_status,
                   m.telegram_url, m.created_at, m.is_dedup, m.content_hash,
                   (SELECT COUNT(*) FROM messages md
                    WHERE md.content_hash = m.content_hash AND md.is_dedup = 1
                      AND m.content_hash IS NOT NULL AND m.content_hash != '') AS dedup_count
            FROM messages m
            JOIN sources s ON s.id = m.source_id
            WHERE {" AND ".join(where_parts)}
            ORDER BY m.published_at DESC, m.id DESC
            LIMIT ?
        """
        with get_connection() as conn:
            try:
                rows = conn.execute(query, params).fetchall()
            except sqlite3.OperationalError:
                if search_raw:
                    fallback_params: list[Any] = []
                    fallback_where = [
                        "m.ai_status = 'done'",
                        "m.text IS NOT NULL",
                        "m.text != ''",
                    ]
                    if not include_dedup:
                        fallback_where.append(
                            "(m.content_hash IS NULL OR m.content_hash = '' OR "
                            "NOT EXISTS (SELECT 1 FROM messages md "
                            "WHERE md.content_hash = m.content_hash "
                            "AND md.id < m.id "
                            "AND md.ai_status = 'done'))"
                        )
                    fallback_where.append("COALESCE(m.text, '') LIKE ?")
                    fallback_params.append(f"%{search_raw}%")
                    if category_raw:
                        fallback_where.append("m.ai_category = ?")
                        fallback_params.append(category_raw)
                    if source_id is not None and int(source_id) > 0:
                        fallback_where.append("m.source_id = ?")
                        fallback_params.append(int(source_id))
                    if keyword_raw:
                        fallback_where.append("LOWER(COALESCE(m.text, '')) LIKE LOWER(?)")
                        fallback_params.append(f"%{keyword_raw}%")
                    if min_score is not None:
                        fallback_where.append("m.ai_score >= ?")
                        fallback_params.append(int(min_score))
                    if max_score is not None:
                        fallback_where.append("m.ai_score <= ?")
                        fallback_params.append(int(max_score))
                    if content_hash_raw:
                        fallback_where.append("m.content_hash = ?")
                        fallback_params.append(content_hash_raw)
                    if published_date_raw:
                        fallback_where.append("DATE(datetime(m.published_at, '+3 hours')) = ?")
                        fallback_params.append(published_date_raw)
                    fallback_params.append(limit)
                    fallback_query = f"""
                        SELECT m.id, m.source_id, s.name AS source_name, s.url AS source_url,
                               m.tg_message_id, m.published_at, m.text, m.media_type,
                               m.ai_score, m.ai_category, m.ai_status, m.workflow_status,
                               m.telegram_url, m.created_at, m.is_dedup, m.content_hash,
                               (SELECT COUNT(*) FROM messages md
                                WHERE md.content_hash = m.content_hash AND md.is_dedup = 1
                                  AND m.content_hash IS NOT NULL AND m.content_hash != '') AS dedup_count
                        FROM messages m
                        JOIN sources s ON s.id = m.source_id
                        WHERE {" AND ".join(fallback_where)}
                        ORDER BY m.published_at DESC, m.id DESC
                        LIMIT ?
                    """
                    rows = conn.execute(fallback_query, fallback_params).fetchall()
                else:
                    raise
        return [dict(r) for r in rows]

    def get_last_tg_message_id(self, source_id: int) -> int:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(tg_message_id), 0) AS max_id FROM messages WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return int(row["max_id"] or 0)

    def count_messages(self) -> int:
        with get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM messages").fetchone()
        return int(row["cnt"] or 0)

    def enforce_max_messages(self, max_messages: int) -> int:
        safe_limit = max(500, min(10000, int(max_messages)))
        with get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM messages").fetchone()
            total = int(row["cnt"] or 0)
            excess = max(0, total - safe_limit)
            if excess <= 0:
                return 0
            old_rows = conn.execute(
                """
                SELECT id
                FROM messages
                ORDER BY published_at ASC, id ASC
                LIMIT ?
                """,
                (excess,),
            ).fetchall()
            ids = [int(r["id"]) for r in old_rows]
            if not ids:
                return 0
            placeholders = ",".join("?" for _ in ids)
            conn.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", ids)
        return excess

    def enforce_retention_months(self, retention_months: int) -> int:
        safe_months = max(1, min(6, int(retention_months)))
        with get_connection() as conn:
            old_rows = conn.execute(
                """
                SELECT id
                FROM messages
                WHERE datetime(published_at) < datetime('now', ?)
                """,
                (f"-{safe_months} months",),
            ).fetchall()
            ids = [int(r["id"]) for r in old_rows]
            if not ids:
                return 0
            placeholders = ",".join("?" for _ in ids)
            conn.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", ids)
        return len(ids)

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
        content_hash: str | None = None,
    ) -> int:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO messages(
                    source_id, tg_message_id, published_at, text, media_type, telegram_url, raw_json,
                    content_hash, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(source_id, tg_message_id) DO UPDATE SET
                    published_at=excluded.published_at,
                    text=excluded.text,
                    media_type=excluded.media_type,
                    telegram_url=excluded.telegram_url,
                    raw_json=excluded.raw_json,
                    content_hash=COALESCE(excluded.content_hash, messages.content_hash),
                    updated_at=datetime('now')
                """,
                (source_id, tg_message_id, published_at, text, media_type, telegram_url, raw_json, content_hash),
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

    def delete_empty_messages(self) -> int:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM messages WHERE text IS NULL OR TRIM(text) = ''"
            ).fetchone()
            deleted = int(row["cnt"] or 0)
            if deleted:
                conn.execute(
                    "DELETE FROM messages WHERE text IS NULL OR TRIM(text) = ''"
                )
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
                f"SELECT id, name, url, is_active, ai_enabled, last_message_at, tg_peer_id, tg_access_hash, created_at FROM sources ORDER BY {order_by}"
            ).fetchall()
        return [dict(r) for r in rows]

    def create_source(self, name: str, url: str) -> dict[str, Any]:
        with get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO sources(name, url) VALUES (?, ?)",
                (name, url),
            )
            row = conn.execute(
                "SELECT id, name, url, is_active, ai_enabled, last_message_at, tg_peer_id, tg_access_hash, created_at FROM sources WHERE id = ?",
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
                "SELECT id, name, url, is_active, ai_enabled, last_message_at, tg_peer_id, tg_access_hash, created_at FROM sources WHERE id = ?",
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

    def update_source_tg_peer(self, source_id: int, peer_id: int, access_hash: int) -> None:
        with get_connection() as conn:
            conn.execute(
                "UPDATE sources SET tg_peer_id = ?, tg_access_hash = ? WHERE id = ?",
                (int(peer_id), int(access_hash), source_id),
            )

    def list_categories(self) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT c.id, c.name, c.color, c.is_default, c.created_at,
                       COUNT(m.id) AS message_count
                FROM categories c
                LEFT JOIN messages m ON m.ai_category = c.name AND m.ai_status = 'done'
                GROUP BY c.id
                ORDER BY c.id DESC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def update_category(
        self,
        category_id: int,
        name: str | None,
        color: str | None,
        is_default: bool | None,
    ) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute("SELECT id FROM categories WHERE id = ?", (category_id,)).fetchone()
            if not row:
                return None
            if is_default:
                conn.execute("UPDATE categories SET is_default = 0 WHERE id != ?", (category_id,))
            if name is not None:
                conn.execute("UPDATE categories SET name = ? WHERE id = ?", (name, category_id))
            if color is not None:
                conn.execute("UPDATE categories SET color = ? WHERE id = ?", (color, category_id))
            if is_default is not None:
                conn.execute("UPDATE categories SET is_default = ? WHERE id = ?", (int(is_default), category_id))
            updated = conn.execute(
                """
                SELECT c.id, c.name, c.color, c.is_default, c.created_at,
                       COUNT(m.id) AS message_count
                FROM categories c
                LEFT JOIN messages m ON m.ai_category = c.name AND m.ai_status = 'done'
                WHERE c.id = ?
                GROUP BY c.id
                """,
                (category_id,),
            ).fetchone()
        return dict(updated) if updated else None

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
                SELECT claude_api_key, claude_model, claude_model_2, claude_model_3,
                       grok_api_key, grok_model, grok_model_2, grok_model_3,
                       gemini_api_key, gemini_model, gemini_model_2, gemini_model_3,
                       telegram_api_id, telegram_api_hash,
                       telegram_bot_token, telegram_bot_chat_id,
                       telegram_unknown_forward_enabled,
                       telegram_unknown_forward_primary,
                       telegram_unknown_forward_reserve,
                       updated_at
                FROM integrations WHERE id = 1
                """
            ).fetchone()
            if row is None:
                conn.execute("INSERT INTO integrations(id) VALUES (1)")
                row = conn.execute(
                    """
                    SELECT claude_api_key, claude_model, claude_model_2, claude_model_3,
                           grok_api_key, grok_model, grok_model_2, grok_model_3,
                           gemini_api_key, gemini_model, gemini_model_2, gemini_model_3,
                           telegram_api_id, telegram_api_hash,
                           telegram_bot_token, telegram_bot_chat_id,
                           telegram_unknown_forward_enabled,
                           telegram_unknown_forward_primary,
                           telegram_unknown_forward_reserve,
                           updated_at
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
                    id, claude_api_key, claude_model, claude_model_2, claude_model_3,
                    grok_api_key, grok_model, grok_model_2, grok_model_3,
                    gemini_api_key, gemini_model, gemini_model_2, gemini_model_3,
                    telegram_api_id, telegram_api_hash,
                    telegram_bot_token, telegram_bot_chat_id,
                    telegram_unknown_forward_enabled,
                    telegram_unknown_forward_primary,
                    telegram_unknown_forward_reserve,
                    updated_at
                )
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                    claude_api_key=excluded.claude_api_key,
                    claude_model=excluded.claude_model,
                    claude_model_2=excluded.claude_model_2,
                    claude_model_3=excluded.claude_model_3,
                    grok_api_key=excluded.grok_api_key,
                    grok_model=excluded.grok_model,
                    grok_model_2=excluded.grok_model_2,
                    grok_model_3=excluded.grok_model_3,
                    gemini_api_key=excluded.gemini_api_key,
                    gemini_model=excluded.gemini_model,
                    gemini_model_2=excluded.gemini_model_2,
                    gemini_model_3=excluded.gemini_model_3,
                    telegram_api_id=excluded.telegram_api_id,
                    telegram_api_hash=excluded.telegram_api_hash,
                    telegram_bot_token=excluded.telegram_bot_token,
                    telegram_bot_chat_id=excluded.telegram_bot_chat_id,
                    telegram_unknown_forward_enabled=excluded.telegram_unknown_forward_enabled,
                    telegram_unknown_forward_primary=excluded.telegram_unknown_forward_primary,
                    telegram_unknown_forward_reserve=excluded.telegram_unknown_forward_reserve,
                    updated_at=datetime('now')
                """,
                (
                    payload.get("claude_api_key"),
                    payload.get("claude_model"),
                    payload.get("claude_model_2") or None,
                    payload.get("claude_model_3") or None,
                    payload.get("grok_api_key"),
                    payload.get("grok_model"),
                    payload.get("grok_model_2") or None,
                    payload.get("grok_model_3") or None,
                    payload.get("gemini_api_key"),
                    payload.get("gemini_model"),
                    payload.get("gemini_model_2") or None,
                    payload.get("gemini_model_3") or None,
                    payload.get("telegram_api_id"),
                    payload.get("telegram_api_hash"),
                    payload.get("telegram_bot_token"),
                    payload.get("telegram_bot_chat_id"),
                    int(bool(payload.get("telegram_unknown_forward_enabled"))),
                    payload.get("telegram_unknown_forward_primary"),
                    payload.get("telegram_unknown_forward_reserve"),
                ),
            )
            row = conn.execute(
                """
                SELECT claude_api_key, claude_model, claude_model_2, claude_model_3,
                       grok_api_key, grok_model, grok_model_2, grok_model_3,
                       gemini_api_key, gemini_model, gemini_model_2, gemini_model_3,
                       telegram_api_id, telegram_api_hash,
                       telegram_bot_token, telegram_bot_chat_id,
                       telegram_unknown_forward_enabled,
                       telegram_unknown_forward_primary,
                       telegram_unknown_forward_reserve,
                       updated_at
                FROM integrations WHERE id = 1
                """
            ).fetchone()
        return dict(row)

    def record_dashboard_session(
        self,
        *,
        session_key: str,
        ip: str,
        active_seconds: int,
        user_agent: str | None,
        language: str | None,
        timezone: str | None,
        screen: str | None,
        path: str | None,
    ) -> None:
        safe_session_key = session_key.strip()[:120]
        safe_ip = ip.strip()[:80] or "unknown"
        safe_active_seconds = max(0, min(int(active_seconds), 86_400))
        safe_user_agent = (user_agent or "").strip()[:300]
        safe_language = (language or "").strip()[:32]
        safe_timezone = (timezone or "").strip()[:64]
        safe_screen = (screen or "").strip()[:32]
        safe_path = (path or "").strip()[:120]
        if not safe_session_key:
            return

        with get_connection() as conn:
            conn.execute(
                "DELETE FROM dashboard_sessions WHERE last_seen_at < datetime('now', '-7 days')"
            )
            conn.execute(
                """
                INSERT INTO dashboard_sessions(
                    session_key, ip, first_seen_at, last_seen_at, active_seconds,
                    heartbeat_count, user_agent, language, timezone, screen, last_path
                )
                VALUES (?, ?, datetime('now'), datetime('now'), ?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    ip = excluded.ip,
                    last_seen_at = datetime('now'),
                    active_seconds = MAX(dashboard_sessions.active_seconds, excluded.active_seconds),
                    heartbeat_count = dashboard_sessions.heartbeat_count + 1,
                    user_agent = CASE
                        WHEN LENGTH(TRIM(excluded.user_agent)) > 0 THEN excluded.user_agent
                        ELSE dashboard_sessions.user_agent
                    END,
                    language = CASE
                        WHEN LENGTH(TRIM(excluded.language)) > 0 THEN excluded.language
                        ELSE dashboard_sessions.language
                    END,
                    timezone = CASE
                        WHEN LENGTH(TRIM(excluded.timezone)) > 0 THEN excluded.timezone
                        ELSE dashboard_sessions.timezone
                    END,
                    screen = CASE
                        WHEN LENGTH(TRIM(excluded.screen)) > 0 THEN excluded.screen
                        ELSE dashboard_sessions.screen
                    END,
                    last_path = CASE
                        WHEN LENGTH(TRIM(excluded.last_path)) > 0 THEN excluded.last_path
                        ELSE dashboard_sessions.last_path
                    END
                """,
                (
                    safe_session_key,
                    safe_ip,
                    safe_active_seconds,
                    safe_user_agent,
                    safe_language,
                    safe_timezone,
                    safe_screen,
                    safe_path,
                ),
            )


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

    def mark_message_no_ai(self, message_id: int, category: str | None) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE messages
                SET ai_score = NULL, ai_category = ?, ai_status = 'done', updated_at = datetime('now')
                WHERE id = ?
                """,
                (category, message_id),
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

    def list_alerts(self) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT a.id, a.name, a.pattern, a.alert_type, a.source_id,
                       s.name AS source_name, a.min_score, a.target_chat_id,
                       a.is_ai_keyword, a.is_enabled, a.created_at
                FROM alerts a
                LEFT JOIN sources s ON s.id = a.source_id
                ORDER BY a.id DESC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def create_alert(
        self,
        name: str,
        pattern: str,
        alert_type: str,
        source_id: int | None,
        min_score: int | None,
        target_chat_id: str,
        is_ai_keyword: bool,
        is_enabled: bool,
    ) -> dict[str, Any]:
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO alerts(name, pattern, alert_type, source_id, min_score, target_chat_id, is_ai_keyword, is_enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (name, pattern, alert_type, source_id, min_score, target_chat_id, int(is_ai_keyword), int(is_enabled)),
            )
            row = conn.execute(
                """
                SELECT a.id, a.name, a.pattern, a.alert_type, a.source_id,
                       s.name AS source_name, a.min_score, a.target_chat_id,
                       a.is_ai_keyword, a.is_enabled, a.created_at
                FROM alerts a
                LEFT JOIN sources s ON s.id = a.source_id
                WHERE a.id = ?
                """,
                (cur.lastrowid,),
            ).fetchone()
        return dict(row)

    def update_alert(
        self,
        alert_id: int,
        name: str | None = None,
        pattern: str | None = None,
        alert_type: str | None = None,
        source_id: int | None = None,
        min_score: int | None = None,
        target_chat_id: str | None = None,
        is_ai_keyword: bool | None = None,
        is_enabled: bool | None = None,
    ) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute("SELECT id FROM alerts WHERE id = ?", (alert_id,)).fetchone()
            if not row:
                return None
            if name is not None:
                conn.execute("UPDATE alerts SET name = ? WHERE id = ?", (name, alert_id))
            if pattern is not None:
                conn.execute("UPDATE alerts SET pattern = ? WHERE id = ?", (pattern, alert_id))
            if alert_type is not None:
                conn.execute("UPDATE alerts SET alert_type = ? WHERE id = ?", (alert_type, alert_id))
            if source_id is not None:
                conn.execute("UPDATE alerts SET source_id = ? WHERE id = ?", (source_id, alert_id))
            if min_score is not None:
                conn.execute("UPDATE alerts SET min_score = ? WHERE id = ?", (min_score, alert_id))
            if target_chat_id is not None:
                conn.execute("UPDATE alerts SET target_chat_id = ? WHERE id = ?", (target_chat_id, alert_id))
            if is_ai_keyword is not None:
                conn.execute("UPDATE alerts SET is_ai_keyword = ? WHERE id = ?", (int(is_ai_keyword), alert_id))
            if is_enabled is not None:
                conn.execute("UPDATE alerts SET is_enabled = ? WHERE id = ?", (int(is_enabled), alert_id))
            updated = conn.execute(
                """
                SELECT a.id, a.name, a.pattern, a.alert_type, a.source_id,
                       s.name AS source_name, a.min_score, a.target_chat_id,
                       a.is_ai_keyword, a.is_enabled, a.created_at
                FROM alerts a
                LEFT JOIN sources s ON s.id = a.source_id
                WHERE a.id = ?
                """,
                (alert_id,),
            ).fetchone()
        return dict(updated) if updated else None

    def delete_alert(self, alert_id: int) -> bool:
        with get_connection() as conn:
            cur = conn.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
        return cur.rowcount > 0

    def list_alert_keywords(self) -> list[str]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT TRIM(pattern) AS keyword
                FROM alerts
                WHERE alert_type = 'keyword_ai'
                  AND is_enabled = 1
                  AND TRIM(pattern) <> ''
                ORDER BY LOWER(TRIM(pattern)) ASC
                """
            ).fetchall()
        return [str(r["keyword"]) for r in rows if r["keyword"]]

    def get_message_by_id(self, message_id: int) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT m.id, m.source_id, s.name AS source_name, m.tg_message_id, m.published_at,
                       m.text, m.ai_score, m.ai_category, m.telegram_url
                FROM messages m
                JOIN sources s ON s.id = m.source_id
                WHERE m.id = ?
                """,
                (message_id,),
            ).fetchone()
        return dict(row) if row else None

    def is_alert_delivered(self, alert_id: int, message_id: int) -> bool:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT 1 AS ok FROM alert_deliveries WHERE alert_id = ? AND message_id = ?",
                (alert_id, message_id),
            ).fetchone()
        return bool(row)

    def mark_alert_delivered(self, alert_id: int, message_id: int, matched_keyword: str | None = None) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO alert_deliveries(alert_id, message_id, matched_keyword, delivered_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(alert_id, message_id) DO NOTHING
                """,
                (alert_id, message_id, matched_keyword),
            )

    # ------------------------------------------------------------------
    # AI queue helpers
    # ------------------------------------------------------------------

    def flush_ai_queue_no_ai(self, default_category: str | None) -> int:
        """Mark all pending/error queue items as done without AI scoring.

        Called when AI is globally disabled or the API key is missing so
        messages become visible on the dashboard immediately.
        """
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT message_id FROM ai_queue WHERE status IN ('pending', 'error')"
            ).fetchall()
            ids = [int(r["message_id"]) for r in rows]
            if not ids:
                return 0
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"""
                UPDATE messages
                SET ai_score = NULL, ai_category = ?, ai_status = 'done',
                    updated_at = datetime('now')
                WHERE id IN ({placeholders})
                """,
                [default_category, *ids],
            )
            conn.execute(
                f"""
                UPDATE ai_queue
                SET status = 'done', updated_at = datetime('now')
                WHERE message_id IN ({placeholders})
                """,
                ids,
            )
        return len(ids)

    def get_ai_queue_stats(self) -> dict[str, int]:
        """Return counts per status for the AI queue."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM ai_queue GROUP BY status"
            ).fetchall()
        result: dict[str, int] = {"pending": 0, "processing": 0, "done": 0, "error": 0}
        for r in rows:
            result[str(r["status"])] = int(r["cnt"])
        return result

    def reset_stale_ai_processing(self, minutes: int = 5) -> int:
        """Reset 'processing' items stuck longer than *minutes* back to 'pending'."""
        with get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE ai_queue
                SET status = 'pending', updated_at = datetime('now')
                WHERE status = 'processing'
                  AND updated_at < datetime('now', ? || ' minutes')
                """,
                (f"-{minutes}",),
            )
            if cur.rowcount:
                conn.execute(
                    """
                    UPDATE messages SET ai_status = 'pending', updated_at = datetime('now')
                    WHERE id IN (SELECT message_id FROM ai_queue WHERE status = 'pending')
                    """
                )
        return cur.rowcount

    def reset_error_items_for_retry(self, max_retries: int = 3) -> int:
        """Reset failed items that haven't exceeded *max_retries* back to pending."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT message_id FROM ai_queue WHERE status = 'error' AND retries < ?",
                (max_retries,),
            ).fetchall()
            ids = [int(r["message_id"]) for r in rows]
            if not ids:
                return 0
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"UPDATE ai_queue SET status = 'pending', updated_at = datetime('now') WHERE message_id IN ({placeholders})",
                ids,
            )
            conn.execute(
                f"UPDATE messages SET ai_status = 'pending', updated_at = datetime('now') WHERE id IN ({placeholders})",
                ids,
            )
        return len(ids)

    # ------------------------------------------------------------------
    # Debug persistence
    # ------------------------------------------------------------------

    def log_api_call(self, call_type: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO debug_api_calls(call_type, input_tokens, output_tokens) VALUES (?, ?, ?)",
                (call_type, input_tokens, output_tokens),
            )
            # Prune records older than 7 days
            conn.execute(
                "DELETE FROM debug_api_calls WHERE called_at < datetime('now', '-7 days')"
            )

    def load_api_calls(self, call_type: str, hours: int = 48) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT called_at, input_tokens, output_tokens FROM debug_api_calls "
                "WHERE call_type = ? AND called_at >= datetime('now', ? || ' hours') "
                "ORDER BY called_at ASC",
                (call_type, f"-{hours}"),
            ).fetchall()
        return [dict(r) for r in rows]

    def log_event(self, event_type: str, detail: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO debug_event_log(event_type, detail) VALUES (?, ?)",
                (event_type, detail),
            )
            # Keep only last 200 rows
            conn.execute(
                "DELETE FROM debug_event_log WHERE id NOT IN "
                "(SELECT id FROM debug_event_log ORDER BY id DESC LIMIT 200)"
            )

    def load_event_log(self, limit: int = 50) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT logged_at AS at, event_type AS type, detail FROM debug_event_log "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def log_run(self, at: str, updated: int, total: int, ingested: int,
                ai_processed: int, state: str, error: str | None) -> None:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO debug_run_history(ran_at, updated, total, ingested, ai_processed, state, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (at, updated, total, ingested, ai_processed, state, error),
            )
            # Keep only last 50 rows
            conn.execute(
                "DELETE FROM debug_run_history WHERE id NOT IN "
                "(SELECT id FROM debug_run_history ORDER BY id DESC LIMIT 50)"
            )

    def load_run_history(self, limit: int = 10) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT ran_at AS at, updated, total, ingested, ai_processed, state, error "
                "FROM debug_run_history ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def find_scored_message_by_hash(
        self, content_hash: str, hours: int = 6, exclude_id: int | None = None
    ) -> dict[str, Any] | None:
        """Return an already-scored message with the same content hash within *hours*."""
        if not content_hash:
            return None
        q = (
            "SELECT id, ai_score, ai_category FROM messages "
            "WHERE content_hash = ? AND ai_status = 'done' AND ai_score IS NOT NULL "
            "AND published_at >= datetime('now', ? || ' hours')"
        )
        params: list[Any] = [content_hash, f"-{hours}"]
        if exclude_id is not None:
            q += " AND id != ?"
            params.append(exclude_id)
        q += " ORDER BY published_at ASC LIMIT 1"
        with get_connection() as conn:
            row = conn.execute(q, params).fetchone()
        return dict(row) if row else None

    def mark_message_dedup(self, message_id: int, score: int, category: str | None) -> None:
        """Copy score/category from an original and mark this message as a duplicate."""
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE messages
                SET ai_score = ?, ai_category = ?, ai_status = 'done', is_dedup = 1,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (score, category, message_id),
            )
            conn.execute("DELETE FROM ai_queue WHERE message_id = ?", (message_id,))

    def get_stats_overview(self) -> dict[str, Any]:
        with get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) AS cnt FROM messages").fetchone()["cnt"]
            scored = conn.execute(
                "SELECT COUNT(*) AS cnt FROM messages WHERE ai_status = 'done' AND ai_score IS NOT NULL"
            ).fetchone()["cnt"]
            avg_row = conn.execute(
                "SELECT ROUND(AVG(CAST(ai_score AS REAL)), 1) AS avg FROM messages WHERE ai_status = 'done' AND ai_score IS NOT NULL"
            ).fetchone()
            sources = conn.execute("SELECT COUNT(*) AS cnt FROM sources WHERE is_active = 1").fetchone()["cnt"]
            dedup = conn.execute("SELECT COUNT(*) AS cnt FROM messages WHERE is_dedup = 1").fetchone()["cnt"]
        return {
            "total_messages": int(total or 0),
            "scored_messages": int(scored or 0),
            "avg_score": float(avg_row["avg"]) if avg_row["avg"] is not None else None,
            "active_sources": int(sources or 0),
            "dedup_count": int(dedup or 0),
        }

    def get_dedup_stats(self) -> dict[str, int]:
        with get_connection() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS cnt FROM messages WHERE is_dedup = 1"
            ).fetchone()["cnt"]
            h24 = conn.execute(
                "SELECT COUNT(*) AS cnt FROM messages WHERE is_dedup = 1 "
                "AND published_at >= datetime('now', '-24 hours')"
            ).fetchone()["cnt"]
        return {"dedup_total": int(total or 0), "dedup_24h": int(h24 or 0)}

    def get_stats_messages_over_time(self, days: int = 30) -> list[dict[str, Any]]:
        safe_days = max(1, min(int(days), 365))
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT DATE(published_at) AS day, COUNT(*) AS count
                FROM messages
                WHERE published_at >= DATE('now', ? || ' days')
                GROUP BY day
                ORDER BY day ASC
                """,
                (f"-{safe_days}",),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_stats_score_distribution(self, days: int | None = None) -> list[dict[str, Any]]:
        with get_connection() as conn:
            if days is not None:
                safe = max(1, min(int(days), 365))
                rows = conn.execute(
                    """
                    SELECT ai_score AS score, COUNT(*) AS count
                    FROM messages
                    WHERE ai_status = 'done' AND ai_score IS NOT NULL
                      AND published_at >= datetime('now', ? || ' days')
                    GROUP BY ai_score ORDER BY ai_score ASC
                    """,
                    (f"-{safe}",),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT ai_score AS score, COUNT(*) AS count
                    FROM messages
                    WHERE ai_status = 'done' AND ai_score IS NOT NULL
                    GROUP BY ai_score ORDER BY ai_score ASC
                    """
                ).fetchall()
        return [dict(r) for r in rows]

    def get_stats_categories(self, days: int | None = None) -> list[dict[str, Any]]:
        with get_connection() as conn:
            if days is not None:
                safe = max(1, min(int(days), 365))
                rows = conn.execute(
                    """
                    SELECT ai_category AS category, COUNT(*) AS count
                    FROM messages
                    WHERE ai_status = 'done'
                      AND ai_category IS NOT NULL
                      AND TRIM(ai_category) != ''
                      AND published_at >= datetime('now', ? || ' days')
                    GROUP BY ai_category ORDER BY count DESC LIMIT 15
                    """,
                    (f"-{safe}",),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT ai_category AS category, COUNT(*) AS count
                    FROM messages
                    WHERE ai_status = 'done'
                      AND ai_category IS NOT NULL
                      AND TRIM(ai_category) != ''
                    GROUP BY ai_category ORDER BY count DESC LIMIT 15
                    """
                ).fetchall()
        return [dict(r) for r in rows]

    def get_stats_sources(self, limit: int = 10, days: int | None = None) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 50))
        with get_connection() as conn:
            if days is not None:
                safe = max(1, min(int(days), 365))
                rows = conn.execute(
                    """
                    SELECT s.name, COUNT(m.id) AS count
                    FROM messages m
                    JOIN sources s ON s.id = m.source_id
                    WHERE m.ai_status = 'done'
                      AND m.published_at >= datetime('now', ? || ' days')
                    GROUP BY m.source_id ORDER BY count DESC LIMIT ?
                    """,
                    (f"-{safe}", safe_limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT s.name, COUNT(m.id) AS count
                    FROM messages m
                    JOIN sources s ON s.id = m.source_id
                    WHERE m.ai_status = 'done'
                    GROUP BY m.source_id ORDER BY count DESC LIMIT ?
                    """,
                    (safe_limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def _iter_published_local(self, days: int | None = None) -> list[datetime]:
        with get_connection() as conn:
            if days is not None:
                safe = max(1, min(int(days), 365))
                rows = conn.execute(
                    "SELECT published_at FROM messages WHERE published_at IS NOT NULL"
                    " AND published_at >= datetime('now', ? || ' days')",
                    (f"-{safe}",),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT published_at FROM messages WHERE published_at IS NOT NULL"
                ).fetchall()
        out: list[datetime] = []
        for r in rows:
            raw = r["published_at"]
            if not raw:
                continue
            try:
                dt = datetime.fromisoformat(str(raw).replace(" ", "T").replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            out.append(dt.astimezone(_KYIV_TZ))
        return out

    def get_stats_hours(self, days: int | None = None) -> list[dict[str, Any]]:
        hour_totals: dict[int, int] = {}
        all_dates: set = set()
        for dt in self._iter_published_local(days):
            hour_totals[dt.hour] = hour_totals.get(dt.hour, 0) + 1
            all_dates.add(dt.date())
        n_days = len(all_dates) or 1
        return [
            {"hour": h, "count": round(total / n_days, 1), "total": total}
            for h, total in sorted(hour_totals.items())
        ]

    def get_stats_weekday(self, days: int | None = None) -> list[dict[str, Any]]:
        # weekday(): Mon=0..Sun=6 — return average per weekday occurrence, not cumulative
        day_totals: dict[int, int] = {}
        day_dates: dict[int, set] = {}
        for dt in self._iter_published_local(days):
            wd = dt.weekday()
            day_totals[wd] = day_totals.get(wd, 0) + 1
            day_dates.setdefault(wd, set()).add(dt.date())
        names = {0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Нд"}
        result = []
        for i in range(7):
            total = day_totals.get(i, 0)
            n = len(day_dates.get(i, set()))
            avg = round(total / n, 1) if n > 0 else 0
            result.append({"weekday": i, "name": names[i], "count": avg, "total": total, "days": n})
        return result

    def get_stats_alerts(self) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT a.name, a.alert_type, COUNT(d.id) AS delivery_count
                FROM alerts a
                LEFT JOIN alert_deliveries d ON d.alert_id = a.id
                GROUP BY a.id
                ORDER BY delivery_count DESC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def list_dashboard_sessions(self, limit: int = 200, since_hours: int | None = None) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        where = ""
        params: list[Any] = []
        if since_hours is not None:
            where = "WHERE first_seen_at >= datetime('now', ? || ' hours')"
            params.append(f"-{since_hours}")
        params.append(safe_limit)
        with get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT id, session_key, ip, first_seen_at, last_seen_at,
                       active_seconds, heartbeat_count, user_agent, language,
                       timezone, screen, last_path
                FROM dashboard_sessions
                {where}
                ORDER BY first_seen_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Digest
    # ------------------------------------------------------------------

    def get_digest_messages(
        self,
        target_date: str | None = None,
        min_score: int = 6,
        excluded_categories: list[str] | None = None,
        max_per_category: int = 5,
        start_datetime: str | None = None,
        end_datetime: str | None = None,
    ) -> list[dict[str, Any]]:
        excl_clause = ""
        params: list[Any] = [min_score]
        if start_datetime and end_datetime:
            date_clause = "AND m.published_at BETWEEN ? AND ?"
            params.extend([start_datetime, end_datetime])
        else:
            date_clause = "AND DATE(m.published_at) = ?"
            params.append(target_date or "")
        if excluded_categories:
            placeholders = ",".join("?" * len(excluded_categories))
            excl_clause = f"AND m.ai_category NOT IN ({placeholders})"
            params.extend(excluded_categories)
        with get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT m.id, m.text, m.ai_score, m.ai_category, m.published_at,
                       s.name AS source_name
                FROM messages m
                JOIN sources s ON s.id = m.source_id
                WHERE m.ai_status = 'done'
                  AND m.is_dedup = 0
                  AND m.ai_score >= ?
                  {date_clause}
                  {excl_clause}
                ORDER BY m.ai_category, m.ai_score DESC
                """,
                params,
            ).fetchall()
        by_category: dict[str, list[dict]] = {}
        for row in rows:
            cat = str(row["ai_category"] or "Інше")
            if cat not in by_category:
                by_category[cat] = []
            if len(by_category[cat]) < max_per_category:
                by_category[cat].append(dict(row))
        result: list[dict] = []
        for msgs in by_category.values():
            result.extend(msgs)
        return result

    def save_digest(
        self,
        digest_date: str,
        content: str,
        message_count: int,
        status: str,
        model_name: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO digests(digest_date, content, message_count, status, generated_at,
                                    model_name, tokens_in, tokens_out)
                VALUES (?, ?, ?, ?, datetime('now'), ?, ?, ?)
                ON CONFLICT(digest_date) DO UPDATE SET
                    content=excluded.content,
                    message_count=excluded.message_count,
                    status=excluded.status,
                    generated_at=datetime('now'),
                    model_name=excluded.model_name,
                    tokens_in=excluded.tokens_in,
                    tokens_out=excluded.tokens_out
                """,
                (digest_date, content, message_count, status, model_name, tokens_in, tokens_out),
            )

    def get_digest(self, digest_date: str) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM digests WHERE digest_date = ?", (digest_date,)
            ).fetchone()
        return dict(row) if row else None

    def list_digests(self, limit: int = 7) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 30))
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT digest_date, status, message_count, generated_at, "
                "model_name, tokens_in, tokens_out "
                "FROM digests ORDER BY digest_date DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_digest(self, digest_date: str) -> bool:
        with get_connection() as conn:
            cur = conn.execute("DELETE FROM digests WHERE digest_date = ?", (digest_date,))
        return cur.rowcount > 0

    def cleanup_old_digests(self, keep_days: int = 30) -> None:
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM digests WHERE digest_date < date('now', ? || ' days')",
                (f"-{keep_days}",),
            )
