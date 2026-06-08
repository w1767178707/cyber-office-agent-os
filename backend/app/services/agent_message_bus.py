from __future__ import annotations
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

class AgentMessageBus:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS agent_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_npc_id TEXT NOT NULL,
                    to_npc_id TEXT NOT NULL DEFAULT '',
                    message_type TEXT NOT NULL DEFAULT 'request',
                    content TEXT NOT NULL,
                    related_task_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_agent_messages_to ON agent_messages(to_npc_id, id DESC)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_agent_messages_from ON agent_messages(from_npc_id, id DESC)')
            conn.commit()

    def send(self, *, from_npc_id: str, to_npc_id: str='', content: str='', message_type: str='request', related_task_id: int | None=None) -> dict[str, Any]:
        now = datetime.now().isoformat(timespec='seconds')
        with self._connect() as conn:
            cur = conn.execute('''
                INSERT INTO agent_messages(from_npc_id, to_npc_id, message_type, content, related_task_id, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'open', ?)
            ''', (from_npc_id, to_npc_id, message_type, content[:1200], related_task_id, now))
            conn.commit()
            return self.get(int(cur.lastrowid)) or {}

    def inbox(self, npc_id: str, *, limit: int=8) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute('''
                SELECT * FROM agent_messages
                WHERE (to_npc_id = ? OR to_npc_id = '') AND status = 'open'
                ORDER BY id DESC LIMIT ?
            ''', (npc_id, min(max(1, int(limit)), 50))).fetchall()
        return [dict(r) for r in rows]

    def recent(self, *, limit: int=30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute('SELECT * FROM agent_messages ORDER BY id DESC LIMIT ?', (min(max(1, int(limit)), 100),)).fetchall()
        return [dict(r) for r in rows]

    def get(self, message_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM agent_messages WHERE id = ?', (int(message_id),)).fetchone()
        return dict(row) if row else None
