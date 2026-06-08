from __future__ import annotations
import json
import sqlite3
from datetime import datetime
from pathlib import Path
DEFAULT_TASKS: list[tuple[str, str, str, int, list[str]]] = []

class TaskManager:

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._seed_if_empty()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("\n                CREATE TABLE IF NOT EXISTS office_tasks (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    title TEXT NOT NULL,\n                    description TEXT DEFAULT '',\n                    owner_npc_id TEXT DEFAULT '',\n                    status TEXT NOT NULL DEFAULT 'todo',\n                    priority INTEGER NOT NULL DEFAULT 3,\n                    tags TEXT NOT NULL DEFAULT '[]',\n                    created_at TEXT NOT NULL,\n                    updated_at TEXT NOT NULL\n                )\n                ")
            conn.commit()

    def _seed_if_empty(self) -> None:


        if not DEFAULT_TASKS:
            return
        with self._connect() as conn:
            count = conn.execute('SELECT COUNT(*) FROM office_tasks').fetchone()[0]
            if count:
                return
            now = datetime.now().isoformat(timespec='seconds')
            conn.executemany("INSERT INTO office_tasks(title, description, owner_npc_id, status, priority, tags, created_at, updated_at) VALUES (?, ?, ?, 'todo', ?, ?, ?, ?)", [(t, d, o, p, json.dumps(tags, ensure_ascii=False), now, now) for t, d, o, p, tags in DEFAULT_TASKS])
            conn.commit()

    def create_task(self, title: str, description: str='', owner_npc_id: str='', priority: int=3, tags: list[str] | None=None) -> dict:
        now = datetime.now().isoformat(timespec='seconds')
        with self._connect() as conn:
            cur = conn.execute("INSERT INTO office_tasks(title, description, owner_npc_id, status, priority, tags, created_at, updated_at) VALUES (?, ?, ?, 'todo', ?, ?, ?, ?)", (title, description, owner_npc_id, priority, json.dumps(tags or [], ensure_ascii=False), now, now))
            conn.commit()
            return self.get_task(cur.lastrowid) or {}

    def list_tasks(self, status: str | None=None, owner_npc_id: str | None=None, limit: int=50) -> list[dict]:
        query = 'SELECT * FROM office_tasks WHERE 1=1'
        params: list = []
        if status:
            query += ' AND status = ?'
            params.append(status)
        if owner_npc_id:
            query += ' AND owner_npc_id = ?'
            params.append(owner_npc_id)
        query += " ORDER BY CASE status WHEN 'doing' THEN 0 WHEN 'review' THEN 1 WHEN 'todo' THEN 2 WHEN 'blocked' THEN 3 ELSE 4 END, priority DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            return [self._row_to_dict(r) for r in conn.execute(query, params).fetchall()]

    def get_task(self, task_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM office_tasks WHERE id = ?', (task_id,)).fetchone()
            return self._row_to_dict(row) if row else None

    def next_task_for(self, npc_id: str, role: str='') -> dict | None:
        rows = self.list_tasks(owner_npc_id=npc_id, limit=5)
        for status in ('doing', 'todo', 'review', 'blocked'):
            for row in rows:
                if row['status'] == status:
                    return row
        candidates = self.list_tasks(status='todo', limit=20)
        role_keywords = {'产品': ['product', 'demo', 'metrics'], '算法': ['memory', 'prompt', 'eval', 'agent'], '安全': ['security', 'audit'], '前端': ['frontend', 'demo', 'movement'], '后端': ['backend', 'llmops', 'deepseek'], '招聘': ['resume', 'interview'], '技术': ['architecture', 'planning', 'agent']}
        keys = []
        for k, v in role_keywords.items():
            if k in role:
                keys.extend(v)
        for c in candidates:
            if any((tag in c['tags'] for tag in keys)):
                self.assign_task(c['id'], npc_id)
                return self.get_task(c['id'])
        return candidates[0] if candidates else None

    def assign_task(self, task_id: int, owner_npc_id: str) -> None:
        now = datetime.now().isoformat(timespec='seconds')
        with self._connect() as conn:
            conn.execute('UPDATE office_tasks SET owner_npc_id = ?, updated_at = ? WHERE id = ?', (owner_npc_id, now, task_id))
            conn.commit()

    def update_status(self, task_id: int, status: str) -> None:
        now = datetime.now().isoformat(timespec='seconds')
        with self._connect() as conn:
            conn.execute('UPDATE office_tasks SET status = ?, updated_at = ? WHERE id = ?', (status, now, task_id))
            conn.commit()

    def progress_task(self, task_id: int, tick: int) -> dict | None:
        task = self.get_task(task_id)
        if not task:
            return None
        if task['status'] == 'todo':
            self.update_status(task_id, 'doing')
        return self.get_task(task_id)

    def advance_after_episode(self, task_id: int, outcome: str='progress') -> dict | None:
        task = self.get_task(task_id)
        if not task:
            return None
        status = task['status']
        if status == 'todo':
            self.update_status(task_id, 'doing')
        elif status == 'doing':
            self.update_status(task_id, 'review' if outcome != 'blocked' else 'blocked')
        elif status == 'review':
            self.update_status(task_id, 'done')
        elif status == 'blocked' and outcome != 'blocked':
            self.update_status(task_id, 'doing')
        return self.get_task(task_id)

    def list_tasks_with_tag(self, tag: str, limit: int=200) -> list[dict]:
        tag = str(tag or '').strip()
        if not tag:
            return []
        rows = self.list_tasks(limit=max(1, min(int(limit), 500)))
        return [r for r in rows if tag in (r.get('tags') or [])]

    def list_mission_tasks(self, mission_id: int, include_main: bool=True, limit: int=300) -> list[dict]:
        mission_tag = f'mission:{int(mission_id)}'
        rows = self.list_tasks(limit=max(1, min(int(limit), 500)))
        out = []
        for r in rows:
            tags = r.get('tags') or []
            if mission_tag in tags or (include_main and 'boss_mission' in tags and str(int(mission_id)) in [str(x).removeprefix('mission:') for x in tags]):
                out.append(r)
        return out

    def clear_all_tasks(self) -> int:
        with self._connect() as conn:
            cur = conn.execute('DELETE FROM office_tasks')
            conn.commit()
            return int(cur.rowcount)

    def delete_task(self, task_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute('DELETE FROM office_tasks WHERE id = ?', (int(task_id),))
            conn.commit()
            return cur.rowcount > 0

    def delete_tasks(self, task_ids: list[int]) -> int:
        count = 0
        for tid in task_ids:
            if self.delete_task(int(tid)):
                count += 1
        return count

    def active_count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM office_tasks WHERE status != 'done'").fetchone()[0])

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        data = dict(row)
        try:
            data['tags'] = json.loads(data.get('tags') or '[]')
        except Exception:
            data['tags'] = []
        return data
