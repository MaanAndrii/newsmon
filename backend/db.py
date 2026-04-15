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
                alert_type TEXT NOT NULL DEFAULT 'new_message',
                source_id INTEGER NULL REFERENCES sources(id) ON DELETE SET NULL,
                min_score INTEGER NULL CHECK(min_score BETWEEN 0 AND 10),
                target_chat_id TEXT NOT NULL DEFAULT '',
                is_ai_keyword INTEGER NOT NULL DEFAULT 0,
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
        _ensure_column(conn, "alerts", "alert_type", "TEXT NOT NULL DEFAULT 'new_message'")
        _ensure_column(conn, "alerts", "source_id", "INTEGER NULL REFERENCES sources(id) ON DELETE SET NULL")
        _ensure_column(conn, "alerts", "min_score", "INTEGER NULL CHECK(min_score BETWEEN 0 AND 10)")
        _ensure_column(conn, "alerts", "target_chat_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "alerts", "is_ai_keyword", "INTEGER NOT NULL DEFAULT 0")


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
    ) -> list[dict[str, Any]]:
        where_parts = ["m.ai_status = 'done'"]
        params: list[Any] = []
        search_raw = (search_query or "").strip()
        category_raw = (category or "").strip()
        keyword_raw = (keyword or "").strip()
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
        if min_score is not None and int(min_score) > 0:
            where_parts.append("m.ai_score >= ?")
            params.append(int(min_score))
        params.append(limit)
        query = f"""
            SELECT m.id, m.source_id, s.name AS source_name, s.url AS source_url,
                   m.tg_message_id, m.published_at, m.text, m.media_type,
                   m.ai_score, m.ai_category, m.ai_status, m.workflow_status,
                   m.telegram_url, m.created_at
            FROM messages m
            JOIN sources s ON s.id = m.source_id
            WHERE {" AND ".join(where_parts)}
            ORDER BY datetime(m.published_at) DESC, m.id DESC
            LIMIT ?
        """
        with get_connection() as conn:
            try:
                rows = conn.execute(query, params).fetchall()
            except sqlite3.OperationalError:
                if search_raw:
                    fallback_params: list[Any] = []
                    fallback_where = ["m.ai_status = 'done'"]
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
                    if min_score is not None and int(min_score) > 0:
                        fallback_where.append("m.ai_score >= ?")
                        fallback_params.append(int(min_score))
                    fallback_params.append(limit)
                    fallback_query = f"""
                        SELECT m.id, m.source_id, s.name AS source_name, s.url AS source_url,
                               m.tg_message_id, m.published_at, m.text, m.media_type,
                               m.ai_score, m.ai_category, m.ai_status, m.workflow_status,
                               m.telegram_url, m.created_at
                        FROM messages m
                        JOIN sources s ON s.id = m.source_id
                        WHERE {" AND ".join(fallback_where)}
                        ORDER BY datetime(m.published_at) DESC, m.id DESC
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
                ORDER BY datetime(published_at) ASC, id ASC
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

    def list_dashboard_sessions(self, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, session_key, ip, first_seen_at, last_seen_at,
                       active_seconds, heartbeat_count, user_agent, language,
                       timezone, screen, last_path
                FROM dashboard_sessions
                ORDER BY datetime(last_seen_at) DESC, id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(r) for r in rows]

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
