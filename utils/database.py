"""
utils/database.py – SQLite persistence layer for ET-Talks Studio Pro.

Manages two tables:
  - render_history  : records of completed render jobs
  - projects        : saved project configurations (extension point)

All access goes through the Database class. No ORM dependency — uses the
standard library sqlite3 module with Row factory for dict-like access.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS render_history (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL DEFAULT '',
    source_url      TEXT NOT NULL DEFAULT '',
    thumbnail_path  TEXT NOT NULL DEFAULT '',
    output_filename TEXT NOT NULL DEFAULT '',
    output_path     TEXT NOT NULL DEFAULT '',
    duration        REAL NOT NULL DEFAULT 0,
    resolution      TEXT NOT NULL DEFAULT '1080x1920',
    quality         TEXT NOT NULL DEFAULT 'high',
    crop_mode       TEXT NOT NULL DEFAULT 'blur_background',
    encoder         TEXT NOT NULL DEFAULT 'libx264',
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    render_settings TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT 'Untitled Project',
    source_url  TEXT NOT NULL DEFAULT '',
    settings    TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------

class Database:
    """Thin SQLite wrapper for ET-Talks Studio Pro persistence."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path: Path = db_path or config.DATABASE_PATH
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _cursor(self) -> Generator[tuple[sqlite3.Connection, sqlite3.Cursor], None, None]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            yield conn, cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        with self._cursor() as (conn, cur):
            cur.executescript(_SCHEMA)
        logger.debug("Database initialized at %s", self._db_path)

    # ------------------------------------------------------------------
    # Render history CRUD
    # ------------------------------------------------------------------

    def add_history(
        self,
        *,
        record_id: str,
        title: str,
        source_url: str,
        thumbnail_path: str,
        output_filename: str,
        output_path: str,
        duration: float,
        resolution: str = "1080x1920",
        quality: str = "high",
        crop_mode: str = "blur_background",
        encoder: str = "libx264",
        file_size_bytes: int = 0,
        render_settings: dict[str, Any] | None = None,
    ) -> None:
        """Insert a new render history record."""
        settings_json = json.dumps(render_settings or {})
        created_at = datetime.utcnow().isoformat()
        with self._cursor() as (conn, cur):
            cur.execute(
                """
                INSERT INTO render_history
                  (id, title, source_url, thumbnail_path, output_filename,
                   output_path, duration, resolution, quality, crop_mode,
                   encoder, file_size_bytes, created_at, render_settings)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record_id, title, source_url, thumbnail_path,
                    output_filename, output_path, duration, resolution,
                    quality, crop_mode, encoder, file_size_bytes,
                    created_at, settings_json,
                ),
            )
        logger.info("History record added: %s — %s", record_id, title)

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return render history, newest first."""
        with self._cursor() as (conn, cur):
            cur.execute(
                "SELECT * FROM render_history ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_history_item(self, record_id: str) -> dict[str, Any] | None:
        """Return a single history record by ID, or None."""
        with self._cursor() as (conn, cur):
            cur.execute(
                "SELECT * FROM render_history WHERE id = ?", (record_id,)
            )
            row = cur.fetchone()
        return self._row_to_dict(row) if row else None

    def delete_history(self, record_id: str) -> bool:
        """Delete a history record. Returns True if a row was deleted."""
        with self._cursor() as (conn, cur):
            cur.execute(
                "DELETE FROM render_history WHERE id = ?", (record_id,)
            )
            deleted = cur.rowcount > 0
        if deleted:
            logger.info("History record deleted: %s", record_id)
        return deleted

    def search_history(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Full-text search over title and source_url fields."""
        pattern = f"%{query}%"
        with self._cursor() as (conn, cur):
            cur.execute(
                """
                SELECT * FROM render_history
                WHERE title LIKE ? OR source_url LIKE ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (pattern, pattern, limit),
            )
            rows = cur.fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Projects CRUD (extension point)
    # ------------------------------------------------------------------

    def save_project(
        self,
        *,
        project_id: str,
        name: str,
        source_url: str,
        settings: dict[str, Any],
    ) -> None:
        """Upsert a project record."""
        now = datetime.utcnow().isoformat()
        settings_json = json.dumps(settings)
        with self._cursor() as (conn, cur):
            cur.execute(
                """
                INSERT INTO projects (id, name, source_url, settings, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name,
                  settings=excluded.settings,
                  updated_at=excluded.updated_at
                """,
                (project_id, name, source_url, settings_json, now, now),
            )

    def get_projects(self) -> list[dict[str, Any]]:
        """Return all projects, newest first."""
        with self._cursor() as (conn, cur):
            cur.execute("SELECT * FROM projects ORDER BY updated_at DESC")
            rows = cur.fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        # Deserialize JSON fields
        for key in ("render_settings", "settings"):
            if key in d and isinstance(d[key], str):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    d[key] = {}
        return d


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

db = Database()
