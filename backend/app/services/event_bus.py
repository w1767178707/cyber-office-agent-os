from __future__ import annotations
import json
import sqlite3
from pathlib import Path

class EventBus:

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
            conn.execute("\n                CREATE TABLE IF NOT EXISTS office_events (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    event_type TEXT NOT NULL,\n                    content TEXT NOT NULL,\n                    involved_npcs TEXT NOT NULL DEFAULT '[]',\n                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))\n                );\n            ")
            conn.commit()

    def publish(self, event_type: str, content: str, involved_npcs: list[str] | None=None) -> int:
        with self._connect() as conn:
            cur = conn.execute('INSERT INTO office_events(event_type, content, involved_npcs) VALUES (?, ?, ?)', (event_type, content, json.dumps(involved_npcs or [], ensure_ascii=False)))
            conn.commit()
            return int(cur.lastrowid)

    def recent(self, limit: int=10, npc_id: str | None=None) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute('SELECT id, event_type, content, involved_npcs, created_at FROM office_events ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        result = []
        for r in rows:
            item = dict(r)
            try:
                item['involved_npcs'] = json.loads(item['involved_npcs'])
            except Exception:
                item['involved_npcs'] = []
            if npc_id and npc_id not in item['involved_npcs'] and (item['event_type'] != 'public'):
                continue
            result.append(item)
        return result
