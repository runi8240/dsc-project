import sqlite3
from pathlib import Path
from typing import Iterable, Tuple


def get_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path) -> None:
    conn = get_conn(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            hr INTEGER NOT NULL,
            user_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            track_id TEXT,
            track_name TEXT,
            artists TEXT,
            energy REAL,
            hr INTEGER,
            user_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            event_type TEXT NOT NULL,
            track_id TEXT,
            metadata TEXT,
            user_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            rest_hr INTEGER DEFAULT 60,
            max_hr INTEGER DEFAULT 190
        )
        """
    )
    _ensure_column(conn, "telemetry", "user_id", "TEXT")
    _ensure_column(conn, "recommendations", "user_id", "TEXT")
    _ensure_column(conn, "feedback", "user_id", "TEXT")
    _ensure_column(conn, "users", "rest_hr", "INTEGER DEFAULT 60")
    _ensure_column(conn, "users", "max_hr", "INTEGER DEFAULT 190")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_blacklist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            track_id TEXT NOT NULL,
            UNIQUE(user_id, track_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            track_id TEXT NOT NULL,
            energy REAL,
            danceability REAL,
            tempo REAL,
            valence REAL,
            UNIQUE(user_id, track_id)
        )
        """
    )
    conn.commit()
    conn.close()


def insert_many(conn: sqlite3.Connection, query: str, rows: Iterable[Tuple]) -> None:
    conn.executemany(query, rows)
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc):
            raise
