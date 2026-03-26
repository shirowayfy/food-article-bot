from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

EKB_TZ = ZoneInfo("Asia/Yekaterinburg")


@dataclass
class FoodEntry:
    id: int
    user_id: int
    photo_file_id: str
    caption: str | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> FoodEntry:
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            photo_file_id=row["photo_file_id"],
            caption=row["caption"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class Storage:
    def __init__(self, db_path: str = "food_diary.db") -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def init(self) -> None:
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS food_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                photo_file_id TEXT NOT NULL,
                caption TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_entries_user_date
            ON food_entries (user_id, created_at)
            """
        )
        self._conn.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Storage not initialized. Call init() first.")
        return self._conn

    def save_entry(
        self,
        user_id: int,
        photo_file_id: str,
        caption: str | None = None,
    ) -> int:
        now_ekb = datetime.now(EKB_TZ).strftime("%Y-%m-%d %H:%M:%S")
        cursor = self.conn.execute(
            """
            INSERT INTO food_entries (user_id, photo_file_id, caption, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, photo_file_id, caption, now_ekb),
        )
        self.conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def get_today_entries(self, user_id: int) -> list[FoodEntry]:
        today = datetime.now(EKB_TZ).date().isoformat()
        rows = self.conn.execute(
            """
            SELECT * FROM food_entries
            WHERE user_id = ?
              AND date(created_at) = ?
            ORDER BY created_at ASC
            """,
            (user_id, today),
        ).fetchall()
        return [FoodEntry.from_row(row) for row in rows]

    def clear_today_entries(self, user_id: int) -> int:
        today = datetime.now(EKB_TZ).date().isoformat()
        cursor = self.conn.execute(
            """
            DELETE FROM food_entries
            WHERE user_id = ?
              AND date(created_at) = ?
            """,
            (user_id, today),
        )
        self.conn.commit()
        return cursor.rowcount

    def update_entry_time(self, entry_id: int, hour: int, minute: int) -> None:
        """Update the time portion of an entry's created_at."""
        row = self.conn.execute(
            "SELECT created_at FROM food_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if not row:
            return
        current = datetime.fromisoformat(row["created_at"])
        new_dt = current.replace(hour=hour, minute=minute, second=0)
        self.conn.execute(
            "UPDATE food_entries SET created_at = ? WHERE id = ?",
            (new_dt.strftime("%Y-%m-%d %H:%M:%S"), entry_id),
        )
        self.conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
