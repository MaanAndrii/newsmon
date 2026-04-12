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
            """
        )


class Repository:
    def list_sources(self) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, name, url, is_active, created_at FROM sources ORDER BY id DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def create_source(self, name: str, url: str) -> dict[str, Any]:
        with get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO sources(name, url) VALUES (?, ?)",
                (name, url),
            )
            row = conn.execute(
                "SELECT id, name, url, is_active, created_at FROM sources WHERE id = ?",
                (cur.lastrowid,),
            ).fetchone()
        return dict(row)

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
