from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from ..llm import LLMClient
from ..models import NPCProfile
from .safety_guard import SafetyGuard
from .task_manager import TaskManager
from .event_bus import EventBus
from .memory_router import MemoryRouter
from .company_resources import CompanyResourceCenter

def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')

def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)

class BossMissionService:

    def __init__(
        self,
        *,
        db_path: Path,
        llm: LLMClient,
        tasks: TaskManager,
        events: EventBus,
        safety: SafetyGuard,
        memory_router: MemoryRouter | None = None,
        resource_center: CompanyResourceCenter | None = None,
    ):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.llm = llm
        self.tasks = tasks
        self.events = events
        self.safety = safety
        self.memory_router = memory_router
        self.resource_center = resource_center
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS boss_missions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    boss_name TEXT NOT NULL DEFAULT '老板',
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    desired_outcome TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    main_task_id INTEGER DEFAULT 0,
                    dispatch_json TEXT NOT NULL DEFAULT '[]',
                    report_text TEXT DEFAULT '',
                    report_resource_id INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            ''')
            conn.commit()

    async def submit_mission(self, *, boss_name: str, title: str, description: str = '', desired_outcome: str = '', priority: int = 5, profiles: list[NPCProfile], auto_dispatch: bool = True) -> dict[str, Any]:
        verdict = self.safety.inspect(' '.join([title, description, desired_outcome])[:5000], actor=boss_name or '老板', tool_name='boss_submit_mission')
        if not verdict.allowed:
            return {'ok': False, 'blocked': True, 'reason': verdict.reason, 'risk_level': verdict.risk_level}
        now = _now()
        with self._connect() as conn:
            cur = conn.execute('''
                INSERT INTO boss_missions(boss_name, title, description, desired_outcome, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?)
            ''', ((boss_name or '老板')[:32], title[:160], description[:6000], desired_outcome[:3000], now, now))
            mission_id = int(cur.lastrowid)
            conn.commit()
        main_owner = self._find_owner(profiles, prefer=['CTO', '技术负责人']) or (profiles[0].npc_id if profiles else '')
        main_task = self.tasks.create_task(
            title=f'老板主任务｜{title}'[:120],
            description=f'老板：{boss_name or "老板"}\n主任务：{title}\n背景：{description}\n期望结果：{desired_outcome}\n\n要求：各 Agent 自行拆分任务、判断是否调用工具、沉淀共享资源，完成后输出完整任务报告。',
            owner_npc_id=main_owner,
            priority=priority,
            tags=['boss_mission', f'mission:{mission_id}', 'needs_dispatch'],
        )
        with self._connect() as conn:
            conn.execute('UPDATE boss_missions SET main_task_id = ?, updated_at = ? WHERE id = ?', (int(main_task['id']), _now(), mission_id))
            conn.commit()
        subtasks: list[dict[str, Any]] = []
        if auto_dispatch:
            subtasks = await self.dispatch_mission(mission_id=mission_id, profiles=profiles)
        event_ids = [self.events.publish('boss_mission_created', f'老板发布主任务：{title}。公司成员将自行拆分、协作和执行。', [main_owner])]
        if subtasks:
            event_ids.append(self.events.publish('boss_mission_dispatched', f'主任务 #{mission_id} 已由 Agent 团队拆分为 {len(subtasks)} 个子任务。', [s.get('owner_npc_id', '') for s in subtasks]))
        if self.memory_router:
            await self.memory_router.remember(npc_id=main_owner or '__company__', player_name='__office__', content=f'【老板主任务】{title}\n{description}\n期望结果：{desired_outcome}', importance=5, kind='boss_mission', npc_name='公司任务中枢', role='company', share_check=True)
        return {'ok': True, 'mission': self.get_mission(mission_id), 'main_task': main_task, 'subtasks': subtasks, 'events': event_ids}

    async def dispatch_mission(self, *, mission_id: int, profiles: list[NPCProfile]) -> list[dict[str, Any]]:
        mission = self.get_mission(mission_id)
        if not mission:
            return []
        existing = [t for t in self.tasks.list_mission_tasks(mission_id, include_main=False) if 'boss_subtask' in (t.get('tags') or [])]
        if existing:
            return existing
        plan = await self._llm_dispatch_plan(mission, profiles)
        if not plan:
            plan = self._fallback_dispatch_plan(mission, profiles)
        created: list[dict[str, Any]] = []
        for item in plan[: max(1, min(12, len(plan)) )]:
            owner = item.get('owner_npc_id') or self._owner_by_role_hint(profiles, item.get('role_hint', '')) or (profiles[0].npc_id if profiles else '')
            title = str(item.get('title') or '主任务子任务')[:120]
            desc = str(item.get('description') or mission.get('description') or '')[:1000]
            tags = list(dict.fromkeys(['boss_subtask', f'mission:{mission_id}'] + [str(x) for x in item.get('tags', []) if str(x).strip()]))
            task = self.tasks.create_task(title=title, description=desc, owner_npc_id=owner, priority=int(item.get('priority') or mission.get('priority') or 4), tags=tags)
            created.append(task)
        self.tasks.update_status(int(mission['main_task_id']), 'doing')
        with self._connect() as conn:
            conn.execute('UPDATE boss_missions SET dispatch_json = ?, updated_at = ? WHERE id = ?', (_json(created), _now(), mission_id))
            conn.commit()
        return created

    async def maybe_generate_ready_reports(self, *, profiles: list[NPCProfile]) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        for m in self.list_missions(status='active', limit=20):
            ready = self._mission_ready(m['id'])
            if ready:
                report = await self.generate_report(mission_id=int(m['id']), profiles=profiles, force=False)
                if report.get('ok'):
                    reports.append(report)
        return reports

    async def generate_report(self, *, mission_id: int, profiles: list[NPCProfile], force: bool = False, note: str = '') -> dict[str, Any]:
        mission = self.get_mission(mission_id)
        if not mission:
            return {'ok': False, 'reason': f'mission {mission_id} not found'}
        ready = self._mission_ready(mission_id)
        if not ready and not force:
            return {'ok': False, 'ready': False, 'reason': '仍有子任务未完成；可 force=true 生成阶段性报告。', 'mission': mission}
        tasks = self.tasks.list_mission_tasks(mission_id, include_main=True)
        report = await self._llm_report(mission, tasks, profiles, note=note)
        if not report:
            report = self._fallback_report(mission, tasks, profiles, note=note)
        resource: dict[str, Any] = {}
        if self.resource_center:
            resource = self.resource_center.create_document(
                owner_npc_id=self._find_owner(profiles, prefer=['CTO', '产品']) or (profiles[0].npc_id if profiles else 'guo_cto'),
                title=f'任务报告｜{mission["title"]}'[:180],
                document_type='mission_report',
                topic=mission['title'],
                context=f'老板主任务 #{mission_id} 完整任务报告',
                content=report,
                publish=True,
                metadata={'mission_id': mission_id, 'created_by': 'BossMissionService'},
            )
        rid = int((resource.get('resource') or {}).get('id') or 0) if isinstance(resource, dict) else 0
        with self._connect() as conn:
            conn.execute('UPDATE boss_missions SET status = ?, report_text = ?, report_resource_id = ?, updated_at = ? WHERE id = ?', ('reported', report, rid, _now(), mission_id))
            conn.commit()
        for t in tasks:
            if 'boss_mission' in (t.get('tags') or []):
                self.tasks.update_status(int(t['id']), 'done')
        event_id = self.events.publish('boss_mission_reported', f'老板主任务 #{mission_id} 已形成完整任务报告：{mission["title"]}', [t.get('owner_npc_id', '') for t in tasks[:8]])
        if self.memory_router:
            await self.memory_router.remember(npc_id='__company__', player_name='__office__', content=f'【任务报告】{mission["title"]}\n{report[:1600]}', importance=5, kind='mission_report', npc_name='公司任务中枢', role='company', share_check=True)
        return {'ok': True, 'ready': True, 'mission': self.get_mission(mission_id), 'report': report, 'resource': resource, 'event_id': event_id}

    def list_missions(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        where = ''
        params: list[Any] = []
        if status:
            where = 'WHERE status = ?'
            params.append(status)
        params.append(max(1, min(int(limit), 200)))
        with self._connect() as conn:
            rows = conn.execute(f'SELECT * FROM boss_missions {where} ORDER BY id DESC LIMIT ?', tuple(params)).fetchall()
        return [self._row_to_mission(r) for r in rows]

    def get_mission(self, mission_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM boss_missions WHERE id = ?', (int(mission_id),)).fetchone()
        return self._row_to_mission(row) if row else None

    def active_mission_count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM boss_missions WHERE status = 'active'").fetchone()[0])

    def has_active_or_recent_mission(self) -> bool:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM boss_missions WHERE status IN ('active','reported')").fetchone()[0]) > 0

    def _mission_ready(self, mission_id: int) -> bool:
        subtasks = [t for t in self.tasks.list_mission_tasks(mission_id, include_main=False) if 'boss_subtask' in (t.get('tags') or [])]
        return bool(subtasks) and all(t.get('status') == 'done' for t in subtasks)

    async def _llm_dispatch_plan(self, mission: dict[str, Any], profiles: list[NPCProfile]) -> list[dict[str, Any]]:
        if self.llm.status().get('active_mode') != 'real_llm':
            return []
        payload = {
            'mission': mission,
            'agents': [p.model_dump() for p in profiles],
            'contract': '请由公司 Agent 团队自行分配主任务，输出 JSON：{"subtasks":[{"title":"...","description":"...","owner_npc_id":"...","role_hint":"...","priority":1-5,"tags":[...] }]}。只使用已有 owner_npc_id。不要安排现实经济类操作。',
        }
        result = await self.llm.chat([
            {'role': 'system', 'content': '你是公司任务拆分会议的记录员。请只输出 JSON，不要 Markdown。任务必须可执行、可验收、可由 Agent 自主判断工具调用。'},
            {'role': 'user', 'content': _json(payload)[:8000]},
        ], temperature=0.28, max_tokens=900, response_format={'type': 'json_object'}, call_purpose='boss_mission_dispatch')
        try:
            obj = self._parse_json(result.text)
            items = obj.get('subtasks') if isinstance(obj, dict) else []
            if isinstance(items, list):
                valid_ids = {p.npc_id for p in profiles}
                return [x for x in items if isinstance(x, dict) and (x.get('owner_npc_id') in valid_ids or x.get('role_hint'))]
        except Exception:
            return []
        return []

    def _fallback_dispatch_plan(self, mission: dict[str, Any], profiles: list[NPCProfile]) -> list[dict[str, Any]]:
        text = f"{mission.get('title','')} {mission.get('description','')} {mission.get('desired_outcome','')}".lower()
        plan: list[dict[str, Any]] = []
        def add(role_keys: list[str], title: str, desc: str, tags: list[str], priority: int = 4) -> None:
            owner = self._find_owner(profiles, prefer=role_keys) or (profiles[0].npc_id if profiles else '')
            if owner and owner not in [p.get('owner_npc_id') for p in plan]:
                plan.append({'title': title, 'description': desc, 'owner_npc_id': owner, 'priority': priority, 'tags': tags})
        add(['CTO', '技术负责人'], f'任务拆解与技术路线｜{mission["title"]}', '组织主任务拆解，明确架构边界、依赖、验收标准和回滚点。', ['architecture', 'planning', 'report'], 5)
        add(['产品', '增长'], f'需求澄清与验收指标｜{mission["title"]}', '把老板主任务转为用户价值、范围边界、里程碑和验收指标。', ['product', 'metrics', 'acceptance'], 5)
        if any(k in text for k in ['ai', 'agent', 'rag', '模型', '检索', '知识库', '算法', '智能体', 'llm']):
            add(['算法'], f'智能能力方案与评估｜{mission["title"]}', '判断是否需要 RAG、记忆、工具调用或评估集，并产出可验证方案。', ['agent', 'rag', 'eval', 'prompt'], 4)
        if any(k in text for k in ['前端', '界面', '游戏', '显示', '运动', '可视化', 'ui', 'canvas', '交互']):
            add(['前端', '交互'], f'交互与可视化实现｜{mission["title"]}', '实现或规划前端交互、地图显示、状态反馈和游戏体验闭环。', ['frontend', 'demo', 'ui'], 4)
        if any(k in text for k in ['代码', '运行', '后端', '接口', 'api', '测试', '部署', '工具', 'tool']):
            add(['LLMOps', '后端', '平台'], f'后端工具链与运行验证｜{mission["title"]}', '设计接口、代码工件、运行验证、日志和可观测性。', ['backend', 'llmops', 'tool', 'run'], 4)
        add(['安全'], f'安全边界与退回机制｜{mission["title"]}', '审查工具调用、代码运行、动态 Agent、回滚事务和禁止真实交易边界。', ['security', 'audit', 'rollback'], 4)
        add(['HR', '招聘'], f'组织协作与最终报告汇编｜{mission["title"]}', '跟踪各 Agent 产出，整理协作过程、风险、结论和最终任务报告素材。', ['hr', 'report', 'coordination'], 3)
        return plan

    async def _llm_report(self, mission: dict[str, Any], tasks: list[dict[str, Any]], profiles: list[NPCProfile], note: str = '') -> str:
        if self.llm.status().get('active_mode') != 'real_llm':
            return ''
        payload = {'mission': mission, 'tasks': tasks, 'agents': [{'npc_id': p.npc_id, 'name': p.name, 'role': p.role} for p in profiles], 'note': note}
        result = await self.llm.chat([
            {'role': 'system', 'content': '你是公司 COO，负责把老板主任务的执行过程汇总成中文完整任务报告。报告必须包含：任务背景、拆分与分工、执行过程、使用过的工具/资源、产物、风险与回滚、完成情况、下一步建议。不要编造现实经济类操作。'},
            {'role': 'user', 'content': _json(payload)[:9000]},
        ], temperature=0.22, max_tokens=1600, call_purpose='boss_mission_report')
        return result.text.strip() if not result.degraded else ''

    def _fallback_report(self, mission: dict[str, Any], tasks: list[dict[str, Any]], profiles: list[NPCProfile], note: str = '') -> str:
        owner_name = {p.npc_id: p.name for p in profiles}
        lines = [
            f'# 完整任务报告｜{mission["title"]}',
            '',
            '## 1. 任务背景',
            mission.get('description') or '老板发布了一个主任务，要求公司 Agent 团队自行拆分、执行并形成报告。',
            '',
            '## 2. 期望结果',
            mission.get('desired_outcome') or '形成可执行、可验收、可复用的公司内部产出。',
            '',
            '## 3. 任务拆分与分工',
        ]
        for t in tasks:
            tags = ', '.join(t.get('tags') or [])
            lines.append(f'- {t["title"]}｜负责人：{owner_name.get(t.get("owner_npc_id"), t.get("owner_npc_id") or "待分配")}｜状态：{t.get("status")}｜标签：{tags}')
        done = sum(1 for t in tasks if t.get('status') == 'done')
        lines.extend([
            '',
            '## 4. 执行过程',
            f'团队围绕主任务共创建 {len(tasks)} 个相关任务，其中 {done} 个已完成。每个 Agent 在执行时独立判断是否需要检索知识库、查阅共享资源、写文档、写/运行安全代码或创建动态工具；系统只负责权限、安全和审计。',
            '',
            '## 5. 工具与共享资源',
            '执行产物会沉淀到公司内部共享资源中心、RAG 知识库和公共记忆池，后续 Agent 可按需检索复用。',
            '',
            '## 6. 风险与退回机制',
            '本轮禁止现实经济类操作以及外部不可控操作。涉及招聘、扩张和动态工具的动作均保留事务或资源记录，必要时可通过公司事务接口回滚。',
            '',
            '## 7. 完成情况',
            '已完成' if done == len(tasks) else '阶段性完成：仍有子任务未完全 done。',
            '',
            '## 8. 下一步建议',
            note or '建议老板查看共享资源、知识库和工具审计记录，再决定是否发布下一轮主任务。',
        ])
        return '\n'.join(lines)

    def _find_owner(self, profiles: list[NPCProfile], prefer: list[str]) -> str:
        for key in prefer:
            for p in profiles:
                hay = f'{p.role} {p.title} {p.department} {p.name}'
                if key.lower() in hay.lower() or key in hay:
                    return p.npc_id
        return ''

    def _owner_by_role_hint(self, profiles: list[NPCProfile], hint: str) -> str:
        if not hint:
            return ''
        return self._find_owner(profiles, [hint])

    def _parse_json(self, text: str) -> dict[str, Any]:
        clean = (text or '').strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
        try:
            obj = json.loads(clean)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            m = re.search(r'\{.*\}', clean, flags=re.S)
            if not m:
                return {}
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else {}

    def _row_to_mission(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        try:
            item['dispatch'] = json.loads(item.get('dispatch_json') or '[]')
        except Exception:
            item['dispatch'] = []
        item['tasks'] = self.tasks.list_mission_tasks(int(item['id']), include_main=True) if item.get('id') else []
        item['ready_for_report'] = self._mission_ready(int(item['id'])) if item.get('id') else False
        return item
