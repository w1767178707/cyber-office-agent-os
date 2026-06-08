from __future__ import annotations
import sqlite3
from pathlib import Path

class RelationshipManager:
    LEVELS = [(20, '陌生'), (40, '熟悉'), (60, '友好'), (80, '亲密'), (100, '挚友')]
    POSITIVE = ['谢谢', '感谢', '喜欢', '很棒', '厉害', '赞', '请', '帮', '支持', '开心', '优秀', '学习', '合作']
    NEGATIVE = ['讨厌', '垃圾', '闭嘴', '烦', '差劲', '笨', '滚', '不行', '攻击', '骂', '废物']

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
            conn.execute("\n                CREATE TABLE IF NOT EXISTS relationships (\n                    npc_id TEXT NOT NULL,\n                    player_name TEXT NOT NULL,\n                    score INTEGER NOT NULL DEFAULT 0,\n                    level TEXT NOT NULL DEFAULT '陌生',\n                    interaction_count INTEGER NOT NULL DEFAULT 0,\n                    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),\n                    PRIMARY KEY(npc_id, player_name)\n                );\n            ")
            conn.commit()

    def get_level(self, score: int) -> str:
        score = max(0, min(100, int(score)))
        for upper, level in self.LEVELS:
            if score <= upper:
                return level
        return '挚友'

    def get_affinity(self, npc_id: str, player_name: str) -> dict:
        with self._connect() as conn:
            row = conn.execute('SELECT npc_id, player_name, score, level, interaction_count, updated_at FROM relationships WHERE npc_id = ? AND player_name = ?', (npc_id, player_name)).fetchone()
            if row:
                return dict(row)
            now = self._now(conn)
            return {'npc_id': npc_id, 'player_name': player_name, 'score': 0, 'level': '陌生', 'interaction_count': 0, 'updated_at': now}

    def analyze_delta(self, player_message: str) -> int:
        text = player_message.lower()
        pos = sum((1 for w in self.POSITIVE if w in text))
        neg = sum((1 for w in self.NEGATIVE if w in text))
        if neg > pos:
            return max(-6, -2 - neg)
        if pos > 0:
            return min(8, 3 + pos)
        if '?' in text or '？' in text:
            return 2
        return 1

    def update_affinity(self, npc_id: str, player_name: str, player_message: str) -> dict:
        delta = self.analyze_delta(player_message)
        current = self.get_affinity(npc_id, player_name)
        score = max(0, min(100, int(current['score']) + delta))
        level = self.get_level(score)
        count = int(current['interaction_count']) + 1
        with self._connect() as conn:
            conn.execute("\n                INSERT INTO relationships(npc_id, player_name, score, level, interaction_count, updated_at)\n                VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))\n                ON CONFLICT(npc_id, player_name) DO UPDATE SET\n                    score=excluded.score,\n                    level=excluded.level,\n                    interaction_count=excluded.interaction_count,\n                    updated_at=datetime('now','localtime')\n                ", (npc_id, player_name, score, level, count))
            conn.commit()
        updated = self.get_affinity(npc_id, player_name)
        updated['score_delta'] = delta
        return updated

    @staticmethod
    def _now(conn: sqlite3.Connection) -> str:
        return conn.execute("SELECT datetime('now','localtime') AS now").fetchone()['now']
