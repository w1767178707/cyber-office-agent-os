from __future__ import annotations
import sqlite3
import uuid
from pathlib import Path
from typing import Iterable, Any

class MemoryStore:

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.fts_enabled = False
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute('PRAGMA journal_mode=WAL;')
            conn.execute("\n                CREATE TABLE IF NOT EXISTS conversations (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    session_id TEXT NOT NULL,\n                    npc_id TEXT NOT NULL,\n                    player_name TEXT NOT NULL,\n                    player_message TEXT NOT NULL,\n                    npc_reply TEXT NOT NULL,\n                    affinity_level TEXT NOT NULL,\n                    affinity_score INTEGER NOT NULL,\n                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))\n                );\n            ")
            conn.execute("\n                CREATE TABLE IF NOT EXISTS memories (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    npc_id TEXT NOT NULL,\n                    player_name TEXT NOT NULL,\n                    content TEXT NOT NULL,\n                    importance INTEGER NOT NULL DEFAULT 1,\n                    kind TEXT NOT NULL DEFAULT 'dialogue',\n                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))\n                );\n            ")
            for ddl in ['ALTER TABLE memories ADD COLUMN compressed INTEGER NOT NULL DEFAULT 0', "ALTER TABLE memories ADD COLUMN summary_batch_id TEXT NOT NULL DEFAULT ''"]:
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass
            conn.execute('CREATE INDEX IF NOT EXISTS idx_memories_owner ON memories(npc_id, player_name, id DESC);')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_memories_compact ON memories(npc_id, player_name, compressed, kind, id DESC);')
            try:
                conn.execute('\n                    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(\n                        content,\n                        npc_id UNINDEXED,\n                        player_name UNINDEXED,\n                        memory_id UNINDEXED\n                    );\n                ')
                self.fts_enabled = True
            except sqlite3.OperationalError:
                self.fts_enabled = False
            conn.commit()

    def add_conversation(self, session_id: str, npc_id: str, player_name: str, player_message: str, npc_reply: str, affinity_level: str, affinity_score: int) -> int:
        with self._connect() as conn:
            cur = conn.execute('\n                INSERT INTO conversations(session_id, npc_id, player_name, player_message, npc_reply, affinity_level, affinity_score)\n                VALUES (?, ?, ?, ?, ?, ?, ?)\n                ', (session_id, npc_id, player_name, player_message, npc_reply, affinity_level, affinity_score))
            conn.commit()
            return int(cur.lastrowid)

    def add_memory(self, npc_id: str, player_name: str, content: str, importance: int=1, kind: str='dialogue') -> int:
        content = (content or '').strip()
        if not content:
            content = '空记忆片段'
        importance = max(1, min(5, int(importance)))
        with self._connect() as conn:
            cur = conn.execute("INSERT INTO memories(npc_id, player_name, content, importance, kind, compressed, summary_batch_id) VALUES (?, ?, ?, ?, ?, 0, '')", (npc_id, player_name, content, importance, kind))
            memory_id = int(cur.lastrowid)
            if self.fts_enabled:
                conn.execute('INSERT INTO memories_fts(content, npc_id, player_name, memory_id) VALUES (?, ?, ?, ?)', (content, npc_id, player_name, str(memory_id)))
            conn.commit()
            return memory_id

    def search_memories(self, npc_id: str, player_name: str, query: str, limit: int=5) -> list[dict]:
        query = (query or '').strip()
        visible_filter = "(m.compressed = 0 OR m.kind = 'memory_summary')"
        with self._connect() as conn:
            if self.fts_enabled and query:
                terms = [t for t in self._tokenize(query) if t]
                fts_query = ' OR '.join((f'"{t}"' for t in terms[:8])) or '""'
                try:
                    rows = conn.execute(f'\n                        SELECT m.id, m.npc_id, m.player_name, m.content, m.importance, m.kind, m.created_at,\n                               m.compressed, m.summary_batch_id,\n                               bm25(memories_fts) * -1 + m.importance * 0.7 AS score\n                        FROM memories_fts\n                        JOIN memories m ON m.id = CAST(memories_fts.memory_id AS INTEGER)\n                        WHERE memories_fts MATCH ?\n                          AND m.npc_id = ?\n                          AND m.player_name = ?\n                          AND {visible_filter}\n                        ORDER BY score DESC, m.id DESC\n                        LIMIT ?\n                        ', (fts_query, npc_id, player_name, limit)).fetchall()
                    if rows:
                        return [dict(r) for r in rows]
                except sqlite3.OperationalError:
                    pass
            like = f'%{query[:80]}%' if query else '%'
            rows = conn.execute(f"\n                SELECT id, npc_id, player_name, content, importance, kind, created_at, compressed, summary_batch_id,\n                       importance * 0.7 + CASE WHEN kind = 'memory_summary' THEN 1.2 ELSE 0 END AS score\n                FROM memories m\n                WHERE npc_id = ? AND player_name = ? AND content LIKE ? AND {visible_filter}\n                ORDER BY score DESC, id DESC\n                LIMIT ?\n                ", (npc_id, player_name, like, limit)).fetchall()
            if rows:
                return [dict(r) for r in rows]
            rows = conn.execute(f"\n                SELECT id, npc_id, player_name, content, importance, kind, created_at, compressed, summary_batch_id,\n                       importance * 0.5 + CASE WHEN kind = 'memory_summary' THEN 1.0 ELSE 0 END AS score\n                FROM memories m\n                WHERE npc_id = ? AND player_name = ? AND {visible_filter}\n                ORDER BY id DESC\n                LIMIT ?\n                ", (npc_id, player_name, limit)).fetchall()
            return [dict(r) for r in rows]

    def recent_conversations(self, npc_id: str, player_name: str, limit: int=4) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute('\n                SELECT player_message, npc_reply, affinity_level, affinity_score, created_at\n                FROM conversations\n                WHERE npc_id = ? AND player_name = ?\n                ORDER BY id DESC\n                LIMIT ?\n                ', (npc_id, player_name, limit)).fetchall()
            return [dict(r) for r in reversed(rows)]

    def list_memories(self, npc_id: str | None=None, player_name: str | None=None, limit: int=50, include_compressed: bool=False) -> list[dict]:
        clauses = []
        values: list[str | int] = []
        if npc_id:
            clauses.append('npc_id = ?')
            values.append(npc_id)
        if player_name:
            clauses.append('player_name = ?')
            values.append(player_name)
        if not include_compressed:
            clauses.append("(compressed = 0 OR kind = 'memory_summary')")
        where = 'WHERE ' + ' AND '.join(clauses) if clauses else ''
        values.append(limit)
        with self._connect() as conn:
            rows = conn.execute(f'\n                SELECT id, npc_id, player_name, content, importance, kind, created_at, compressed, summary_batch_id\n                FROM memories\n                {where}\n                ORDER BY id DESC\n                LIMIT ?\n                ', tuple(values)).fetchall()
            return [dict(r) for r in rows]

    def compaction_candidates(self, npc_id: str, player_name: str, *, threshold: int=72, keep_recent: int=24, batch_size: int=36) -> list[dict]:
        threshold = max(8, int(threshold))
        keep_recent = max(4, int(keep_recent))
        batch_size = max(6, int(batch_size))
        with self._connect() as conn:
            rows = conn.execute("\n                SELECT id, npc_id, player_name, content, importance, kind, created_at, compressed, summary_batch_id\n                FROM memories\n                WHERE npc_id = ? AND player_name = ? AND compressed = 0 AND kind != 'memory_summary'\n                ORDER BY id DESC\n                LIMIT ?\n                ", (npc_id, player_name, threshold + keep_recent + batch_size + 8)).fetchall()
        rows = [dict(r) for r in rows]
        if len(rows) <= threshold:
            return []
        older = rows[keep_recent:]
        if not older:
            return []
        return list(reversed(older[-batch_size:]))

    def search_public_memories(self, query: str, *, public_npc_id: str='__public__', public_player_name: str='__shared__', limit: int=5) -> list[dict]:
        return self.search_memories(public_npc_id, public_player_name, query, limit=limit)

    def add_public_memory(self, content: str, *, public_npc_id: str='__public__', public_player_name: str='__shared__', importance: int=3, kind: str='public_shared') -> int:
        return self.add_memory(public_npc_id, public_player_name, content, importance=importance, kind=kind)

    def active_streams(self, *, threshold: int=1, limit: int=200) -> list[dict]:
        threshold = max(1, int(threshold))
        limit = max(1, min(int(limit), 1000))
        with self._connect() as conn:
            rows = conn.execute('''
                SELECT npc_id, player_name, COUNT(*) AS active_count,
                       SUM(CASE WHEN kind = 'memory_summary' THEN 1 ELSE 0 END) AS summary_count,
                       MAX(id) AS last_memory_id
                FROM memories
                WHERE compressed = 0 AND kind != 'memory_summary'
                GROUP BY npc_id, player_name
                HAVING active_count > ?
                ORDER BY active_count DESC, last_memory_id DESC
                LIMIT ?
            ''', (threshold, limit)).fetchall()
            return [dict(r) for r in rows]

    def mark_compressed(self, ids: Iterable[int], batch_id: str | None=None) -> int:
        id_list = [int(x) for x in ids]
        if not id_list:
            return 0
        batch_id = batch_id or uuid.uuid4().hex[:12]
        placeholders = ','.join(('?' for _ in id_list))
        with self._connect() as conn:
            conn.execute(f'UPDATE memories SET compressed = 1, summary_batch_id = ? WHERE id IN ({placeholders})', (batch_id, *id_list))
            conn.commit()
        return len(id_list)

    def stats(self, npc_id: str | None=None, player_name: str | None=None) -> dict[str, Any]:
        clauses = []
        values: list[str] = []
        if npc_id:
            clauses.append('npc_id = ?')
            values.append(npc_id)
        if player_name:
            clauses.append('player_name = ?')
            values.append(player_name)
        where = 'WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self._connect() as conn:
            row = conn.execute(f"\n                SELECT COUNT(*) AS total,\n                       SUM(CASE WHEN compressed = 0 THEN 1 ELSE 0 END) AS active,\n                       SUM(CASE WHEN compressed = 1 THEN 1 ELSE 0 END) AS compressed,\n                       SUM(CASE WHEN kind = 'memory_summary' THEN 1 ELSE 0 END) AS summaries\n                FROM memories\n                {where}\n                ", tuple(values)).fetchone()
        return {k: int(row[k] or 0) for k in ['total', 'active', 'compressed', 'summaries']}

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        cleaned = ''.join((ch if ch.isalnum() or '一' <= ch <= '鿿' else ' ' for ch in text))
        tokens = cleaned.split()
        if not tokens and cleaned:
            tokens = [cleaned[:24]]
        if any(('一' <= ch <= '鿿' for ch in cleaned)):
            compact = cleaned.replace(' ', '')
            for size in (2, 3, 4):
                tokens.extend((compact[i:i + size] for i in range(0, max(0, min(len(compact) - size + 1, 12)))))
        return list(dict.fromkeys((t for t in tokens if t.strip())))
