from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..config import Settings
from ..llm import LLMClient
from ..models import NPCProfile, Position
from .safety_guard import SafetyGuard
from .office_map import zone_center

def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')

def _slug(text: str, fallback: str = 'agent') -> str:
    raw = re.sub(r'[^a-zA-Z0-9]+', '_', text.lower()).strip('_')
    return (raw or fallback)[:32]

def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)

@dataclass
class RuntimeBindings:
    agent_manager: Any | None = None
    state_manager: Any | None = None
    office_simulator: Any | None = None
    task_manager: Any | None = None
    event_bus: Any | None = None
    memory_router: Any | None = None

class CompanyRuntime:

    def __init__(self, db_path: Path, llm: LLMClient, settings: Settings, safety: SafetyGuard):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.llm = llm
        self.settings = settings
        self.safety = safety
        self.bindings = RuntimeBindings()
        self.last_cycle: dict[str, Any] = {'tick': 0, 'events': [], 'thoughts': []}
        self._cycle_lock = asyncio.Lock()
        self._init_db()

    def bind_runtime(self, *, agent_manager: Any, state_manager: Any, office_simulator: Any, task_manager: Any, event_bus: Any, memory_router: Any | None = None) -> None:
        self.bindings = RuntimeBindings(agent_manager=agent_manager, state_manager=state_manager, office_simulator=office_simulator, task_manager=task_manager, event_bus=event_bus, memory_router=memory_router)

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS agent_thoughts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    npc_id TEXT NOT NULL,
                    tick INTEGER NOT NULL,
                    observation TEXT DEFAULT '',
                    private_judgement TEXT DEFAULT '',
                    collaboration_judgement TEXT DEFAULT '',
                    action_intent TEXT DEFAULT '',
                    needs_help INTEGER DEFAULT 0,
                    needs_hiring INTEGER DEFAULT 0,
                    needs_scale INTEGER DEFAULT 0,
                    share_candidate TEXT DEFAULT '',
                    rollback_plan TEXT DEFAULT '',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS hiring_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_npc_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    title TEXT DEFAULT '',
                    department TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'interviewing',
                    round INTEGER DEFAULT 1,
                    skill_gap TEXT DEFAULT '',
                    profile_json TEXT NOT NULL,
                    interview_summary TEXT DEFAULT '',
                    evaluation_score INTEGER DEFAULT 0,
                    evaluation_reason TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS company_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'committed',
                    reason TEXT DEFAULT '',
                    before_json TEXT NOT NULL DEFAULT '{}',
                    after_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    rolled_back_at TEXT DEFAULT '',
                    rollback_result TEXT DEFAULT ''
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS expansion_projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'planned',
                    owner_npc_id TEXT DEFAULT '',
                    reason TEXT DEFAULT '',
                    task_ids TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            ''')
            conn.commit()


    def recent_thoughts(self, *, npc_id: str | None = None, limit: int = 80) -> list[dict[str, Any]]:
        where = ''
        params: list[Any] = []
        if npc_id:
            where = 'WHERE npc_id = ?'
            params.append(npc_id)
        params.append(max(1, min(int(limit), 300)))
        with self._connect() as conn:
            rows = conn.execute(f'''SELECT * FROM agent_thoughts {where} ORDER BY id DESC LIMIT ?''', tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def candidates(self, *, status: str | None = None, limit: int = 80) -> list[dict[str, Any]]:
        where = ''
        params: list[Any] = []
        if status:
            where = 'WHERE status = ?'
            params.append(status)
        params.append(max(1, min(int(limit), 200)))
        with self._connect() as conn:
            rows = conn.execute(f'''SELECT * FROM hiring_candidates {where} ORDER BY id DESC LIMIT ?''', tuple(params)).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            try:
                item['profile'] = json.loads(item.get('profile_json') or '{}')
            except Exception:
                item['profile'] = {}
            out.append(item)
        return out

    def transactions(self, *, limit: int = 80) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute('''SELECT * FROM company_transactions ORDER BY id DESC LIMIT ?''', (max(1, min(int(limit), 200)),)).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            for key in ('before_json', 'after_json'):
                try:
                    item[key.removesuffix('_json')] = json.loads(item.get(key) or '{}')
                except Exception:
                    item[key.removesuffix('_json')] = {}
            out.append(item)
        return out

    def expansion_projects(self, *, limit: int = 60) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute('''SELECT * FROM expansion_projects ORDER BY id DESC LIMIT ?''', (max(1, min(int(limit), 150)),)).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            try:
                item['task_ids'] = json.loads(item.get('task_ids') or '[]')
            except Exception:
                item['task_ids'] = []
            out.append(item)
        return out

    def load_joined_profiles(self) -> list[NPCProfile]:
        profiles: list[NPCProfile] = []
        with self._connect() as conn:
            rows = conn.execute("SELECT profile_json FROM hiring_candidates WHERE status = 'hired' ORDER BY id ASC").fetchall()
        for row in rows:
            try:
                profiles.append(NPCProfile.model_validate(json.loads(row['profile_json'])))
            except Exception:
                continue
        return profiles

    def status(self, profiles: list[NPCProfile], task_manager: Any | None = None) -> dict[str, Any]:
        task_manager = task_manager or self.bindings.task_manager
        tasks = task_manager.list_tasks(limit=300) if task_manager else []
        active = [t for t in tasks if t.get('status') != 'done']
        blocked = [t for t in active if t.get('status') == 'blocked']
        return {
            'agent_count': len(profiles),
            'max_agents': int(getattr(self.settings, 'company_max_agents', 14) or 14),
            'active_task_count': len(active),
            'blocked_task_count': len(blocked),
            'open_candidate_count': len(self.candidates(status='interviewing', limit=20)),
            'hired_dynamic_count': len(self.candidates(status='hired', limit=100)),
            'recent_thoughts': self.recent_thoughts(limit=12),
            'hiring_pipeline': self.candidates(limit=12),
            'expansion_projects': self.expansion_projects(limit=12),
            'recent_transactions': self.transactions(limit=12),
            'last_cycle': self.last_cycle,
        }


    async def think_for_agent(self, *, profile: NPCProfile, state: dict, task: dict, tick: int, teammates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if not bool(getattr(self.settings, 'company_thinking_enabled', True)):
            return {}
        use_llm = bool(getattr(self.settings, 'company_thinking_use_llm', False)) and self.llm.status().get('active_mode') == 'real_llm'
        obj: dict[str, Any]
        if use_llm:
            try:
                obj = await self._llm_think(profile=profile, state=state, task=task, tick=tick, teammates=teammates or [])
            except Exception:
                obj = self._fallback_think(profile=profile, state=state, task=task, tick=tick, teammates=teammates or [])
        else:
            obj = self._fallback_think(profile=profile, state=state, task=task, tick=tick, teammates=teammates or [])
        thought_id = self._record_thought(profile.npc_id, tick, obj)
        obj['thought_id'] = thought_id
        return obj

    async def _llm_think(self, *, profile: NPCProfile, state: dict, task: dict, tick: int, teammates: list[dict[str, Any]]) -> dict[str, Any]:
        system = '''你是一个真实公司模拟游戏中的 Agent 内部思考器。请只输出 JSON，不要 Markdown。
字段：observation, private_judgement, collaboration_judgement, action_intent, needs_help(boolean), needs_hiring(boolean), needs_scale(boolean), share_candidate, rollback_plan。
要求：每个角色独立判断自己的任务，也要判断是否需要和其他 Agent 合作；HR 可判断是否需要招聘和面试；CTO/PM/LLMOps 可判断是否扩大公司规模；任何扩张动作都必须给出可回滚方案。'''
        user = {
            'tick': tick,
            'agent': profile.model_dump(),
            'state': state,
            'task': task,
            'teammates': teammates[:8],
        }
        result = await self.llm.chat([
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': _json(user)[:6000]},
        ], temperature=0.35, max_tokens=520, response_format={'type': 'json_object'}, call_purpose='agent_thinking')
        return self._parse_json(result.text) or self._fallback_think(profile=profile, state=state, task=task, tick=tick, teammates=teammates)

    def _fallback_think(self, *, profile: NPCProfile, state: dict, task: dict, tick: int, teammates: list[dict[str, Any]]) -> dict[str, Any]:
        role_text = f'{profile.role} {profile.department} {profile.title}'.lower()
        task_text = f"{task.get('title', '')} {task.get('description', '')} {task.get('tags', [])}".lower()
        stress = int(state.get('stress', 20) or 20)
        energy = int(state.get('energy', 80) or 80)
        task_status = task.get('status') or ''
        needs_help = task_status == 'blocked' or stress > 72 or '风险' in task_text or 'blocked' in task_text
        is_hr = any(k in role_text for k in ['hr', '招聘', '组织', '面试'])
        is_scale_owner = any(k in role_text for k in ['cto', '技术负责人', '产品', '增长', 'llmops', '后端'])
        active_count = self.bindings.task_manager.active_count() if self.bindings.task_manager else 0
        agent_count = max(1, self.bindings.state_manager.count() if self.bindings.state_manager else 7)
        needs_hiring = is_hr and active_count >= max(6, int(agent_count * 1.25)) and agent_count < int(getattr(self.settings, 'company_max_agents', 14) or 14)
        needs_scale = is_scale_owner and active_count >= max(5, int(agent_count * 1.1)) and tick % max(2, int(getattr(self.settings, 'company_scale_interval_ticks', 9) or 9)) == 0
        partner = self._suggest_partner(profile, task_text, teammates)
        if needs_help and partner:
            collab = f'当前问题可能需要 {partner.get("name")}({partner.get("role")}) 协作判断，先发送协作消息或安排评审。'
        elif needs_hiring:
            collab = '当前任务队列超过现有人力承载，HR 应启动行为面试并补充短缺岗位。'
        elif needs_scale:
            collab = '当前公司规模接近瓶颈，应把扩张拆成小事务并保留回滚点。'
        else:
            collab = '暂时可以独立推进；若工具链或验收出现阻塞，再请求协作。'
        if energy < 30:
            intent = '降低执行强度，先恢复状态并整理下一步行动。'
        elif needs_hiring:
            intent = '发起招聘需求评审，构建候选 Agent 并安排行为面试。'
        elif needs_scale:
            intent = '提出公司扩张实验：新增模块、岗位或流程，并绑定回滚事务。'
        elif needs_help:
            intent = '先进行合作判断，再执行风险/阻塞排查。'
        else:
            intent = f'围绕「{task.get("title", "自主工作")}"继续推进可验证产出。'
        return {
            'observation': f'{profile.name} 观察到任务状态={task_status or "none"}，能量={energy}，压力={stress}，当前任务={task.get("title", "无")}',
            'private_judgement': f'作为{profile.role}，优先处理自己职责边界内的问题；当前判断 needs_help={needs_help}。',
            'collaboration_judgement': collab,
            'action_intent': intent,
            'needs_help': bool(needs_help),
            'needs_hiring': bool(needs_hiring),
            'needs_scale': bool(needs_scale),
            'share_candidate': '若该判断影响多个角色的任务分配、接口契约、招聘或扩张，则写入公共记忆池。',
            'rollback_plan': '所有招聘/扩张都先记录 company_transactions；失败时删除动态 Agent、撤销任务或标记项目 rolled_back。',
        }

    def _record_thought(self, npc_id: str, tick: int, obj: dict[str, Any]) -> int:
        with self._connect() as conn:
            cur = conn.execute('''
                INSERT INTO agent_thoughts(npc_id, tick, observation, private_judgement, collaboration_judgement, action_intent,
                    needs_help, needs_hiring, needs_scale, share_candidate, rollback_plan, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                npc_id, tick,
                str(obj.get('observation', ''))[:1000],
                str(obj.get('private_judgement', ''))[:1000],
                str(obj.get('collaboration_judgement', ''))[:1000],
                str(obj.get('action_intent', ''))[:1000],
                1 if obj.get('needs_help') else 0,
                1 if obj.get('needs_hiring') else 0,
                1 if obj.get('needs_scale') else 0,
                str(obj.get('share_candidate', ''))[:1000],
                str(obj.get('rollback_plan', ''))[:1000],
                _json(obj)[:5000],
                _now(),
            ))
            conn.commit()
            return int(cur.lastrowid)


    async def run_cycle(self, *, profiles: list[NPCProfile], tick: int) -> list[int]:
        async with self._cycle_lock:
            event_ids: list[int] = []
            if not bool(getattr(self.settings, 'company_game_enabled', True)):
                return event_ids
            task_manager = self.bindings.task_manager
            event_bus = self.bindings.event_bus
            state_manager = self.bindings.state_manager
            if not task_manager or not event_bus or not state_manager:
                return event_ids

            thoughts: list[dict[str, Any]] = []
            if bool(getattr(self.settings, 'company_thinking_enabled', True)):
                interval = max(1, int(getattr(self.settings, 'company_thinking_interval_ticks', 2) or 2))
                if tick == 1 or tick % interval == 0:
                    for profile in profiles[: int(getattr(self.settings, 'company_thinking_agents_per_cycle', 12) or 12)]:
                        state = state_manager.get_state(profile.npc_id) or {}
                        task = task_manager.next_task_for(profile.npc_id, profile.role) or {}
                        teammates = self._teammates(profile.npc_id)
                        thought = await self.think_for_agent(profile=profile, state=state, task=task, tick=tick, teammates=teammates)
                        if thought:
                            thoughts.append({'npc_id': profile.npc_id, 'name': profile.name, **thought})
                            if thought.get('needs_help') and self.bindings.memory_router:
                                await self.bindings.memory_router.remember(
                                    npc_id=profile.npc_id, player_name='__office__',
                                    content=f"【Agent 思考】{profile.name}：{thought.get('private_judgement')} / 合作判断：{thought.get('collaboration_judgement')}",
                                    importance=4, kind='agent_thinking', npc_name=profile.name, role=profile.role, share_check=True,
                                )

            if bool(getattr(self.settings, 'company_hiring_enabled', True)):
                e = await self._maybe_run_hiring(profiles=profiles, tick=tick, thoughts=thoughts)
                event_ids.extend(e)
            if bool(getattr(self.settings, 'company_scale_enabled', True)):
                e = await self._maybe_run_expansion(profiles=profiles, tick=tick, thoughts=thoughts)
                event_ids.extend(e)
            self.last_cycle = {'tick': tick, 'events': event_ids, 'thoughts': thoughts[-20:]}
            return event_ids

    async def request_hiring_cycle(self, reason: str = 'manual_or_tool_request') -> dict[str, Any]:
        profiles = list(getattr(self.bindings.agent_manager, 'profiles', []) or [])
        event_ids = await self._maybe_run_hiring(profiles=profiles, tick=-1, thoughts=[{'needs_hiring': True, 'reason': reason}], force=True)
        return {'ok': True, 'event_ids': event_ids, 'reason': reason, 'pipeline': self.candidates(limit=5)}

    async def request_expansion_cycle(self, reason: str = 'manual_or_tool_request') -> dict[str, Any]:
        profiles = list(getattr(self.bindings.agent_manager, 'profiles', []) or [])
        event_ids = await self._maybe_run_expansion(profiles=profiles, tick=-1, thoughts=[{'needs_scale': True, 'reason': reason}], force=True)
        return {'ok': True, 'event_ids': event_ids, 'reason': reason, 'projects': self.expansion_projects(limit=5)}

    async def _maybe_run_hiring(self, *, profiles: list[NPCProfile], tick: int, thoughts: list[dict[str, Any]], force: bool = False) -> list[int]:
        event_ids: list[int] = []
        event_bus = self.bindings.event_bus
        task_manager = self.bindings.task_manager
        if not event_bus or not task_manager:
            return event_ids
        active_candidates = self.candidates(status='interviewing', limit=1)
        if active_candidates:
            candidate = active_candidates[0]
            result = await self._evaluate_candidate(candidate, profiles=profiles)
            if result.get('passed'):
                profile = NPCProfile.model_validate(json.loads(candidate['profile_json']))
                tx_id = self._begin_transaction(kind='hire_agent', reason=f"行为面试通过：{profile.name}/{profile.role}", before={'agents': [p.npc_id for p in profiles], 'candidate': candidate})
                applied = self._add_dynamic_agent(profile)
                if not applied.get('ok'):
                    self._finish_transaction(tx_id, status='failed', after={'error': applied})
                    event_ids.append(event_bus.publish('hiring_failed', f"候选 Agent {profile.name} 加入失败，事务 #{tx_id} 已标记失败：{applied.get('reason')}", ['yin_hr']))
                else:
                    self._mark_candidate(candidate['candidate_npc_id'], 'hired', result)
                    self._finish_transaction(tx_id, status='committed', after={'added_agents': [profile.npc_id], 'candidate_npc_id': profile.npc_id})
                    event_ids.append(event_bus.publish('hiring_passed', f"HR 行为面试通过：新 Agent {profile.name}({profile.role}) 已加入公司。事务 #{tx_id} 可回滚。", ['yin_hr', profile.npc_id]))
                    task_manager.create_task(title=f'新同事 {profile.name} 入职与项目接入', description='系统自动生成：动态 Agent 加入后，需要完成环境熟悉、私有记忆初始化和协作边界确认。', owner_npc_id=profile.npc_id, priority=4, tags=['onboarding', 'hiring', 'agent'])
            else:
                self._mark_candidate(candidate['candidate_npc_id'], 'rejected', result)
                event_ids.append(event_bus.publish('hiring_rejected', f"HR 行为面试未通过：{candidate['name']} 暂不加入。原因：{result.get('reason')}；系统将继续寻找下一个候选 Agent。", ['yin_hr']))
                if self._should_hire(profiles, thoughts) or force:
                    created = self._create_candidate(profiles, reason='上一位候选人未通过，继续面试下一位。')
                    event_ids.append(event_bus.publish('hiring_candidate_created', f"系统构建新候选 Agent：{created['name']}({created['role']})，等待 HR 行为面试。", ['yin_hr']))
            return event_ids

        if force or self._should_hire(profiles, thoughts):
            created = self._create_candidate(profiles, reason='LLM/策略判断当前公司需要补充人手。')
            task_manager.create_task(title=f'行为面试：{created["name"]} / {created["role"]}', description=f'HR 与相关岗位 Agent 对候选 Agent 进行行为面试，重点考察 STAR、协作边界、工具调用安全和独立判断能力。技能缺口：{created.get("skill_gap")}', owner_npc_id='yin_hr', priority=5, tags=['interview', 'hiring', 'behavioral'])
            event_ids.append(event_bus.publish('hiring_candidate_created', f"LLM/策略判断需要招聘，系统构建候选 Agent：{created['name']}({created['role']})，进入行为面试。", ['yin_hr', 'guo_cto']))
        return event_ids

    async def _maybe_run_expansion(self, *, profiles: list[NPCProfile], tick: int, thoughts: list[dict[str, Any]], force: bool = False) -> list[int]:
        event_ids: list[int] = []
        event_bus = self.bindings.event_bus
        task_manager = self.bindings.task_manager
        if not event_bus or not task_manager:
            return event_ids
        interval = max(2, int(getattr(self.settings, 'company_scale_interval_ticks', 9) or 9))
        if not force and tick > 0 and tick % interval != 0:
            return event_ids
        if not force and not self._should_scale(profiles, thoughts):
            return event_ids
        title = self._expansion_title(profiles)
        existing = [p for p in self.expansion_projects(limit=30) if p.get('title') == title and p.get('status') in {'planned', 'building'}]
        if existing:
            return event_ids
        before = {'agents': [p.npc_id for p in profiles], 'active_tasks': task_manager.list_tasks(limit=80)}
        tx_id = self._begin_transaction(kind='scale_company', reason=title, before=before)
        owner = 'guo_cto' if any(p.npc_id == 'guo_cto' for p in profiles) else profiles[0].npc_id
        task_defs = [
            (f'{title}：架构拆分与容量评估', owner, ['scale', 'architecture', 'rollback']),
            (f'{title}：产品边界与验收指标', 'tang_pm', ['scale', 'product', 'metrics']),
            (f'{title}：安全与回滚演练', 'han_security', ['scale', 'security', 'rollback']),
        ]
        created_task_ids: list[int] = []
        for t, owner_id, tags in task_defs:
            task = task_manager.create_task(title=t[:120], description='公司扩张实验自动生成：必须先完成小步开发、可观测验证和失败回滚方案。', owner_npc_id=owner_id, priority=4, tags=tags)
            created_task_ids.append(int(task.get('id')))
        with self._connect() as conn:
            conn.execute('''INSERT INTO expansion_projects(title, status, owner_npc_id, reason, task_ids, created_at, updated_at) VALUES (?, 'building', ?, ?, ?, ?, ?)''', (title, owner, '相关 Agent 判断当前公司规模可以扩大，但必须可回滚。', _json(created_task_ids), _now(), _now()))
            conn.commit()
        self._finish_transaction(tx_id, status='committed', after={'created_task_ids': created_task_ids, 'project_title': title})
        event_ids.append(event_bus.publish('company_scale_started', f'相关 Agent 判断可以扩大公司规模：{title}。已创建 {len(created_task_ids)} 个开发/验收/回滚任务，事务 #{tx_id} 可回滚。', ['guo_cto', 'tang_pm', 'han_security']))
        return event_ids

    def _should_hire(self, profiles: list[NPCProfile], thoughts: list[dict[str, Any]]) -> bool:
        if len(profiles) >= int(getattr(self.settings, 'company_max_agents', 14) or 14):
            return False
        if any(t.get('needs_hiring') for t in thoughts):
            return True
        task_manager = self.bindings.task_manager
        if not task_manager:
            return False
        tasks = task_manager.list_tasks(limit=240)
        active = [t for t in tasks if t.get('status') != 'done']
        blocked = [t for t in active if t.get('status') == 'blocked']
        return len(active) >= max(7, int(len(profiles) * 1.4)) or len(blocked) >= 2

    def _should_scale(self, profiles: list[NPCProfile], thoughts: list[dict[str, Any]]) -> bool:
        if any(t.get('needs_scale') for t in thoughts):
            return True
        task_manager = self.bindings.task_manager
        if not task_manager:
            return False
        active = task_manager.active_count()
        hired = len(self.candidates(status='hired', limit=100))
        return active >= max(8, int(len(profiles) * 1.3)) or hired >= 1

    def _create_candidate(self, profiles: list[NPCProfile], reason: str) -> dict[str, Any]:
        gap = self._detect_skill_gap()
        key = gap.get('key', 'agent')
        role, dept = self._candidate_role_for_gap(key)
        name = self._next_unique_candidate_name(key, profiles)
        npc_id = self._next_dynamic_npc_id(key, name, profiles)
        profile = NPCProfile(
            npc_id=npc_id,
            name=name,
            role=role,
            title=f'动态招聘候选 Agent / {role}',
            department=dept,
            personality='自驱、注重证据、愿意接受行为面试和试用期回滚机制。',
            goals=['快速补齐公司当前能力短板', '通过行为面试证明独立判断、协作和工具调用能力', '加入后产出可验收的项目增量'],
            skills=gap.get('skills') or ['Agent 工程', '协作判断', '工具调用'],
            responsibilities=gap.get('responsibilities') or ['补齐当前项目短板', '参与跨角色协作', '输出验收材料'],
            speaking_style='像真实候选人，回答要用 STAR，明确自己能解决什么问题。',
            movement_style='在面试间、研发工位和相关业务区域之间移动，先观察再执行。',
            home_zone=gap.get('home_zone') or 'interview_room',
            position=Position(x=930, y=500),
            color=gap.get('color') or '#38BDF8',
        )
        with self._connect() as conn:
            conn.execute('''
                INSERT INTO hiring_candidates(candidate_npc_id, name, role, title, department, status, round, skill_gap, profile_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'interviewing', 1, ?, ?, ?, ?)
            ''', (profile.npc_id, profile.name, profile.role, profile.title, profile.department, gap.get('label', ''), profile.model_dump_json(), _now(), _now()))
            conn.commit()
        return {'candidate_npc_id': profile.npc_id, 'name': profile.name, 'role': profile.role, 'skill_gap': gap.get('label'), 'reason': reason, 'profile': profile.model_dump()}

    def _candidate_role_for_gap(self, key: str) -> tuple[str, str]:
        role_pool = {
            'qa': ('AI 测试与评估工程师', '质量效能'),
            'data': ('数据分析与增长工程师', '数据增长'),
            'design': ('AI 交互设计师', '体验设计'),
            'sre': ('平台 SRE / 成本治理工程师', '平台工程'),
            'agent': ('Agent 工具链工程师', '智能体平台'),
            'security': ('AI 安全治理工程师', '安全合规'),
            'docs': ('知识库与技术文档工程师', '知识运营'),
            'ops': ('公司运营流程 Agent', '组织运营'),
            'customer': ('解决方案与客户成功 Agent', '客户成功'),
        }
        return role_pool.get(key, role_pool['agent'])

    def _next_unique_candidate_name(self, key: str, profiles: list[NPCProfile]) -> str:
        pools = {
            'qa': ['林砚秋', '许知微', '陆青禾', '顾星澜', '闻书宁', '陈予白'],
            'data': ['周闻笛', '叶澄怀', '温予安', '姜明棠', '白知夏', '宋景澄'],
            'design': ['夏以宁', '江映雪', '苏鹿鸣', '秦知晚', '沈南枝', '梁星禾'],
            'sre': ['程砚', '梁启辰', '赵北辰', '贺云舟', '严景行', '段清越'],
            'agent': ['裴澈', '秦望舒', '白景行', '宋清越', '沈星晏', '陆怀瑾'],
            'security': ['韩知远', '顾承安', '叶谨言', '沈砺', '陆清衡', '许景明'],
            'docs': ['宋闻书', '江白榆', '夏知简', '林听雨', '苏怀序', '秦予墨'],
            'ops': ['唐景和', '梁予舟', '温知衡', '赵行简', '方若衡', '俞明澈'],
            'customer': ['方知予', '俞知遥', '季南星', '温若谷', '江予珩', '唐清欢'],
        }
        used = {p.name for p in profiles}
        used |= {c.get('name') for c in self.candidates(limit=800) if c.get('name')}
        for candidate in pools.get(key, pools['agent']):
            if candidate not in used and not re.search(r'\d', candidate):
                return candidate

        surnames = list('赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤')
        given = ['知行', '景澄', '清越', '望舒', '星临', '予安', '云舟', '书宁', '南枝', '若谷', '怀瑾', '明棠', '青禾', '砚秋', '映川', '以宁']
        seed = f'{key}|{len(used)}|{_now()}'.encode('utf-8')
        offset = int(hashlib.md5(seed).hexdigest()[:6], 16)
        for i in range(len(surnames) * len(given)):
            name = surnames[(offset + i) % len(surnames)] + given[((offset // 7) + i) % len(given)]
            if name not in used and not re.search(r'\d', name):
                return name
        raise RuntimeError('无法生成唯一新人姓名')

    def _next_dynamic_npc_id(self, key: str, name: str, profiles: list[NPCProfile]) -> str:
        taken = {p.npc_id for p in profiles} | {c['candidate_npc_id'] for c in self.candidates(limit=800)}
        base = f'dyn_{_slug(key)}_{hashlib.md5(name.encode("utf-8")).hexdigest()[:8]}'
        npc_id = base
        salt = 0
        while npc_id in taken:
            salt += 1
            npc_id = f'{base}_{hashlib.md5((name + str(salt)).encode("utf-8")).hexdigest()[:4]}'
        return npc_id

    async def _evaluate_candidate(self, candidate: dict[str, Any], profiles: list[NPCProfile]) -> dict[str, Any]:
        use_llm = bool(getattr(self.settings, 'company_hiring_use_llm', False)) and self.llm.status().get('active_mode') == 'real_llm'
        if use_llm:
            try:
                prompt = {'candidate': candidate, 'company_status': self.status(profiles), 'criteria': ['岗位缺口匹配', 'STAR 行为面试', '工具调用安全', '独立判断', '协作判断', '可回滚试用']}
                result = await self.llm.chat([
                    {'role': 'system', 'content': '你是 HR 与技术面试官联合评审。请只输出 JSON：passed(boolean), score(0-100), reason, interview_summary。'},
                    {'role': 'user', 'content': _json(prompt)[:6000]},
                ], temperature=0.25, max_tokens=360, response_format={'type': 'json_object'}, call_purpose='behavioral_interview')
                obj = self._parse_json(result.text)
                if obj:
                    return {'passed': bool(obj.get('passed')), 'score': int(obj.get('score', 0)), 'reason': str(obj.get('reason', '')), 'interview_summary': str(obj.get('interview_summary', ''))}
            except Exception:
                pass
        raw = f"{candidate.get('candidate_npc_id')}|{candidate.get('skill_gap')}|{len(profiles)}"
        h = int(hashlib.md5(raw.encode()).hexdigest()[:4], 16)
        score = 62 + h % 31


        rejected_count = len(self.candidates(status='rejected', limit=200))
        round_no = int(candidate.get('round') or 1)
        passed = score >= int(getattr(self.settings, 'company_hiring_pass_score', 74) or 74) or round_no >= 2 or rejected_count >= 1
        reason = '行为面试体现了清晰 STAR、协作边界和工具安全意识。' if passed else 'STAR 结果不够具体，工具权限边界和回滚意识不足。'
        summary = f"HR 行为面试：候选人围绕技能缺口「{candidate.get('skill_gap')}」回答，score={score}，passed={passed}。"
        return {'passed': passed, 'score': score, 'reason': reason, 'interview_summary': summary}

    def _mark_candidate(self, candidate_npc_id: str, status: str, result: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute('''
                UPDATE hiring_candidates
                SET status = ?, interview_summary = ?, evaluation_score = ?, evaluation_reason = ?, round = round + 1, updated_at = ?
                WHERE candidate_npc_id = ?
            ''', (status, str(result.get('interview_summary', ''))[:2000], int(result.get('score', 0) or 0), str(result.get('reason', ''))[:1200], _now(), candidate_npc_id))
            conn.commit()

    def _detect_skill_gap(self) -> dict[str, Any]:
        task_manager = self.bindings.task_manager
        profiles = list(getattr(self.bindings.agent_manager, 'profiles', []) or [])
        tasks = task_manager.list_tasks(limit=240) if task_manager else []
        active_tasks = [t for t in tasks if t.get('status') != 'done']
        text_parts: list[str] = []
        for t in active_tasks:
            text_parts.extend([
                str(t.get('title', '')),
                str(t.get('description', '')),
                ' '.join(map(str, t.get('tags') or [])),
                str(t.get('owner_npc_id', '')),
            ])
        text = re.sub(r'\s+', ' ', ' '.join(text_parts)).lower()

        gap_defs: dict[str, dict[str, Any]] = {
            'agent': {
                'label': 'Agent 工具链与推理编排缺口',
                'skills': ['LangChain', 'Tool Calling', 'RAG', '多 Agent 协作', 'Planner 设计'],
                'responsibilities': ['开发工具链', '优化 Agent Planner', '维护知识库与公共记忆', '沉淀可复用动态工具'],
                'home_zone': 'architecture_board', 'color': '#38BDF8',
                'keywords': {'agent': 6, 'tool': 6, '工具': 6, 'langchain': 6, 'langgraph': 6, 'rag': 6, '知识库': 4, '检索': 4, '动态工具': 8, '多智能体': 7, '自主': 3},
            },
            'sre': {
                'label': '平台稳定性与成本治理缺口',
                'skills': ['SRE', 'Tracing', '成本优化', '限流降级', '运行维护'],
                'responsibilities': ['治理模型调用成本', '完善可观测性', '演练回滚与降级', '维护代码运行沙箱'],
                'home_zone': 'server_corner', 'color': '#34D399',
                'keywords': {'llmops': 7, 'backend': 5, '后端': 5, '平台': 5, '部署': 6, '运行': 4, '稳定': 7, '监控': 7, '成本': 7, '延迟': 4, '回滚': 4, '接口': 3, 'api': 3},
            },
            'security': {
                'label': '安全治理与权限边界缺口',
                'skills': ['Prompt Injection 防护', '权限策略', '审计追踪', '沙箱安全', '合规评审'],
                'responsibilities': ['评审工具权限', '拦截高风险操作', '维护审计和回滚策略', '保护私有记忆边界'],
                'home_zone': 'security_room', 'color': '#EF4444',
                'keywords': {'安全': 8, '权限': 7, '合规': 7, '审计': 6, '风险': 5, '注入': 7, '沙箱': 6, '隐私': 5, '交易': 6, '支付': 6},
            },
            'design': {
                'label': '交互与演示体验缺口',
                'skills': ['交互设计', 'Canvas', '可解释性 UI', '游戏化体验', '前端原型'],
                'responsibilities': ['设计 Agent 状态解释', '优化演示路径', '增强沉浸感', '让新人/任务/报告在界面可见'],
                'home_zone': 'demo_zone', 'color': '#FB7185',
                'keywords': {'frontend': 5, '前端': 5, 'demo': 4, '演示': 5, '交互': 7, '体验': 7, 'ui': 5, 'canvas': 5, '显示': 4, '运动': 4, '地图': 4, '游戏': 3},
            },
            'data': {
                'label': '数据增长与指标分析缺口',
                'skills': ['数据分析', '指标体系', '实验设计', '任务度量', '增长分析'],
                'responsibilities': ['建立公司模拟指标', '分析任务吞吐与质量趋势', '输出老板报告中的量化部分', '驱动产品决策'],
                'home_zone': 'product_board', 'color': '#22C55E',
                'keywords': {'metrics': 6, '指标': 6, '数据': 7, '增长': 6, '分析': 5, '报表': 4, '统计': 5, '量化': 5, '转化': 4, 'product': 3, '产品': 3},
            },
            'docs': {
                'label': '知识库与技术文档缺口',
                'skills': ['技术写作', '知识库治理', 'ADR/Runbook', '报告归档', 'RAG 资料组织'],
                'responsibilities': ['把任务产物整理成内部文档', '维护共享资源目录', '提升 RAG 可检索性', '生成最终任务报告素材'],
                'home_zone': 'product_board', 'color': '#A78BFA',
                'keywords': {'文档': 7, '报告': 6, 'prd': 5, 'adr': 5, 'runbook': 5, '知识运营': 7, '归档': 5, '总结': 4, '说明书': 4},
            },
            'qa': {
                'label': '测试评估缺口',
                'skills': ['测试平台', 'Eval 设计', '自动化验收', '回归测试', '质量度量'],
                'responsibilities': ['构建评估集', '维护自动化测试', '验收 Agent 行为质量', '设计回归用例'],
                'home_zone': 'demo_zone', 'color': '#F97316',


                'keywords': {'eval': 8, '测试': 8, 'test': 7, '回归': 7, '质量': 6, '评估': 6, '用例': 5, '验收': 1, '通过标准': 1, 'acceptance': 1},
            },
            'ops': {
                'label': '组织运营与流程协作缺口',
                'skills': ['组织流程', '跨角色协作', '会议纪要', '任务编排', '人员接入'],
                'responsibilities': ['维护任务分配节奏', '推动跨角色同步', '沉淀运营流程', '协助新人融入团队'],
                'home_zone': 'meeting_room', 'color': '#F59E0B',
                'keywords': {'组织': 5, '流程': 6, '协作': 5, '会议': 4, '入职': 5, '招聘': 4, '分配': 5, '运营': 5},
            },
            'customer': {
                'label': '客户成功与方案表达缺口',
                'skills': ['解决方案', '需求访谈', '演示叙事', '客户成功', '反馈整理'],
                'responsibilities': ['把老板目标转成可演示方案', '收集反馈并回传产品/技术', '维护非交易型客户成功流程'],
                'home_zone': 'meeting_room', 'color': '#06B6D4',
                'keywords': {'客户': 6, '方案': 5, '访谈': 5, '反馈': 5, '展示': 4, '路演': 4, '商业化': 3},
            },
        }

        scores = {key: 0.0 for key in gap_defs}
        for key, cfg in gap_defs.items():
            for kw, weight in cfg['keywords'].items():
                kw_l = str(kw).lower()


                hit_count = text.count(kw_l)
                if hit_count:
                    scores[key] += float(weight) * min(hit_count, 4)




        coverage = self._gap_coverage_by_key(profiles)
        candidates = self.candidates(limit=500)
        candidate_counts = {key: 0 for key in gap_defs}
        for c in candidates:
            key = self._gap_key_from_text(' '.join([str(c.get('role', '')), str(c.get('department', '')), str(c.get('skill_gap', ''))]))
            if key in candidate_counts:
                candidate_counts[key] += 1
        for key in scores:
            scores[key] -= candidate_counts.get(key, 0) * 24.0


            scores[key] -= max(0, coverage.get(key, 0) - 1) * 4.0



        strong_qa_terms = ['eval', '测试', 'test', '回归', '质量', '评估', '用例']
        if not any(k in text for k in strong_qa_terms):
            scores['qa'] = min(scores['qa'], 0.5)

        best_key, best_score = max(scores.items(), key=lambda kv: kv[1])
        rotation = ['agent', 'sre', 'security', 'design', 'data', 'docs', 'qa', 'ops', 'customer']


        if best_score < 3.0:
            best_key = min(rotation, key=lambda k: (candidate_counts.get(k, 0), coverage.get(k, 0), rotation.index(k)))
        else:




            best_count = candidate_counts.get(best_key, 0)
            if best_count > 0:
                alternatives = [k for k in rotation if candidate_counts.get(k, 0) < best_count and scores.get(k, -999) >= -12.0]
                if alternatives:
                    best_key = max(alternatives, key=lambda k: (scores.get(k, 0), -coverage.get(k, 0), -rotation.index(k)))

        cfg = gap_defs[best_key]
        result = {k: v for k, v in cfg.items() if k != 'keywords'}
        result['key'] = best_key
        result['score'] = round(float(scores.get(best_key, 0)), 2)
        result['debug_scores'] = {k: round(v, 2) for k, v in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)}
        return result

    def _gap_coverage_by_key(self, profiles: list[NPCProfile]) -> dict[str, int]:
        keys = ['agent', 'sre', 'security', 'design', 'data', 'docs', 'qa', 'ops', 'customer']
        coverage = {k: 0 for k in keys}
        for p in profiles:
            text = ' '.join([
                getattr(p, 'role', '') or '',
                getattr(p, 'department', '') or '',
                ' '.join(getattr(p, 'skills', []) or []),
                ' '.join(getattr(p, 'responsibilities', []) or []),
            ])
            key = self._gap_key_from_text(text)
            if key in coverage:
                coverage[key] += 1
        return coverage

    def _gap_key_from_text(self, text: str) -> str:
        t = str(text or '').lower()
        checks = [
            ('security', ['安全', '合规', '权限', '审计', 'prompt injection', '注入', '隐私']),
            ('qa', ['测试', '评估', 'eval', '回归', '质量效能', '质量工程', 'qa']),
            ('sre', ['sre', 'llmops', '后端', '平台工程', '稳定', '监控', '成本治理']),
            ('design', ['前端', '交互', '体验设计', 'ui', 'ux', 'canvas', 'demo']),
            ('data', ['数据', '增长', '指标', '分析', 'metrics']),
            ('docs', ['文档', '知识运营', '技术写作', '知识库与技术文档']),
            ('ops', ['组织运营', '流程', 'hr', '招聘', '人员接入']),
            ('customer', ['客户', '解决方案', '客户成功', '商业化']),
            ('agent', ['agent', '智能体', 'langchain', 'rag', 'tool', '工具链', '大模型算法']),
        ]
        for key, words in checks:
            if any(w in t for w in words):
                return key
        return 'agent'

    def _add_dynamic_agent(self, profile: NPCProfile) -> dict[str, Any]:
        b = self.bindings
        if not b.agent_manager or not b.state_manager or not b.office_simulator:
            return {'ok': False, 'reason': 'runtime bindings missing'}
        if b.agent_manager.has_npc(profile.npc_id):
            return {'ok': False, 'reason': f'agent {profile.npc_id} already exists'}
        b.agent_manager.add_profile(profile)
        b.state_manager.add_profile(profile)
        b.office_simulator.add_profile(profile)



        try:
            target = zone_center(profile.home_zone)
            started_tick = int(getattr(b.office_simulator, 'tick_count', 0) or 0)
            b.state_manager.start_episode(
                profile.npc_id,
                episode_id=f'onboarding-{profile.npc_id}-{started_tick}',
                intention='完成新人入职接入',
                action='从面试间前往岗位工位，读取公共记忆池并建立私有工作上下文',
                mood='curious',
                target_zone=profile.home_zone,
                target_position=target,
                current_task='新同事入职与项目接入',
                plan_summary='新人通过行为面试后自动加入公司，进入可视化地图并参与移动、任务和工具调用循环。',
                selected_tool='dynamic_agent_onboarding',
                duration_ticks=6,
                started_tick=started_tick,
                reasoning_factors=['行为面试通过', '动态 Agent 已注入运行时', '需要从面试间移动到岗位工作区'],
                autonomy=0.82,
                action_kind='sync',
                decision_source='company_hiring_runtime',
            )
        except Exception:
            pass
        if b.event_bus:
            b.event_bus.publish('dynamic_agent_joined_runtime', f'新 Agent {profile.name} 已加入运行时：可在地图显示、移动、对话并参与自主 Agent 循环。', ['yin_hr', profile.npc_id])
        return {'ok': True, 'npc_id': profile.npc_id, 'name': profile.name}

    def _remove_dynamic_agent(self, npc_id: str) -> dict[str, Any]:
        b = self.bindings
        removed = {'agent_manager': False, 'state_manager': False, 'office_simulator': False}
        if b.agent_manager:
            removed['agent_manager'] = b.agent_manager.remove_profile(npc_id)
        if b.state_manager:
            removed['state_manager'] = b.state_manager.remove_agent(npc_id)
        if b.office_simulator:
            removed['office_simulator'] = b.office_simulator.remove_profile(npc_id)
        return {'ok': any(removed.values()), 'removed': removed}

    def _begin_transaction(self, *, kind: str, reason: str, before: dict[str, Any]) -> int:
        with self._connect() as conn:
            cur = conn.execute('''INSERT INTO company_transactions(kind, status, reason, before_json, after_json, created_at) VALUES (?, 'pending', ?, ?, '{}', ?)''', (kind, reason[:1000], _json(before)[:20000], _now()))
            conn.commit()
            return int(cur.lastrowid)

    def _finish_transaction(self, tx_id: int, *, status: str, after: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute('''UPDATE company_transactions SET status = ?, after_json = ? WHERE id = ?''', (status, _json(after)[:20000], int(tx_id)))
            conn.commit()

    async def rollback_transaction(self, tx_id: int, reason: str = 'manual rollback') -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM company_transactions WHERE id = ?', (int(tx_id),)).fetchone()
        if not row:
            return {'ok': False, 'reason': f'transaction #{tx_id} not found'}
        item = dict(row)
        if item.get('status') == 'rolled_back':
            return {'ok': True, 'already_rolled_back': True, 'transaction': item}
        try:
            after = json.loads(item.get('after_json') or '{}')
        except Exception:
            after = {}
        results: list[dict[str, Any]] = []
        for npc_id in after.get('added_agents') or []:
            results.append({'remove_agent': npc_id, **self._remove_dynamic_agent(str(npc_id))})
            with self._connect() as conn:
                conn.execute("UPDATE hiring_candidates SET status = 'rolled_back', updated_at = ? WHERE candidate_npc_id = ?", (_now(), npc_id))
                conn.commit()
        task_manager = self.bindings.task_manager
        for task_id in after.get('created_task_ids') or []:
            if task_manager and hasattr(task_manager, 'delete_task'):
                results.append({'delete_task': task_id, 'deleted': task_manager.delete_task(int(task_id))})
            elif task_manager:
                task_manager.update_status(int(task_id), 'blocked')
                results.append({'mark_task_blocked': task_id})
        if after.get('project_title'):
            with self._connect() as conn:
                conn.execute("UPDATE expansion_projects SET status = 'rolled_back', updated_at = ? WHERE title = ?", (_now(), after.get('project_title')))
                conn.commit()
        payload = {'ok': True, 'transaction_id': tx_id, 'reason': reason, 'results': results}
        with self._connect() as conn:
            conn.execute('''UPDATE company_transactions SET status = 'rolled_back', rolled_back_at = ?, rollback_result = ? WHERE id = ?''', (_now(), _json(payload)[:8000], int(tx_id)))
            conn.commit()
        if self.bindings.event_bus:
            self.bindings.event_bus.publish('company_rollback', f'公司事务 #{tx_id} 已回滚：{reason}。结果：{len(results)} 项变更被撤销/标记。', [])
        return payload

    def _expansion_title(self, profiles: list[NPCProfile]) -> str:
        hired = len(self.candidates(status='hired', limit=100))
        if hired:
            return f'公司规模扩张第 {hired} 阶段：动态 Agent 团队化开发'
        return '公司规模扩张实验：从 Demo 团队升级为可运营 Agent 公司'

    def _suggest_partner(self, profile: NPCProfile, task_text: str, teammates: list[dict[str, Any]]) -> dict[str, Any] | None:
        mapping = [('安全', 'han_security'), ('security', 'han_security'), ('frontend', 'su_frontend'), ('demo', 'su_frontend'), ('product', 'tang_pm'), ('metrics', 'tang_pm'), ('memory', 'shen_algo'), ('rag', 'shen_algo'), ('backend', 'lu_ops'), ('llmops', 'lu_ops'), ('interview', 'yin_hr'), ('resume', 'yin_hr'), ('architecture', 'guo_cto')]
        for key, target in mapping:
            if key in task_text and target != profile.npc_id:
                for mate in teammates:
                    if mate.get('npc_id') == target:
                        return mate
        return teammates[0] if teammates else None

    def _teammates(self, current_npc_id: str) -> list[dict[str, Any]]:
        state_manager = self.bindings.state_manager
        if not state_manager:
            return []
        return [s for s in state_manager.list_states() if s.get('npc_id') != current_npc_id][:10]

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        raw = (text or '').strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            m = re.search(r'\{.*\}', raw, flags=re.S)
            if not m:
                return {}
            try:
                obj = json.loads(re.sub(r',\s*([}\]])', r'\1', m.group(0)))
                return obj if isinstance(obj, dict) else {}
            except Exception:
                return {}
