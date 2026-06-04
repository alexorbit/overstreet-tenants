"""SQLite FSM Storage para aiogram 3 — persiste estado entre restarts."""
import json
import sqlite3
import threading
import asyncio
from pathlib import Path
from typing import Any, Dict
from aiogram.fsm.storage.base import BaseStorage, StorageKey, StateType
from aiogram.fsm.state import State


class SQLiteFSMStorage(BaseStorage):
    """FSM storage backed by SQLite. Thread-safe via lock + asyncio.to_thread."""

    def __init__(self, db_path: Path | str):
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS fsm_state (
                    chat_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    state TEXT,
                    data TEXT DEFAULT '{}',
                    PRIMARY KEY (chat_id, user_id)
                )
            """)
            self._conn.commit()
        return self._conn

    def _set_state_sync(self, chat_id: str, user_id: str, state: str | None):
        conn = self._get_conn()
        with self._lock:
            conn.execute("""
                INSERT INTO fsm_state (chat_id, user_id, state, data) VALUES (?, ?, ?, '{}')
                ON CONFLICT(chat_id, user_id) DO UPDATE SET state = excluded.state
            """, (chat_id, user_id, state))
            conn.commit()

    def _get_state_sync(self, chat_id: str, user_id: str) -> str | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT state FROM fsm_state WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id)
        ).fetchone()
        return row[0] if row else None

    def _set_data_sync(self, chat_id: str, user_id: str, data: dict):
        conn = self._get_conn()
        with self._lock:
            conn.execute("""
                INSERT INTO fsm_state (chat_id, user_id, state, data) VALUES (?, ?, NULL, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET data = excluded.data
            """, (chat_id, user_id, json.dumps(data, ensure_ascii=False)))
            conn.commit()

    def _get_data_sync(self, chat_id: str, user_id: str) -> dict:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT data FROM fsm_state WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id)
        ).fetchone()
        if not row or not row[0]:
            return {}
        try:
            return json.loads(row[0])
        except Exception:
            return {}

    def _reset_sync(self, chat_id: str, user_id: str):
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "DELETE FROM fsm_state WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id)
            )
            conn.commit()

    async def set_state(self, key: StorageKey, state: StateType = None):
        state_str = state.state if isinstance(state, State) else (str(state) if state else None)
        await asyncio.to_thread(
            self._set_state_sync,
            str(key.chat_id), str(key.user_id), state_str
        )

    async def get_state(self, key: StorageKey) -> str | None:
        return await asyncio.to_thread(
            self._get_state_sync,
            str(key.chat_id), str(key.user_id)
        )

    async def set_data(self, key: StorageKey, data: Dict[str, Any]):
        await asyncio.to_thread(
            self._set_data_sync,
            str(key.chat_id), str(key.user_id), data
        )

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self._get_data_sync,
            str(key.chat_id), str(key.user_id)
        )

    async def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
