from __future__ import annotations
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

class AuditLogStore:
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
                CREATE TABLE IF NOT EXISTS tool_audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    npc_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    risk_level TEXT NOT NULL DEFAULT 'low',
                    reason TEXT DEFAULT '',
                    args_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_tool_audit_npc ON tool_audit_logs(npc_id, id DESC)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_tool_audit_trace ON tool_audit_logs(trace_id)')
            conn.commit()

    def append(self, *, trace_id: str, npc_id: str, tool_name: str, status: str, risk_level: str='low', reason: str='', args: dict[str, Any] | None=None, result: dict[str, Any] | None=None, latency_ms: int=0) -> int:
        now = datetime.now().isoformat(timespec='seconds')
        with self._connect() as conn:
            cur = conn.execute('''
                INSERT INTO tool_audit_logs(trace_id, npc_id, tool_name, status, risk_level, reason, args_json, result_json, latency_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (trace_id, npc_id, tool_name, status, risk_level, reason, json.dumps(args or {}, ensure_ascii=False), json.dumps(result or {}, ensure_ascii=False), int(latency_ms), now))
            conn.commit()
            return int(cur.lastrowid)

    def recent(self, *, npc_id: str | None=None, limit: int=50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        where = ''
        params: list[Any] = []
        if npc_id:
            where = 'WHERE npc_id = ?'
            params.append(npc_id)
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(f'SELECT * FROM tool_audit_logs {where} ORDER BY id DESC LIMIT ?', params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for key in ('args_json', 'result_json'):
                try:
                    d[key.replace('_json', '')] = json.loads(d.get(key) or '{}')
                except Exception:
                    d[key.replace('_json', '')] = {}
                d.pop(key, None)
            out.append(d)
        return out
