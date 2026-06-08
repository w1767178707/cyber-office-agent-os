from __future__ import annotations
import asyncio
import hashlib
import json
import random
import re
import time
from dataclasses import dataclass, field
from math import cos, pi, sin
from typing import Any
from ..config import Settings
from ..llm import LLMClient
from ..models import AgentDecision, NPCProfile, Position
from .event_bus import EventBus
from .memory_store import MemoryStore
from .memory_compactor import MemoryCompactor
from .office_map import ZONES, zone_center, zone_name
from .state_manager import StateManager
from .task_manager import TaskManager
from .tool_registry import ToolRegistry, ToolExecutionContext
from .memory_router import MemoryRouter

@dataclass
class EpisodeDecision:
    intention: str
    tool: str
    action: str
    mood: str
    target_zone: str
    thought: str
    goal: str
    duration_ticks: int
    confidence: float = 0.8
    reasoning_factors: list[str] = field(default_factory=list)
    source: str = 'local_generative'
    action_kind: str = 'work'
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    reflection: str = ''
    tool_plan: list[dict[str, Any]] = field(default_factory=list)
    difficulty: dict[str, Any] = field(default_factory=dict)
    share_memory_candidate: dict[str, Any] = field(default_factory=dict)
    thinking_trace: dict[str, Any] = field(default_factory=dict)
    collaboration_judgement: str = ''
    rollback_plan: str = ''
    company_actions: list[dict[str, Any]] = field(default_factory=list)

class OfficeAgentSimulator:

    def __init__(self, profiles: list[NPCProfile], states: StateManager, events: EventBus, memory: MemoryStore, tasks: TaskManager, llm: LLMClient, settings: Settings, compactor: MemoryCompactor | None=None, tools: ToolRegistry | None=None, memory_router: MemoryRouter | None=None, company_runtime: Any | None=None, boss_mission_service: Any | None=None):
        self.profiles = list(profiles)
        self.profile_map = {p.npc_id: p for p in self.profiles}
        self.states = states
        self.events = events
        self.memory = memory
        self.tasks = tasks
        self.llm = llm
        self.settings = settings
        self.compactor = compactor
        self.tools = tools
        self.memory_router = memory_router
        self.company_runtime = company_runtime
        self.boss_mission_service = boss_mission_service
        self._tick_lock = asyncio.Lock()
        self.tick_count = 0
        self._memory_keys_to_compact: set[tuple[str, str]] = set()
        self.last_traces: list[dict] = []
        self._last_llm_decision_tick: dict[str, int] = {}
        self._dynamic_task_counter = 0
        self._llm_decisions_used_this_tick = 0
        self._force_llm_this_tick = False
        self._last_decision_errors: list[dict[str, Any]] = []
        self._last_parallel_batch: dict[str, Any] = {'tick': 0, 'mode': 'idle', 'requested_agents': 0, 'llm_scheduled': 0, 'llm_completed': 0, 'fallback_count': 0, 'latency_ms': 0, 'concurrency': 0, 'sources': [], 'errors': []}

    def reset_runtime(self) -> None:
        self.tick_count = 0
        self.last_traces = []
        self._last_llm_decision_tick = {}
        self._dynamic_task_counter = 0
        self._llm_decisions_used_this_tick = 0
        self._force_llm_this_tick = False
        self._last_decision_errors = []
        self._last_parallel_batch = {'tick': 0, 'mode': 'reset', 'requested_agents': 0, 'llm_scheduled': 0, 'llm_completed': 0, 'fallback_count': 0, 'latency_ms': 0, 'concurrency': int(getattr(self.settings, 'office_llm_parallel_concurrency', 1) or 1), 'sources': [], 'errors': []}

    def add_profile(self, profile: NPCProfile) -> bool:
        if profile.npc_id in self.profile_map:
            return False
        self.profiles.append(profile)
        self.profile_map[profile.npc_id] = profile
        return True

    def remove_profile(self, npc_id: str) -> bool:
        if npc_id not in self.profile_map:
            return False
        self.profile_map.pop(npc_id, None)
        self.profiles = [p for p in self.profiles if p.npc_id != npc_id]
        self._last_llm_decision_tick.pop(npc_id, None)
        return True

    async def tick(self, use_llm_planner: bool | None=None) -> dict:
        async with self._tick_lock:
            return await self._tick_unlocked(use_llm_planner=use_llm_planner)

    async def _tick_unlocked(self, use_llm_planner: bool | None=None) -> dict:
        self.tick_count += 1
        self._llm_decisions_used_this_tick = 0
        self._force_llm_this_tick = bool(use_llm_planner)
        self._memory_keys_to_compact = set()
        self.states.decay_player_influences()
        traces: list[dict] = []
        created_event_ids: list[int] = []
        pending_decisions: list[tuple[NPCProfile, dict, dict]] = []
        if self.tick_count == 1:
            created_event_ids.append(self.events.publish('office_boot', 'CyberOffice 已进入老板任务模式：等待老板发布主任务；空闲 Agent 会围绕主任务自主分工、判断工具调用并执行。', []))
        if not bool(getattr(self.settings, 'boss_task_mode_enabled', True)):
            self._maybe_inject_dynamic_task()
        ritual_event = self._maybe_create_ritual_event()
        if ritual_event:
            created_event_ids.append(ritual_event)
        if self.company_runtime:
            try:
                boss_mode = bool(getattr(self.settings, 'boss_task_mode_enabled', True))
                has_boss_mission = bool(self.boss_mission_service and self.boss_mission_service.has_active_or_recent_mission())
                if (not boss_mode) or has_boss_mission:
                    company_events = await self.company_runtime.run_cycle(profiles=self.profiles, tick=self.tick_count)
                    created_event_ids.extend(company_events or [])
            except Exception as exc:
                created_event_ids.append(self.events.publish('company_runtime_error', f'公司智能运行时执行失败，已保持当前状态并等待下一轮：{exc.__class__.__name__}: {exc}', []))
        for profile in self.profiles:
            state = self.states.get_state(profile.npc_id) or {}
            if state.get('is_busy'):
                traces.append(self._trace_from_state(profile, state, '玩家对话中，暂停自主行动。'))
                continue
            phase = state.get('action_phase')
            if phase == 'walking' and state.get('episode_id'):
                reached = self.states.move_towards_target(profile.npc_id, self.settings.office_move_step)
                state = self.states.get_state(profile.npc_id) or state
                if reached:
                    self.states.begin_acting(profile.npc_id)
                    state = self.states.get_state(profile.npc_id) or state
                    self._remember_agent_step(profile, state, 'agent_step_arrive', f"到达{state.get('location')}，准备开始执行：{state.get('intention')}", importance=1)
                else:
                    self._remember_agent_step(profile, state, 'agent_step_walk', f"正在前往{zone_name(state.get('target_zone') or profile.home_zone)}：{state.get('intention')}，当前位置={state.get('location')}", importance=1)
                traces.append(self._trace_from_state(profile, state, '继续执行已生成的行动片段；移动不等待模型。'))
                continue
            if phase == 'acting' and state.get('episode_id'):
                done = self.states.advance_action_progress(profile.npc_id)
                state = self.states.get_state(profile.npc_id) or state
                self._remember_agent_step(profile, state, 'agent_step_act', f"执行中：{state.get('intention')}；进度={state.get('action_progress')}；剩余={state.get('action_remaining_ticks')} ticks。", importance=1)
                if done:
                    event_id = self._complete_episode(profile, state)
                    if event_id:
                        created_event_ids.append(event_id)
                    self.states.clear_episode_for_next_decision(profile.npc_id)
                    state = self.states.get_state(profile.npc_id) or state
                traces.append(self._trace_from_state(profile, state, '行动耗时推进；未完成前不会重新请求模型。'))
                continue
            if phase == 'cooldown' and state.get('episode_id'):
                self.states.clear_episode_for_next_decision(profile.npc_id)
                state = self.states.get_state(profile.npc_id) or state
            task = self._choose_task(profile, state)
            pending_decisions.append((profile, state, task))
        if pending_decisions:
            batch_started = time.perf_counter()
            resolved = await self._parallel_synthesize_decisions(pending_decisions, use_llm_planner)
            self._last_parallel_batch['latency_ms'] = int((time.perf_counter() - batch_started) * 1000)
            for profile, state, task, decision in resolved:
                tool_traces = await self._run_agent_tool_flow(profile, state, task, decision)
                if tool_traces:
                    decision.tool_trace = tool_traces
                    first_tool = next((t for t in tool_traces if t.get('tool_name')), None)
                    if first_tool:
                        decision.tool = str(first_tool.get('tool_name'))
                    decision.observations = [self._clean_text(str(t.get('summary') or t.get('reason') or t.get('error') or t.get('tool_name') or '工具已执行'), 140) for t in tool_traces[:5]]
                    failed = [t for t in tool_traces if not t.get('ok')]
                    decision.reflection = self._reflect_after_tools(profile, decision, tool_traces)
                    if failed:
                        decision.reasoning_factors = [f"工具链存在失败/阻断：{failed[0].get('tool_name', decision.tool)} -> {failed[0].get('error', failed[0].get('reason', 'unknown'))}"] + decision.reasoning_factors[:4]
                target = self._jittered_target(decision.target_zone, profile.npc_id)
                episode_id = self._episode_id(profile.npc_id, decision, task)
                self.states.start_episode(profile.npc_id, episode_id=episode_id, intention=decision.intention, action=decision.action, mood=decision.mood, target_zone=decision.target_zone, target_position=target, current_task=task.get('title', decision.intention) if task else decision.intention, plan_summary=decision.thought, selected_tool=decision.tool, duration_ticks=decision.duration_ticks, started_tick=self.tick_count, reasoning_factors=decision.reasoning_factors, autonomy=decision.confidence, action_kind=decision.action_kind, decision_source=decision.source)
                reached = self.states.move_towards_target(profile.npc_id, self.settings.office_move_step)
                if reached:
                    self.states.begin_acting(profile.npc_id)
                next_state = self.states.get_state(profile.npc_id) or state
                self._remember_agent_step(profile, next_state, 'agent_episode_start', f'生成新行动片段：{decision.intention}；目标={zone_name(decision.target_zone)}；工具={decision.tool}；来源={decision.source}。', importance=2 if decision.source.startswith('deepseek') else 1)
                traces.append(self._trace_from_decision(profile, decision, target, task, next_state))
        else:
            self._last_parallel_batch = {'tick': self.tick_count, 'mode': 'no_new_decisions', 'requested_agents': 0, 'llm_scheduled': 0, 'llm_completed': 0, 'fallback_count': 0, 'latency_ms': 0, 'concurrency': int(getattr(self.settings, 'office_llm_parallel_concurrency', 1) or 1), 'sources': [], 'errors': []}
        should_call_llm = bool(use_llm_planner) if use_llm_planner is not None else self.settings.office_use_llm_planner
        if should_call_llm and self.tick_count % max(1, self.settings.office_llm_planner_interval * 2) == 0:
            llm_event = await self._llm_coordinator_event(traces)
            if llm_event:
                created_event_ids.append(llm_event)
        await self._maybe_finalize_boss_reports(created_event_ids)
        await self._compact_memory_streams()
        self.last_traces = traces
        events = self.events.recent(limit=max(10, len(created_event_ids) + 6))
        return {'tick': self.tick_count, 'events': events, 'npc_states': self.states.list_states(), 'agent_traces': traces, 'tasks': self.tasks.list_tasks(limit=24), 'npc_profiles': [p.model_dump() for p in self.profiles]}

    async def _run_agent_tool_flow(self, profile: NPCProfile, state: dict, task: dict, decision: EpisodeDecision) -> list[dict[str, Any]]:
        if not self.tools:
            return []
        ctx = ToolExecutionContext(npc_id=profile.npc_id, npc_name=profile.name, role=profile.role, task=task or {}, tick=self.tick_count)
        plan = self._build_tool_plan(profile, state, task, decision)


        max_steps = max(8, int(getattr(self.settings, 'agent_tool_loop_max_steps', 12) or 12))
        traces = await self.tools.execute_plan(plan, ctx=ctx, max_steps=max_steps)
        for trace in traces:
            compact = self._compact_tool_result(trace)
            await self._remember_tool_result(profile, trace, compact)
        return [self._compact_tool_result(t) for t in traces]

    def _build_tool_plan(self, profile: NPCProfile, state: dict, task: dict, decision: EpisodeDecision) -> list[dict[str, Any]]:
        plan: list[dict[str, Any]] = []
        for step in decision.tool_plan or []:
            if isinstance(step, dict):
                name = step.get('tool') or step.get('name') or step.get('tool_name')
                args = dict(step.get('args') or {})
                if step.get('query') and 'query' not in args:
                    args['query'] = step.get('query')
                if name and str(name).lower() not in {'none', 'no_tool', '无', '不调用工具'}:
                    resolved = self.tools.resolve_name(str(name), role=profile.role, task=task) if self.tools else str(name)
                    plan.append({'tool': resolved, 'args': self._fill_tool_args(resolved, args, profile, state, task, decision)})
        selected = str(decision.tool or '').strip()
        if selected and selected.lower() not in {'none', 'no_tool', 'deepseek_episode_planner', 'intent_synthesizer', '无', '不调用工具'}:
            resolved = self.tools.resolve_name(selected, role=profile.role, task=task) if self.tools else selected
            if resolved and resolved not in {p['tool'] for p in plan}:
                plan.append({'tool': resolved, 'args': self._tool_args_for_decision(resolved, profile, state, task, decision)})
        if decision.share_memory_candidate.get('share') is True:
            plan.append({'tool': 'share_memory', 'args': {'content': f'{profile.name} 判断需要共享：{decision.share_memory_candidate.get("reason") or decision.thought}', 'importance': 4, 'kind': 'agent_shared_judgement'}})
        return self._dedupe_plan(plan)

    def _fill_tool_args(self, tool_name: str, args: dict[str, Any], profile: NPCProfile, state: dict, task: dict, decision: EpisodeDecision) -> dict[str, Any]:
        args = dict(args or {})
        query = self._decision_query(profile, state, task, decision) or profile.role
        if tool_name in {'search_memory', 'search_public_memory', 'search_knowledge', 'search_internal_resources', 'browser_search_and_ingest'}:
            args.setdefault('query', query)
        if tool_name == 'search_memory':
            args.setdefault('player_name', '__office__')
            args.setdefault('limit', 5)
        if tool_name in {'search_public_memory', 'search_knowledge', 'search_internal_resources'}:
            args.setdefault('limit', 5)
        if tool_name == 'risk_check':
            args.setdefault('text', query)
        if tool_name == 'create_company_document':
            args.setdefault('title', f'{profile.name}产出｜{task.get("title") or decision.intention}')
            args.setdefault('topic', task.get('title') or decision.intention)
            args.setdefault('context', task.get('description') or decision.thought)
        if tool_name == 'create_operating_checklist':
            args.setdefault('title', f'{profile.name}检查清单｜{task.get("title") or decision.intention}')
            args.setdefault('topic', task.get('title') or decision.intention)
            args.setdefault('context', task.get('description') or decision.thought)
        if tool_name == 'write_code_artifact':
            args.setdefault('title', f'{profile.name}代码工件｜{task.get("title") or decision.intention}')
            args.setdefault('purpose', task.get('description') or decision.goal or decision.intention)
        if tool_name == 'run_code_artifact':
            args.setdefault('timeout_seconds', float(getattr(self.settings, 'safe_code_timeout_seconds', 3.0) or 3.0))
        if tool_name == 'create_dynamic_tool':
            safe_name = re.sub(r'[^a-zA-Z0-9_]+', '_', f'{profile.npc_id}_{task.get("id", self.tick_count)}_tool').strip('_').lower()
            args.setdefault('name', safe_name[:60])
            args.setdefault('description', f'{profile.name} 为「{task.get("title") or decision.intention}」创建的声明式动态工具')
            args.setdefault('tool_kind', 'checklist')
        if tool_name == 'run_dynamic_tool':
            args.setdefault('inputs', {'topic': task.get('title') or decision.intention, 'context': task.get('description') or decision.thought})
        if tool_name == 'send_agent_message':
            args.setdefault('content', f'{profile.name} 请求协作：{decision.collaboration_judgement or decision.thought}')
            args.setdefault('message_type', 'boss_task_collaboration')
            args.setdefault('related_task_id', task.get('id'))
        if tool_name == 'update_task_status':
            args.setdefault('task_id', task.get('id'))
            args.setdefault('status', 'doing')
        return args

    def _dedupe_plan(self, plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for step in plan:
            name = str(step.get('tool') or step.get('name') or '')
            args = step.get('args') or {}
            key = f'{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)[:220]}'

            if key in seen and name != 'search_knowledge':
                continue
            seen.add(key)
            out.append({'tool': name, 'args': args})
        return out

    def _decision_query(self, profile: NPCProfile, state: dict, task: dict, decision: EpisodeDecision) -> str:
        return ' '.join(str(x or '') for x in [profile.role, decision.intention, decision.action, decision.goal, decision.thought, task.get('title'), task.get('description'), ','.join(task.get('tags') or [])]).strip()[:900]

    def _should_browse_for_difficulty(self, profile: NPCProfile, state: dict, task: dict, decision: EpisodeDecision) -> bool:
        if not bool(getattr(self.settings, 'agent_difficulty_auto_browse', True)):
            return False
        text = f"{decision.difficulty} {decision.reasoning_factors} {decision.intention} {decision.action} {decision.thought} {task}".lower()
        markers = ['困难', '缺少', '不确定', '未知', '阻塞', '需要资料', '需要文档', '查找', '搜索', 'browser', 'browse', 'web', '资料不足', '引用不足', 'unknown', 'blocked']
        if any(m in text for m in markers):
            return True
        if task.get('status') == 'blocked':
            return True
        if decision.confidence < 0.68 and decision.action_kind != 'recover':
            return True

        return bool(task) and self.tick_count % 17 == (len(profile.npc_id) % 17)

    def _stable_slot(self, npc_id: str, modulo: int) -> int:
        modulo = max(1, int(modulo or 1))
        try:
            value = int(hashlib.md5(str(npc_id).encode('utf-8')).hexdigest()[:8], 16)
        except Exception:
            value = len(str(npc_id))
        return value % modulo

    def _autonomous_resource_plan(self, profile: NPCProfile, state: dict, task: dict, decision: EpisodeDecision) -> list[dict[str, Any]]:
        if not bool(getattr(self.settings, 'agent_autonomous_resource_enabled', True)):
            return []
        query = self._decision_query(profile, state, task, decision)
        task_title = task.get('title') or decision.intention or f'{profile.role} 自主运营改进'
        task_desc = task.get('description') or decision.thought or decision.goal or ''
        role_text = f'{profile.role} {profile.department} {" ".join(profile.skills or [])}'
        tick = max(1, int(self.tick_count or 1))
        out: list[dict[str, Any]] = []

        def due(interval_name: str, default: int, *, bootstrap: bool = False) -> bool:
            interval = max(1, int(getattr(self.settings, interval_name, default) or default))
            if bootstrap and tick <= 2:
                return True
            return tick % interval == self._stable_slot(profile.npc_id, interval)



        if due('agent_autonomous_doc_interval_ticks', 3, bootstrap=True):
            if '产品' in role_text:
                doc_type = 'prd'
            elif '安全' in role_text:
                doc_type = 'security_review'
            elif 'LLMOps' in role_text or '后端' in role_text or '平台' in role_text:
                doc_type = 'runbook'
            elif '前端' in role_text:
                doc_type = 'test_plan'
            elif '招聘' in role_text or 'HR' in role_text or '人力' in role_text:
                doc_type = 'meeting_notes'
            else:
                doc_type = 'adr'
            out.append({'tool': 'create_company_document', 'args': {'title': f'{profile.name}内部沉淀｜{task_title}', 'document_type': doc_type, 'topic': task_title, 'context': f'{task_desc}\n\nAgent思考：{decision.thought}\n目标：{decision.goal}'}})


        tech_role = any(k in role_text for k in ['算法', '前端', '后端', 'LLMOps', '平台', '技术', '工程'])
        code_bootstrap = tick <= 2 and any(k in role_text for k in ['算法', 'LLMOps', '后端', '平台'])
        if tech_role and due('agent_autonomous_code_interval_ticks', 18, bootstrap=code_bootstrap):
            out.append({'tool': 'write_code_artifact', 'args': {'title': f'{profile.name}内部代码工件｜{task_title}', 'purpose': f'{profile.role} 用于推进 {task_title} 的公司模拟指标/校验脚本'}})
            out.append({'tool': 'run_code_artifact', 'args': {'timeout_seconds': float(getattr(self.settings, 'safe_code_timeout_seconds', 3.0) or 3.0)}})



        dynamic_role = any(k in role_text for k in ['算法', '前端', '后端', 'LLMOps', '平台', '技术', '产品', '安全'])
        if bool(getattr(self.settings, 'dynamic_tools_enabled', True)) and dynamic_role and due('agent_autonomous_dynamic_tool_interval_ticks', 24, bootstrap=tick <= 2):
            tool_name = f'agent_{profile.npc_id}_ops_checklist'
            out.append({'tool': 'create_dynamic_tool', 'args': {'name': tool_name, 'description': f'{profile.name} 为 {task_title} 创建的可复用运营检查清单工具', 'tool_kind': 'checklist', 'spec': {'template': '# 自治工具检查清单｜{topic}\n\n背景：{context}\n\n- [ ] 已检索私有记忆\n- [ ] 已检索公共记忆池\n- [ ] 已检索 RAG 知识库\n- [ ] 已查看内部共享资源\n- [ ] 已输出可复用结论\n- [ ] 已写入知识库/公共记忆\n'}}})
            out.append({'tool': 'run_dynamic_tool', 'args': {'tool_name': tool_name, 'inputs': {'topic': task_title, 'context': f'{task_desc}\n{decision.thought}'}}})



        if due('agent_autonomous_browser_interval_ticks', 21, bootstrap=(tick == 1 and any(k in role_text for k in ['算法', '产品', '安全']))):
            out.append({'tool': 'browser_search_and_ingest', 'args': {'query': query or task_title or profile.role, 'limit': 2}})
            out.append({'tool': 'search_knowledge', 'args': {'query': query or task_title or profile.role, 'limit': 5}})
        return out

    def _should_request_share(self, profile: NPCProfile, task: dict, decision: EpisodeDecision) -> bool:
        return decision.share_memory_candidate.get('share') is True

    def _compact_tool_result(self, result: dict[str, Any]) -> dict[str, Any]:
        compact = {k: v for k, v in result.items() if k not in {'memories', 'events', 'tasks', 'hits', 'results', 'ingested_docs'} }
        for key in ('memories', 'events', 'tasks', 'hits', 'results', 'ingested_docs'):
            if key in result:
                val = result[key]
                compact[key] = val[:3] if isinstance(val, list) else val
        return compact

    async def _remember_tool_result(self, profile: NPCProfile, raw: dict[str, Any], compact: dict[str, Any]) -> None:
        name = str(compact.get('tool_name') or raw.get('tool_name') or '')
        if not name:
            return




        low_signal_success = {'risk_check', 'search_memory', 'search_public_memory', 'search_internal_resources', 'search_knowledge', 'query_office_events', 'standup_summary'}
        if name in low_signal_success and compact.get('ok') and not compact.get('blocked') and not compact.get('error'):
            return
        summary = str(compact.get('summary') or compact.get('reason') or compact.get('error') or '')[:400]
        content = f'[tick={self.tick_count}][tool={name}][status={compact.get("status", "") or ("ok" if compact.get("ok") else "failed")}] {profile.name} 工具结果：{summary}\n{json.dumps(compact, ensure_ascii=False, default=str)[:1200]}'
        if self.memory_router:
            routed = await self.memory_router.remember(npc_id=profile.npc_id, player_name='__office__', content=content, importance=3 if compact.get('ok') else 4, kind='tool_result', npc_name=profile.name, role=profile.role, share_check=True)
            self._memory_keys_to_compact.add((profile.npc_id, '__office__'))
            if routed.public_memory_id:
                self._memory_keys_to_compact.add((self.memory_router.public_npc_id, self.memory_router.public_player_name))
        else:
            self.memory.add_memory(profile.npc_id, '__office__', content, importance=3 if compact.get('ok') else 4, kind='tool_result')
            self._memory_keys_to_compact.add((profile.npc_id, '__office__'))

    def _reflect_after_tools(self, profile: NPCProfile, decision: EpisodeDecision, traces: list[dict[str, Any]]) -> str:
        ok = [t for t in traces if t.get('ok')]
        blocked = [t for t in traces if t.get('blocked')]
        browser = [t for t in traces if t.get('tool_name') == 'browser_search_and_ingest']
        parts = [f'完成 {len(ok)}/{len(traces)} 个工具步骤']
        if blocked:
            parts.append(f'有 {len(blocked)} 个工具被安全策略阻断')
        if browser:
            parts.append('遇到知识缺口后已触发浏览器检索并尝试写入知识库')
        return '；'.join(parts) + f'，{profile.name} 将基于结果继续执行：{decision.intention}。'

    def _tool_args_for_decision(self, tool_name: str, profile: NPCProfile, state: dict, task: dict, decision: EpisodeDecision) -> dict[str, Any]:
        query = self._decision_query(profile, state, task, decision)
        if tool_name == 'risk_check':
            return {'text': query}
        if tool_name == 'search_memory':
            return {'query': query, 'player_name': '__office__', 'limit': 5}
        if tool_name == 'search_public_memory':
            return {'query': query, 'limit': 5}
        if tool_name == 'browser_search_and_ingest':
            return {'query': query or profile.role, 'limit': 3}
        if tool_name == 'share_memory':
            return {'content': f'{profile.name} 共享当前上下文：{decision.intention}；{decision.thought}', 'importance': 4, 'kind': 'public_candidate'}
        if tool_name == 'search_knowledge':
            return {'query': query or profile.role, 'limit': 4}
        if tool_name == 'search_internal_resources':
            return {'query': query or profile.role, 'limit': 6}
        if tool_name == 'create_company_document':
            doc_type = 'prd' if '产品' in profile.role else ('security_review' if '安全' in profile.role else ('runbook' if '后端' in profile.role or 'LLMOps' in profile.role else 'adr'))
            return {'title': f'{profile.name}沉淀｜{task.get("title", decision.goal or decision.intention)}', 'document_type': doc_type, 'topic': task.get('title') or decision.goal, 'context': task.get('description') or decision.thought}
        if tool_name == 'create_operating_checklist':
            return {'title': f'{profile.name}检查清单｜{task.get("title", decision.goal)}', 'topic': task.get('title') or decision.goal}
        if tool_name == 'write_code_artifact':
            return {'title': f'{profile.name}代码工件｜{task.get("title", decision.goal)}', 'purpose': task.get('title') or decision.goal or decision.action}
        if tool_name == 'run_code_artifact':
            return {'timeout_seconds': 3.0}
        if tool_name == 'create_dynamic_tool':
            return {'name': f'{profile.npc_id}_auto_tool_{self.tick_count}', 'description': f'{profile.name} 为 {task.get("title", decision.goal)} 创建的内部自动化工具', 'tool_kind': 'checklist', 'spec': {'template': '# 动态工具清单｜{topic}\n\n{context}\n\n- [ ] 查询知识库\n- [ ] 查询共享资源\n- [ ] 执行产物沉淀\n- [ ] 记录审计与回滚点\n'}}
        if tool_name == 'run_dynamic_tool':
            return {'inputs': {'topic': task.get('title') or decision.goal, 'context': task.get('description') or decision.thought}}
        if tool_name == 'update_task_status':
            status = 'review' if decision.action_kind == 'review' else 'doing'
            return {'task_id': task.get('id'), 'status': status}
        if tool_name == 'create_task':
            return {'title': decision.goal or decision.intention, 'description': decision.thought, 'owner_npc_id': profile.npc_id, 'priority': task.get('priority', 3), 'tags': task.get('tags') or ['agent-tool']}
        if tool_name == 'send_agent_message':
            target = self._suggest_message_target(profile, task, decision)
            return {'to_npc_id': target, 'content': f"{profile.name} 请求协作：{decision.intention}。依据：{decision.thought[:100]}", 'message_type': 'request', 'related_task_id': task.get('id')}
        if tool_name == 'query_office_events':
            return {'query': '', 'limit': 6}
        if tool_name == 'standup_summary':
            return {'limit': 8}
        if tool_name == 'write_audit_log':
            return {'content': f'{decision.intention} | {decision.thought}', 'risk_level': 'low'}
        return {'query': query}

    def _suggest_partner_from_task(self, profile: NPCProfile, task: dict) -> str:
        text = f"{task.get('title', '')} {task.get('description', '')} {task.get('tags', [])}".lower()
        candidates = [('security', 'han_security'), ('audit', 'han_security'), ('frontend', 'su_frontend'), ('demo', 'su_frontend'), ('ui', 'su_frontend'), ('product', 'tang_pm'), ('metrics', 'tang_pm'), ('memory', 'shen_algo'), ('prompt', 'shen_algo'), ('rag', 'shen_algo'), ('backend', 'lu_ops'), ('llmops', 'lu_ops'), ('tool', 'lu_ops'), ('report', 'yin_hr'), ('interview', 'yin_hr')]
        for key, npc in candidates:
            if key in text and npc != profile.npc_id and npc in self.profile_map:
                return npc
        for npc in ['guo_cto', 'tang_pm', 'shen_algo', 'han_security', 'lu_ops']:
            if npc != profile.npc_id and npc in self.profile_map:
                return npc
        return ''

    def _suggest_message_target(self, profile: NPCProfile, task: dict, decision: EpisodeDecision) -> str:
        text = f"{task.get('tags', [])} {decision.action} {decision.goal} {decision.tool}".lower()
        candidates = [('security', 'han_security'), ('audit', 'han_security'), ('frontend', 'su_frontend'), ('demo', 'su_frontend'), ('product', 'tang_pm'), ('metrics', 'tang_pm'), ('memory', 'shen_algo'), ('prompt', 'shen_algo'), ('backend', 'lu_ops'), ('llmops', 'lu_ops'), ('resume', 'yin_hr'), ('interview', 'yin_hr')]
        for key, npc in candidates:
            if key in text and npc != profile.npc_id:
                return npc
        for npc in ['guo_cto', 'tang_pm', 'shen_algo', 'han_security']:
            if npc != profile.npc_id:
                return npc
        return ''

    def _choose_task(self, profile: NPCProfile, state: dict) -> dict:
        if int(state.get('energy', 80)) < 30 or int(state.get('social_need', 40)) > 88:
            return {}
        return self.tasks.next_task_for(profile.npc_id, profile.role) or {}

    async def _parallel_synthesize_decisions(self, pending: list[tuple[NPCProfile, dict, dict]], use_llm_planner: bool | None) -> list[tuple[NPCProfile, dict, dict, EpisodeDecision]]:
        mode = (self.settings.office_decision_mode or 'llm_parallel').lower().strip()
        concurrency = max(1, int(getattr(self.settings, 'office_llm_parallel_concurrency', 8) or 8))
        semaphore = asyncio.Semaphore(concurrency)
        sources: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        self._last_decision_errors = errors
        llm_scheduled = 0
        fallback_count = 0
        should_try_llm = [self._should_use_llm_decision(profile.npc_id, use_llm_planner) for profile, _, _ in pending]
        llm_scheduled = sum((1 for v in should_try_llm if v))
        batch_id = f'tick-{self.tick_count}-batch-{hashlib.md5(str(self.tick_count).encode()).hexdigest()[:6]}'

        async def resolve_one(item: tuple[NPCProfile, dict, dict], try_llm: bool) -> tuple[NPCProfile, dict, dict, EpisodeDecision]:
            nonlocal fallback_count
            profile, state, task = item
            decision: EpisodeDecision | None = None
            if try_llm:
                async with semaphore:
                    decision = await self._call_llm_episode_with_timeout(profile, state, task)
                    if decision:
                        self._last_llm_decision_tick[profile.npc_id] = self.tick_count
            if not decision:
                fallback_count += 1
                decision = self._local_generative_decision(profile, state, task)
                if try_llm:
                    decision.source = 'llm_parallel_local_fallback'
                    latest_error = self.llm.status().get('last_error', '') or 'timeout_or_json_parse_failed'
                    decision.reasoning_factors = ['DeepSeek 并行决策失败，使用本地安全兜底', latest_error[:120]] + decision.reasoning_factors[:3]
                    errors.append({'npc_id': profile.npc_id, 'npc_name': profile.name, 'error': latest_error[:500]})
                elif mode in {'llm', 'llm_parallel', 'all_llm', 'parallel_llm'}:
                    decision.source = 'llm_parallel_not_ready_fallback'
                    latest_error = 'active_mode != real_llm 或 LLM 开关关闭；请检查 /llm/status 的 has_api_key 与 active_mode'
                    decision.reasoning_factors = [latest_error] + decision.reasoning_factors[:4]
                    errors.append({'npc_id': profile.npc_id, 'npc_name': profile.name, 'error': latest_error})
            if self.company_runtime and bool(getattr(self.settings, 'company_thinking_enabled', True)):
                try:
                    thought = await self.company_runtime.think_for_agent(profile=profile, state=state, task=task or {}, tick=self.tick_count, teammates=self._teammate_snapshot(profile.npc_id))
                    if thought:
                        decision.thinking_trace = thought
                        decision.collaboration_judgement = str(thought.get('collaboration_judgement') or '')
                        decision.rollback_plan = str(thought.get('rollback_plan') or '')
                        decision.reasoning_factors = [str(thought.get('private_judgement') or '')[:120], str(thought.get('collaboration_judgement') or '')[:120]] + decision.reasoning_factors[:4]
                        if thought.get('needs_help'):
                            partner = self._suggest_message_target(profile, task or {}, decision)
                            if partner:
                                decision.tool_plan.append({'tool': 'send_agent_message', 'args': {'to_npc_id': partner, 'content': f'{profile.name} 思考后请求合作判断：{thought.get("collaboration_judgement", "")[:180]}', 'message_type': 'collaboration_judgement', 'related_task_id': (task or {}).get('id')}})
                        if thought.get('needs_hiring') and profile.npc_id == 'yin_hr':
                            decision.tool_plan.append({'tool': 'request_hiring_cycle', 'args': {'reason': str(thought.get('action_intent') or 'HR 思考后认为需要招聘')}})
                        if thought.get('needs_scale') and profile.npc_id in {'guo_cto', 'tang_pm', 'lu_ops'}:
                            decision.tool_plan.append({'tool': 'scale_company', 'args': {'reason': str(thought.get('action_intent') or '相关 Agent 思考后认为需要扩大公司规模')}})
                except Exception as exc:
                    decision.reasoning_factors = [f'Agent 思考模块异常，已保留原决策：{exc.__class__.__name__}'] + decision.reasoning_factors[:4]
            sources.append({'npc_id': profile.npc_id, 'npc_name': profile.name, 'source': decision.source})
            return (profile, state, task, decision)
        results = await asyncio.gather(*(resolve_one(item, flag) for item, flag in zip(pending, should_try_llm)))
        llm_completed = sum((1 for *_, decision in results if str(decision.source).startswith('deepseek')))
        self._llm_decisions_used_this_tick = llm_scheduled
        self._last_parallel_batch = {'tick': self.tick_count, 'batch_id': batch_id, 'mode': mode, 'requested_agents': len(pending), 'llm_scheduled': llm_scheduled, 'llm_completed': llm_completed, 'fallback_count': fallback_count, 'latency_ms': 0, 'concurrency': concurrency, 'timeout_seconds': float(getattr(self.settings, 'office_llm_decision_timeout_seconds', 9.0) or 9.0), 'sources': sources, 'errors': errors[-12:]}
        return results

    async def _call_llm_episode_with_timeout(self, profile: NPCProfile, state: dict, task: dict) -> EpisodeDecision | None:
        timeout = max(1.0, float(getattr(self.settings, 'office_llm_decision_timeout_seconds', 9.0) or 9.0))
        try:
            return await asyncio.wait_for(self._llm_episode_decision(profile, state, task), timeout=timeout)
        except asyncio.TimeoutError:
            self.events.publish('llm_decision_timeout', f'{profile.name} 的 DeepSeek 独立决策超过 {timeout:.1f}s，已用本地策略兜底。', [profile.npc_id])
            self._last_decision_errors.append({'npc_id': profile.npc_id, 'npc_name': profile.name, 'error': f'timeout>{timeout:.1f}s'})
            return None
        except Exception as exc:
            err = f'{exc.__class__.__name__}: {exc}'
            self.events.publish('llm_decision_error', f'{profile.name} 的 DeepSeek 独立决策失败，已用本地策略兜底：{err[:180]}', [profile.npc_id])
            self._last_decision_errors.append({'npc_id': profile.npc_id, 'npc_name': profile.name, 'error': err[:500]})
            return None

    def _should_use_llm_decision(self, npc_id: str, use_llm_planner: bool | None) -> bool:
        status = self.llm.status()
        if status.get('active_mode') != 'real_llm':
            return False
        if use_llm_planner is True:
            return True
        if not self.settings.office_llm_decision_enabled:
            return False
        mode = (self.settings.office_decision_mode or 'llm_parallel').lower().strip()
        if mode in {'llm', 'llm_parallel', 'all_llm', 'parallel_llm'}:
            return self._cooldown_allows_llm(npc_id)
        if mode == 'hybrid':
            budget = int(getattr(self.settings, 'office_llm_decision_budget_per_tick', 1) or 1)
            if budget > 0 and self._llm_decisions_used_this_tick >= budget:
                return False
            if self._cooldown_allows_llm(npc_id):
                self._llm_decisions_used_this_tick += 1
                return True
        return False

    def _cooldown_allows_llm(self, npc_id: str) -> bool:
        cooldown = int(getattr(self.settings, 'office_llm_decision_cooldown_ticks', 0) or 0)
        if cooldown <= 0:
            return True
        return self.tick_count - self._last_llm_decision_tick.get(npc_id, -9999) >= cooldown

    async def _llm_episode_decision(self, profile: NPCProfile, state: dict, task: dict) -> EpisodeDecision | None:
        zones = [{'zone_id': k, 'name': v['name'], 'description': v['description']} for k, v in ZONES.items()]
        recent_events = self.events.recent(limit=6)
        influence = state.get('player_influence') or {}
        query_text = ' '.join((str(x) for x in [state.get('current_task'), state.get('intention'), influence.get('message'), influence.get('directive')]))
        private_memories = self.memory.search_memories(profile.npc_id, '__office__', query_text, limit=6)
        public_memories = []
        if self.memory_router:
            public_memories = self.memory_router.search_context(npc_id=profile.npc_id, query=query_text, player_name='__office__', private_limit=0, public_limit=6).get('public_memories', [])
        else:
            public_memories = self.memory.search_memories('__public__', '__shared__', query_text, limit=6)
        player_memories = []
        if influence.get('player_name'):
            player_memories = self.memory.search_memories(profile.npc_id, str(influence.get('player_name')), query_text, limit=5)
        available_tools = self.tools.list_tools() if self.tools else []
        context = {'tick': self.tick_count, 'agent': {'npc_id': profile.npc_id, 'name': profile.name, 'role': profile.role, 'department': profile.department, 'goals': profile.goals, 'skills': profile.skills, 'responsibilities': profile.responsibilities, 'personality': profile.personality}, 'state': {'location': state.get('location'), 'energy': state.get('energy'), 'stress': state.get('stress'), 'social_need': state.get('social_need'), 'focus': state.get('focus'), 'recent_zones': state.get('recent_zones') or [], 'coffee_streak': state.get('coffee_streak', 0), 'player_influence': influence}, 'task': task, 'private_agent_memories': private_memories, 'public_memory_pool_hits': public_memories, 'relevant_player_memories': player_memories, 'recent_events': recent_events, 'teammates': self._teammate_snapshot(profile.npc_id), 'available_zones': zones, 'available_tools': available_tools, 'decision_contract': {'independent_agent_call': True, 'parallel_batch_tick': self.tick_count, 'you_control_only': profile.name, 'must_create_new_episode': True, 'private_memory_isolation': '只能读取本 Agent 私有记忆 + 公共记忆池；不可读取其他 Agent 私有记忆。', 'tool_loop': '输出 tool_plan，运行时会按安全策略执行并审计。'}}
        result = await self.llm.chat([{'role': 'system', 'content': '你是初创互联网公司办公室里的一个独立 Agent 决策器，此次调用只负责一个成员，不代表全局调度器。请基于该成员的岗位目标、当前状态、任务、队友动态、近期事件和办公室空间 affordance，自主生成下一段行动。不要从固定动作枚举中选择，也不要让所有人做同一件事；要体现真实互联网公司的分工协作。如果 state.player_influence.active=true，说明玩家刚刚与该成员对话并试图影响其行为；除非该指令不安全或与角色完全无关，否则下一段行动必须明显响应这个影响，并在 reasoning_factors 中写明。请结合 private_agent_memories、public_memory_pool_hits 与 relevant_player_memories；不要读取其他 Agent 私有记忆，不要忘记摘要记忆中的长期偏好和承诺。只输出合法 json 对象，不要 Markdown，不要代码块。字段必须包含：intention, action, target_zone, selected_tool, mood, duration_ticks, thought, goal, reasoning_factors, confidence, tool_plan, difficulty, share_memory_candidate。tool_plan 是 0-5 个工具步骤数组；只有当你独立判断该步骤确实有助于完成当前任务时才加入工具，不要为了“看起来智能”而检索或产出。每个元素为 {"tool":"工具名","args":{...}}，工具名必须来自 available_tools；如无需工具，selected_tool="none" 且 tool_plan=[]。遇到知识缺口/阻塞/资料不足时，你可以选择 browser_search_and_ingest 与 search_knowledge；需要写文档/代码/动态工具时，你可以选择 create_company_document、write_code_artifact、run_code_artifact、create_dynamic_tool、run_dynamic_tool。独立 Agent 只能使用 private_agent_memories 与 public_memory_pool_hits；不能读取其他 Agent 私有记忆。share_memory_candidate 用 {"share":true/false,"reason":"..."} 标注哪些记忆值得进入公共记忆池。json 示例：{"intention":"推进老板主任务的架构评审","action":"在白板区梳理服务边界并标注风险点","target_zone":"architecture_board","selected_tool":"search_public_memory","tool_plan":[{"tool":"search_public_memory","args":{"query":"老板主任务 接口契约"}},{"tool":"create_company_document","args":{"document_type":"adr","topic":"服务边界"}}],"difficulty":{"blocked":false,"knowledge_gap":false},"share_memory_candidate":{"share":true,"reason":"接口契约影响多角色"},"mood":"focused","duration_ticks":7,"thought":"当前任务需要技术负责人先澄清边界，检索公共上下文后沉淀 ADR。","goal":"产出一个可执行的调整方案","reasoning_factors":["老板主任务已拆分","岗位职责匹配"],"confidence":0.86}target_zone 必须来自 available_zones。除非 energy < 45 或角色明确需要非正式交流，不要把 target_zone 设为 coffee_bar；如果 recent_zones 最近连续去过同一地点，应主动换到角色相关工作区。duration_ticks 为 4-12 的整数。action 用中文，像真实互联网公司员工正在做的具体动作。回答尽量短，json 中每个字段不要长篇解释；reasoning_factors 用 2-5 条中文短语说明依据。'}, {'role': 'user', 'content': json.dumps(context, ensure_ascii=False)}], temperature=float(getattr(self.settings, 'office_llm_decision_temperature', 0.72) or 0.72), max_tokens=int(getattr(self.settings, 'office_llm_decision_max_tokens', 600) or 600), response_format={'type': 'json_object'}, call_purpose=f'parallel_episode_decision:{profile.npc_id}')
        if result.degraded:
            err = result.error or self.llm.status().get('last_error', 'unknown_llm_error')
            self.events.publish('llm_decision_http_failed', f'{profile.name} 的 DeepSeek HTTP 调用失败：{err[:220]}', [profile.npc_id])
            self._last_decision_errors.append({'npc_id': profile.npc_id, 'npc_name': profile.name, 'error': err[:500]})
            return None
        try:
            obj = self._extract_json(result.text)
            return self._episode_from_llm_object(profile, state, task, obj, repaired=False)
        except Exception as exc:
            raw_preview = (result.text or '')[:420].replace('\n', ' ')
            try:
                repaired = self._repair_llm_text_to_object(result.text or '', profile, state, task)
                decision = self._episode_from_llm_object(profile, state, task, repaired, repaired=True)
                self.events.publish('llm_decision_repaired', f'{profile.name} 的 DeepSeek 行为输出已自动修复为可执行 episode。', [profile.npc_id])
                self._last_decision_errors.append({'npc_id': profile.npc_id, 'npc_name': profile.name, 'error': f'json_or_schema_repaired: {exc.__class__.__name__}: {exc}; raw={raw_preview[:260]}'})
                return decision
            except Exception as repair_exc:
                err = f'{exc.__class__.__name__}: {exc}; repair={repair_exc.__class__.__name__}: {repair_exc}; raw={raw_preview}'
                self.events.publish('llm_decision_parse_failed', f'{profile.name} 的 DeepSeek 行为无法解析，才使用本地安全兜底：{err[:220]}', [profile.npc_id])
                self._last_decision_errors.append({'npc_id': profile.npc_id, 'npc_name': profile.name, 'error': err[:500]})
                return None

    def _episode_from_llm_object(self, profile: NPCProfile, state: dict, task: dict, obj: dict[str, Any], *, repaired: bool=False) -> EpisodeDecision:
        obj = self._normalize_decision_object(obj)
        raw_zone = str(obj.get('target_zone') or obj.get('zone') or obj.get('location') or '').strip()
        zone = self._coerce_zone(profile, raw_zone, state, task, obj)
        duration = self._parse_duration(obj.get('duration_ticks') or obj.get('duration') or obj.get('time_cost') or 6)
        confidence = self._parse_confidence(obj.get('confidence') or obj.get('score') or 0.82)
        factors = self._as_reasoning_list(obj.get('reasoning_factors') or obj.get('reasons') or obj.get('依据') or [])
        action = self._clean_text(obj.get('action') or obj.get('behavior') or obj.get('do') or '推进当前 Agent 项目', 140)
        tool = self._clean_text(obj.get('selected_tool') or obj.get('tool') or obj.get('工具') or 'deepseek_episode_planner', 64)
        intention = self._clean_text(obj.get('intention') or obj.get('intent') or obj.get('意图') or action, 90)
        thought = self._clean_text(obj.get('thought') or obj.get('reasoning') or obj.get('分析') or 'DeepSeek 根据上下文生成了下一段行动。', 260)
        goal = self._clean_text(obj.get('goal') or obj.get('目标') or intention, 140)
        mood = self._clean_text(obj.get('mood') or obj.get('情绪') or 'focused', 32)
        tool_plan = self._normalize_tool_plan(obj.get('tool_plan') or obj.get('tools') or obj.get('工具计划') or [])
        difficulty = obj.get('difficulty') if isinstance(obj.get('difficulty'), dict) else {}
        share_memory_candidate = obj.get('share_memory_candidate') if isinstance(obj.get('share_memory_candidate'), dict) else {}
        source = 'deepseek_episode_repaired' if repaired else 'deepseek_episode'
        return EpisodeDecision(intention=intention, action=action, target_zone=zone, tool=tool, mood=mood, thought=thought, goal=goal, duration_ticks=self._clamp_duration(duration), reasoning_factors=factors[:5] or (['DeepSeek 输出经字段修复后采用'] if repaired else ['DeepSeek episode planner']), confidence=confidence, source=source, action_kind=self._infer_action_kind(action, tool, zone, state, task), tool_plan=tool_plan, difficulty=difficulty, share_memory_candidate=share_memory_candidate)

    def _normalize_tool_plan(self, value: Any) -> list[dict[str, Any]]:
        if not value:
            return []
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list):
            return []
        out: list[dict[str, Any]] = []
        for item in value[:6]:
            if isinstance(item, str):
                out.append({'tool': item, 'args': {}})
            elif isinstance(item, dict):
                name = item.get('tool') or item.get('name') or item.get('tool_name') or item.get('工具')
                args = item.get('args') if isinstance(item.get('args'), dict) else {}
                if item.get('query') and 'query' not in args:
                    args = {**args, 'query': item.get('query')}
                if name:
                    out.append({'tool': self._clean_text(name, 80), 'args': args})
        return out

    def _normalize_decision_object(self, obj: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(obj, dict):
            raise ValueError('decision object is not a dict')
        for key in ('decision', 'episode', 'next_action', 'output', 'result'):
            inner = obj.get(key)
            if isinstance(inner, dict):
                obj = {**obj, **inner}
                break
        alias = {'意图': 'intention', '行为': 'action', '动作': 'action', '目标区域': 'target_zone', '地点': 'target_zone', '区域': 'target_zone', '工具': 'selected_tool', '情绪': 'mood', '耗时': 'duration_ticks', '持续时间': 'duration_ticks', '思考': 'thought', '目标': 'goal', '依据': 'reasoning_factors', '信心': 'confidence', '置信度': 'confidence'}
        for k, v in list(obj.items()):
            if k in alias and alias[k] not in obj:
                obj[alias[k]] = v
        return obj

    def _repair_llm_text_to_object(self, text: str, profile: NPCProfile, state: dict, task: dict) -> dict[str, Any]:
        raw = (text or '').strip()
        if not raw:
            raise ValueError('empty LLM output')
        try:
            obj = self._extract_json_lenient(raw)
            if obj:
                return obj
        except Exception:
            pass
        zone = self._coerce_zone(profile, '', state, task, {'action': raw})
        return {'intention': self._clean_text(raw, 80) or '根据 DeepSeek 文本推进下一段行动', 'action': self._clean_text(raw, 130) or '根据 DeepSeek 文本推进办公室工作', 'target_zone': zone, 'selected_tool': 'deepseek_text_episode_adapter', 'mood': 'focused', 'duration_ticks': 6, 'thought': 'DeepSeek 返回了非标准 JSON，系统保留其语义并转换为可执行 episode。', 'goal': '把真实模型输出转化为可执行办公室行为', 'reasoning_factors': ['真实 DeepSeek 输出', '非标准 JSON 已语义适配'], 'confidence': 0.72}

    def _extract_json_lenient(self, text: str) -> dict[str, Any]:
        clean = text.strip().lstrip('\ufeff')
        clean = clean.removeprefix('```json').removeprefix('```JSON').removeprefix('```').removesuffix('```').strip()
        try:
            parsed = json.loads(clean)
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                return parsed[0]
        except Exception:
            pass
        m = re.search('\\{.*\\}', clean, flags=re.S)
        if m:
            candidate = m.group(0)
            candidate = re.sub(',\\s*([}\\]])', '\\1', candidate)
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        raise ValueError('No recoverable JSON object found')

    def _coerce_zone(self, profile: NPCProfile, raw_zone: str, state: dict, task: dict, obj: dict[str, Any]) -> str:
        raw = str(raw_zone or '').strip()
        if raw in ZONES:
            return self._sanitize_zone(profile, raw, state, task)
        mapped = self._fuzzy_zone(raw, obj)
        if mapped in ZONES:
            return self._sanitize_zone(profile, mapped, state, task)
        tags = set(task.get('tags') or []) if task else set()
        return self._sanitize_zone(profile, self._role_work_zone(profile, tags), state, task)

    def _fuzzy_zone(self, raw_zone: str, obj: dict[str, Any]) -> str:
        text = ' '.join((str(x) for x in [raw_zone, obj.get('action', ''), obj.get('intention', ''), obj.get('thought', ''), obj.get('goal', '')])).lower()
        for zone_id, meta in ZONES.items():
            name = str(meta.get('name', ''))
            if zone_id.lower() in text or (name and name in text):
                return zone_id
        mapping = [(('安全', '合规', '权限', '注入', '评审'), 'security_room'), (('监控', '后端', '接口', '稳定', 'llmops', 'provider', 'deepseek'), 'server_corner'), (('算法', 'prompt', '记忆', 'rag', '上下文', '评估'), 'algo_pod'), (('白板', '架构', '服务边界', '技术方案'), 'architecture_board'), (('产品', '看板', '用户旅程', '增长', '指标'), 'product_board'), (('demo', '演示', '路演', '可视化', '前端', '交互'), 'demo_zone'), (('会议', '同步', '讨论', '站会'), 'meeting_room'), (('面试', '招聘', '简历', '投递', '候选人'), 'interview_room'), (('咖啡', '休息', '恢复', '非正式'), 'coffee_bar'), (('开放', '研发', '协作', '工位'), 'open_workspace')]
        for keys, zone in mapping:
            if any((k in text for k in keys)):
                return zone
        return ''

    def _parse_duration(self, value: Any) -> int:
        if isinstance(value, (int, float)):
            return int(value)
        m = re.search('\\d+', str(value or ''))
        return int(m.group(0)) if m else 6

    def _parse_confidence(self, value: Any) -> float:
        if isinstance(value, (int, float)):
            v = float(value)
        else:
            text = str(value or '').strip().lower()
            if text in {'高', 'high', '较高'}:
                v = 0.86
            elif text in {'中', 'medium', '一般'}:
                v = 0.72
            elif text in {'低', 'low', '较低'}:
                v = 0.58
            else:
                m = re.search('\\d+(?:\\.\\d+)?', text)
                v = float(m.group(0)) if m else 0.82
        if v > 1:
            v = v / 100.0
        return max(0.55, min(0.98, v))

    def _as_reasoning_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [self._clean_text(x, 90) for x in value if str(x).strip()]
        if isinstance(value, dict):
            return [self._clean_text(v, 90) for v in value.values() if str(v).strip()]
        text = str(value or '').strip()
        if not text:
            return []
        parts = re.split('[；;、\\n]+', text)
        return [self._clean_text(x, 90) for x in parts if x.strip()]

    @staticmethod
    def _clean_text(value: Any, limit: int) -> str:
        text = re.sub('\\s+', ' ', str(value or '').strip())
        return text[:limit]

    def _teammate_snapshot(self, current_npc_id: str) -> list[dict[str, Any]]:
        snapshot: list[dict[str, Any]] = []
        for s in self.states.list_states():
            if s.get('npc_id') == current_npc_id:
                continue
            snapshot.append({'name': s.get('name'), 'role': s.get('role'), 'phase': s.get('action_phase'), 'location': s.get('location'), 'intention': s.get('intention'), 'target_zone': s.get('target_zone'), 'energy': s.get('energy'), 'stress': s.get('stress'), 'progress': s.get('action_progress')})
        return snapshot[:8]

    def _local_generative_decision(self, profile: NPCProfile, state: dict, task: dict) -> EpisodeDecision:
        energy = int(state.get('energy', 80))
        stress = int(state.get('stress', 20))
        social = int(state.get('social_need', 45))
        focus = int(state.get('focus', 70))
        task_title = task.get('title') or '办公室自发改进'
        tags = set(task.get('tags') or [])
        recent = self.events.recent(limit=4)
        recent_text = '；'.join((e.get('content', '') for e in recent))
        influence = state.get('player_influence') or {}
        factors: list[str] = []
        if task:
            factors.append(f"任务信号：{task_title} / {','.join(task.get('tags') or []) or '无标签'}")
        else:
            factors.append('没有强绑定任务，进入自发目标模式')
        factors.extend([f'能量={energy}', f'压力={stress}', f'社交需求={social}', f'专注度={focus}'])
        if recent_text:
            factors.append('近期事件影响：' + recent_text[:70])
        if influence.get('active'):
            factors.append('玩家对话影响：' + str(influence.get('directive') or influence.get('message') or '')[:90])
        coffee_streak = int(state.get('coffee_streak', 0) or 0)
        recent_zones = list(state.get('recent_zones') or [])
        motive = 'craft'
        if energy < 34 and coffee_streak < 1:
            motive = 'recover'
        elif energy < 42 and coffee_streak >= 1:
            motive = 'low_energy_light_work'
        elif stress > 72:
            motive = 'de-risk'
        elif social > 84:
            motive = 'sync'
        elif any((k in recent_text for k in ['异常', '风险', '阻塞', '失败', '注入'])):
            motive = 'de-risk'
        elif not task and (self.tick_count + len(recent_zones)) % 4 == 0:
            motive = 'explore'
        elif task and task.get('status') == 'review':
            motive = 'review'
        if influence.get('active') and influence.get('target_zone_hint') in ZONES:
            zone = self._sanitize_zone(profile, influence.get('target_zone_hint'), state, task)
            motive = 'player_directed'
        else:
            zone = self._sanitize_zone(profile, self._infer_zone(profile, tags, motive), state, task)
        skill = self._pick_skill(profile, tags)
        goal = self._derive_goal(profile, task, motive)
        tool = self._derive_tool(profile, skill, motive, tags)
        mood = self._derive_mood(motive, stress, energy, focus)
        action = self._compose_action(profile, task_title, skill, motive)
        intention = self._compose_intention(profile, task_title, motive)
        if influence.get('active'):
            directive = str(influence.get('directive') or influence.get('message') or '')[:90]
            action = f'响应玩家指令并结合{skill}推进：{directive}'
            intention = f'受玩家影响，优先处理：{directive}'
        thought = self._compose_thought(profile, motive, task, factors, zone)
        duration = self._duration(profile, motive, task)
        confidence = self._confidence(profile.npc_id, task.get('id', 0), motive)
        query = ' '.join(str(x or '') for x in [intention, action, goal, task.get('title'), task.get('description')]).strip()



        tool_plan = []
        task_text = f'{task_title} {task.get("description", "")} {profile.role} {skill}'
        if task:
            tool_plan.append({'tool': 'search_public_memory', 'args': {'query': query, 'limit': 4}})
        if any(k in task_text for k in ['知识库', 'RAG', '引用', '资料', '不确定', '调研']):
            tool_plan.append({'tool': 'search_knowledge', 'args': {'query': query, 'limit': 4}})
        if any(k in task_text for k in ['浏览器', '搜索', '外部资料', '最新资料', '文件']):
            tool_plan.append({'tool': 'browser_search_and_ingest', 'args': {'query': query, 'limit': 2}})
        if any(k in task_text for k in ['文档', '报告', 'PRD', '方案', 'ADR', 'Runbook', '验收', '安全评审']):
            doc_type = 'prd' if '产品' in profile.role else ('security_review' if '安全' in profile.role else ('runbook' if 'LLMOps' in profile.role or '后端' in profile.role else 'adr'))
            tool_plan.append({'tool': 'create_company_document', 'args': {'title': f'{profile.name}产出｜{task_title}', 'document_type': doc_type, 'topic': task_title, 'context': task.get('description', '')}})
        if any(k in task_text for k in ['代码', '实现', '运行', '测试', '脚本', '接口', '前端', '后端']):
            tool_plan.append({'tool': 'write_code_artifact', 'args': {'title': f'{profile.name}代码工件｜{task_title}', 'purpose': task_title}})
            tool_plan.append({'tool': 'run_code_artifact', 'args': {'timeout_seconds': 3.0}})
        if any(k in task_text for k in ['动态工具', '写工具', '自动化工具', 'Tool']):
            tool_name = re.sub(r'[^a-zA-Z0-9_]+', '_', f'{profile.npc_id}_{task.get("id", self.tick_count)}_checklist').strip('_').lower()
            tool_plan.append({'tool': 'create_dynamic_tool', 'args': {'name': tool_name, 'description': f'{profile.name} 为 {task_title} 自主创建的检查清单工具', 'tool_kind': 'checklist', 'spec': {'template': '# {topic}\n\n{context}\n\n- [ ] 目标清晰\n- [ ] 资源已检索\n- [ ] 产物已沉淀\n- [ ] 风险可回滚\n'}}})
        if any(k in task_text for k in ['协作', '分工', '接口', '评审', '报告']):
            partner = self._suggest_partner_from_task(profile, task)
            if partner:
                tool_plan.append({'tool': 'send_agent_message', 'args': {'to_npc_id': partner, 'content': f'{profile.name} 请求围绕「{task_title}」进行协作判断', 'related_task_id': task.get('id')}})
        difficulty = {'blocked': task.get('status') == 'blocked', 'knowledge_gap': motive in {'de-risk', 'review'} and self.tick_count % 5 == 0}
        return EpisodeDecision(intention=intention, tool=(tool_plan[0]['tool'] if tool_plan else 'none'), action=action, mood=mood, target_zone=zone, thought=thought, goal=goal, duration_ticks=duration, confidence=confidence, reasoning_factors=factors[:6], source='local_agent_planner', action_kind=self._infer_action_kind(action, (tool_plan[0]['tool'] if tool_plan else 'none'), zone, state, task, motive=motive), tool_plan=tool_plan, difficulty=difficulty, share_memory_candidate={'share': motive in {'de-risk', 'sync', 'review'} and bool(task), 'reason': f'本地 Agent 判断 motive={motive} 可能影响团队协作'})

    def _sanitize_zone(self, profile: NPCProfile, zone: str, state: dict, task: dict) -> str:
        if zone not in ZONES:
            zone = profile.home_zone or 'open_workspace'
        energy = int(state.get('energy', 80) or 80)
        coffee_streak = int(state.get('coffee_streak', 0) or 0)
        recent = list(state.get('recent_zones') or [])
        tags = set(task.get('tags') or []) if task else set()
        if zone == 'coffee_bar':
            if energy >= 52 or coffee_streak >= 1:
                return self._role_work_zone(profile, tags, avoid=set(recent[-3:]) | {'coffee_bar'})
            return zone
        if len(recent) >= 3 and all((z == zone for z in recent[-3:])):
            return self._role_work_zone(profile, tags, avoid={zone})
        return zone

    def _role_work_zone(self, profile: NPCProfile, tags: set[str], avoid: set[str] | None=None) -> str:
        avoid = avoid or set()
        role = profile.role
        ordered: list[str] = []
        if 'security' in tags or 'audit' in tags or '安全' in role:
            ordered += ['security_room', 'server_corner', 'meeting_room']
        if 'backend' in tags or 'llmops' in tags or 'deepseek' in tags or ('后端' in role) or ('LLMOps' in role):
            ordered += ['server_corner', 'architecture_board', 'open_workspace']
        if 'frontend' in tags or 'demo' in tags or '前端' in role or ('交互' in role):
            ordered += ['demo_zone', 'open_workspace', 'product_board']
        if 'product' in tags or 'metrics' in tags or '产品' in role:
            ordered += ['product_board', 'meeting_room', 'demo_zone']
        if 'memory' in tags or 'prompt' in tags or 'eval' in tags or ('算法' in role):
            ordered += ['algo_pod', 'architecture_board', 'open_workspace']
        if 'resume' in tags or 'interview' in tags or '招聘' in role:
            ordered += ['interview_room', 'demo_zone', 'meeting_room']
        ordered += [profile.home_zone, 'open_workspace', 'meeting_room', 'architecture_board', 'demo_zone', 'product_board']
        for z in ordered:
            if z in ZONES and z not in avoid and (z != 'coffee_bar'):
                return z
        return profile.home_zone if profile.home_zone in ZONES else 'open_workspace'

    def _infer_action_kind(self, action: str, tool: str, zone: str, state: dict, task: dict, motive: str | None=None) -> str:
        text = f"{action} {tool} {zone} {motive or ''}".lower()
        if zone == 'coffee_bar' or 'recover' in text or 'coffee' in text or ('恢复' in text) or ('咖啡' in text):
            return 'recover'
        if 'sync' in text or '同步' in text or zone == 'meeting_room':
            return 'sync'
        if 'risk' in text or 'security' in text or '安全' in text or ('风险' in text) or (zone == 'security_room'):
            return 'derisk'
        if 'review' in text or '验收' in text or task.get('status') == 'review':
            return 'review'
        if 'explore' in text or '巡视' in text or '观察' in text:
            return 'explore'
        if motive == 'low_energy_light_work':
            return 'light_work'
        return 'work'

    def _infer_zone(self, profile: NPCProfile, tags: set[str], motive: str) -> str:
        if motive == 'recover':
            return 'coffee_bar'
        if motive == 'low_energy_light_work':
            return profile.home_zone if profile.home_zone != 'coffee_bar' else 'open_workspace'
        if motive == 'sync':
            return 'meeting_room' if self.tick_count % 3 else 'product_board'
        if motive == 'de-risk':
            if 'security' in tags or 'audit' in tags or '安全' in profile.role:
                return 'security_room'
            return 'server_corner' if '后端' in profile.role or 'LLMOps' in profile.role else 'meeting_room'
        if motive == 'review':
            if 'demo' in tags or 'resume' in tags:
                return 'demo_zone'
            return 'meeting_room'
        if 'frontend' in tags or 'movement' in tags or '交互' in profile.role:
            return 'demo_zone'
        if 'deepseek' in tags or 'llmops' in tags or 'backend' in tags:
            return 'server_corner'
        if 'security' in tags or 'audit' in tags:
            return 'security_room'
        if 'product' in tags or 'metrics' in tags or '产品' in profile.role:
            return 'product_board'
        if 'memory' in tags or 'prompt' in tags or 'eval' in tags or ('算法' in profile.role):
            return 'algo_pod'
        if 'resume' in tags or 'interview' in tags or '招聘' in profile.role:
            return 'interview_room'
        if 'architecture' in tags or 'planning' in tags or '技术负责人' in profile.role:
            return 'architecture_board'
        if motive == 'explore':
            return self._explore_zone(profile)
        return profile.home_zone

    def _explore_zone(self, profile: NPCProfile) -> str:
        ids = list(ZONES.keys())
        h = int(hashlib.md5(f'{profile.npc_id}-{self.tick_count}-explore'.encode()).hexdigest()[:8], 16)
        preferred = [profile.home_zone, 'meeting_room', 'open_workspace', 'demo_zone', 'product_board']
        pool = preferred + ids
        return pool[h % len(pool)]

    def _pick_skill(self, profile: NPCProfile, tags: set[str]) -> str:
        if not profile.skills:
            return profile.role
        scored = []
        for skill in profile.skills:
            lower = skill.lower()
            overlap = sum((1 for tag in tags if tag.lower() in lower or lower in tag.lower()))
            scored.append((overlap, skill))
        scored.sort(key=lambda x: (-x[0], x[1]))
        if scored and scored[0][0] > 0:
            return scored[0][1]
        h = int(hashlib.md5(f'{profile.npc_id}-{self.tick_count}-skill'.encode()).hexdigest()[:6], 16)
        return profile.skills[h % len(profile.skills)]

    def _derive_goal(self, profile: NPCProfile, task: dict, motive: str) -> str:
        if task:
            return f"让「{task.get('title')}」向可演示、可验收、可写进简历的方向推进"
        if motive == 'recover':
            return '恢复能量并在非正式交流中捕获新想法'
        if motive == 'sync':
            return '主动同步上下文，减少多智能体协作偏差'
        if motive == 'de-risk':
            return '提前处理风险，避免演示和投递环节失控'
        if profile.goals:
            return profile.goals[self.tick_count % len(profile.goals)]
        return '提升办公室 Agent 系统的真实感和工程完整度'

    def _derive_tool(self, profile: NPCProfile, skill: str, motive: str, tags: set[str]) -> str:
        base = '_'.join(re.findall('[A-Za-z0-9]+', skill.lower())) or 'agent_tool'
        if motive == 'recover':
            return 'energy_recovery_and_informal_context_probe'
        if motive == 'low_energy_light_work':
            return 'low_energy_async_work_planner'
        if motive == 'sync':
            return 'context_sync_briefing_tool'
        if motive == 'de-risk':
            return 'risk_probe_and_incident_triage'
        if motive == 'review':
            return 'acceptance_review_matrix'
        suffix = 'memory' if {'memory', 'prompt', 'eval'} & tags else 'workspace'
        return f'{base}_{suffix}_tool'[:60]

    def _derive_mood(self, motive: str, stress: int, energy: int, focus: int) -> str:
        if motive == 'recover':
            return 'tired_but_open'
        if motive == 'low_energy_light_work':
            return 'quiet_focus'
        if motive == 'de-risk':
            return 'alert'
        if motive == 'sync':
            return 'collaborative'
        if focus > 80:
            return 'deep_focus'
        if stress > 60:
            return 'serious'
        return 'focused'

    def _compose_action(self, profile: NPCProfile, title: str, skill: str, motive: str) -> str:
        prefix = {'recover': '边喝咖啡边把零散上下文整理成下一步线索', 'sync': '拉一个轻量同步，把关键信息写成团队可理解的行动项', 'low_energy_light_work': '切换到低强度工作，把可延迟事项整理成清单', 'de-risk': '围绕潜在风险做一次快速排查并记录缓解方案', 'review': '对当前产出做验收走查并提出可落地修改点', 'explore': '巡视办公室，观察其他 Agent 的状态并寻找新的协作机会', 'craft': '沉浸式推进核心工作并产出一段可验证结果'}.get(motive, '推进工作')
        return f'{prefix}：基于{skill}处理「{title}」'

    def _compose_intention(self, profile: NPCProfile, title: str, motive: str) -> str:
        role_focus = profile.responsibilities[self.tick_count % len(profile.responsibilities)] if profile.responsibilities else profile.role
        if motive == 'recover':
            return f'恢复状态，同时从非正式交流中为{role_focus}找灵感'
        if motive == 'low_energy_light_work':
            return f'用低强度方式维持产出，避免再次坍缩到咖啡吧'
        if motive == 'sync':
            return f'主动同步{role_focus}相关上下文，避免团队重复劳动'
        if motive == 'de-risk':
            return f'先处理{title}背后的风险和阻塞'
        return f'围绕{role_focus}自主推进：{title}'

    def _compose_thought(self, profile: NPCProfile, motive: str, task: dict, factors: list[str], zone: str) -> str:
        task_part = f"任务「{task.get('title')}」" if task else '没有强绑定任务'
        return f'{profile.name}观察到{task_part}，结合{factors[:3]}，生成 motive={motive}，决定去{zone_name(zone)}执行一个持续行动片段。'

    def _duration(self, profile: NPCProfile, motive: str, task: dict) -> int:
        low = max(1, self.settings.office_action_min_ticks)
        high = max(low, self.settings.office_action_max_ticks)
        base = low + int(hashlib.md5(f"{profile.npc_id}-{self.tick_count}-{task.get('id', 0)}-{motive}".encode()).hexdigest()[:2], 16) % (high - low + 1)
        if task.get('priority', 3) >= 5:
            base = min(high, base + 2)
        if motive in {'recover', 'sync', 'low_energy_light_work'}:
            base = max(low, base - 1)
        return self._clamp_duration(base)

    def _clamp_duration(self, n: int) -> int:
        return max(self.settings.office_action_min_ticks, min(self.settings.office_action_max_ticks, int(n)))

    def _complete_episode(self, profile: NPCProfile, state: dict) -> int | None:
        task_title = state.get('current_task') or state.get('intention') or '自主行动'
        task_id = self._task_id_by_title(task_title, profile.npc_id)
        outcome = 'progress'
        if state.get('mood') == 'alert' and self.tick_count % 11 == 0:
            outcome = 'blocked'
        if task_id:
            self.tasks.advance_after_episode(task_id, outcome=outcome)
        content = f"{profile.name}在{state.get('location')}完成行动片段：{state.get('intention')}；工具={state.get('selected_tool')}；耗时={state.get('action_duration_ticks')} ticks。"
        self.memory.add_memory(profile.npc_id, '__office__', content, importance=2, kind='agent_episode')
        self.states.finish_episode(profile.npc_id, summary=f"完成：{state.get('intention')}")
        return self.events.publish('agent_episode_done', content, [profile.npc_id])

    def _remember_agent_step(self, profile: NPCProfile, state: dict, kind: str, content: str, importance: int=1) -> None:
        prefix = f"[tick={self.tick_count}][phase={state.get('action_phase')}][episode={state.get('episode_id') or '-'}]"
        text = f'{prefix} {profile.name}：{content}'
        self.memory.add_memory(profile.npc_id, '__office__', text, importance=importance, kind=kind)
        self._memory_keys_to_compact.add((profile.npc_id, '__office__'))

    async def _compact_memory_streams(self) -> None:
        if not self.compactor:
            return
        keys = set(self._memory_keys_to_compact)




        if self.tick_count % 10 == 0:
            try:
                for stream in self.memory.active_streams(threshold=self.settings.memory_compact_threshold, limit=60):
                    keys.add((str(stream['npc_id']), str(stream['player_name'])))
            except Exception:
                pass
        if not keys:
            return
        for npc_id, player_name in list(keys):
            summary = await self.compactor.maybe_compact(npc_id, player_name)
            if summary:
                profile = self.profile_map.get(npc_id)
                if profile:
                    self.states.note_memory_summary(npc_id, summary.get('summary', ''))
                display = profile.name if profile else ('公共记忆池' if npc_id.startswith('__public__') else npc_id)
                self.events.publish('memory_compacted', f"{display} 的 {player_name} 记忆流已压缩：{summary.get('source_count')} 条 → 摘要 #{summary.get('summary_id')}。", [npc_id])
        self._memory_keys_to_compact = set()

    async def _maybe_finalize_boss_reports(self, created_event_ids: list[int]) -> None:
        if not self.boss_mission_service:
            return
        try:
            reports = await self.boss_mission_service.maybe_generate_ready_reports(profiles=self.profiles)
            for report in reports:
                event_id = report.get('event_id')
                if event_id:
                    created_event_ids.append(int(event_id))
        except Exception as exc:
            created_event_ids.append(self.events.publish('boss_report_error', f'主任务报告生成失败，已保留当前状态：{exc.__class__.__name__}: {exc}', []))

    def _task_id_by_title(self, title: str, npc_id: str) -> int | None:
        for task in self.tasks.list_tasks(owner_npc_id=npc_id, limit=20):
            if task.get('title') == title and task.get('status') != 'done':
                return int(task['id'])
        for task in self.tasks.list_tasks(limit=30):
            if task.get('title') == title and task.get('status') != 'done':
                return int(task['id'])
        return None

    def _trace_from_decision(self, profile: NPCProfile, decision: EpisodeDecision, target: Position, task: dict, state: dict) -> dict:
        return AgentDecision(npc_id=profile.npc_id, npc_name=profile.name, thought=decision.thought, goal=decision.goal, selected_tool=decision.tool, action=state.get('current_action') or decision.action, target_zone=decision.target_zone, target_position=target, task_id=task.get('id') if task else None, confidence=decision.confidence, phase=state.get('action_phase', 'walking'), intention=decision.intention, action_duration_ticks=decision.duration_ticks, action_remaining_ticks=state.get('action_remaining_ticks', decision.duration_ticks), action_progress=state.get('action_progress', 0.0), decision_source=decision.source, reasoning_factors=decision.reasoning_factors, tool_trace=decision.tool_trace, observations=decision.observations, reflection=decision.reflection, tool_plan=decision.tool_plan, thinking_trace=decision.thinking_trace, collaboration_judgement=decision.collaboration_judgement, rollback_plan=decision.rollback_plan, company_actions=decision.company_actions).model_dump()

    def _trace_from_state(self, profile: NPCProfile, state: dict, thought_prefix: str='') -> dict:
        target_raw = state.get('target_position') or profile.position.model_dump()
        target = Position(**target_raw)
        thought = thought_prefix or state.get('plan_summary') or '继续当前自主行动。'
        if state.get('plan_summary') and state.get('plan_summary') not in thought:
            thought = f"{thought} {state.get('plan_summary')}"
        return AgentDecision(npc_id=profile.npc_id, npc_name=profile.name, thought=thought, goal=state.get('intention') or state.get('current_task') or '自主推进', selected_tool=state.get('selected_tool') or 'intent_synthesizer', action=state.get('current_action') or '自主行动', target_zone=state.get('target_zone') or profile.home_zone, target_position=target, task_id=None, confidence=float(state.get('autonomy', 0.8) or 0.8), phase=state.get('action_phase', 'thinking'), intention=state.get('intention') or '', action_duration_ticks=int(state.get('action_duration_ticks') or 1), action_remaining_ticks=int(state.get('action_remaining_ticks') or 0), action_progress=float(state.get('action_progress') or 0.0), decision_source='episode_runtime', reasoning_factors=state.get('reasoning_factors') or [], observations=['继续执行上一段 episode'], reflection=state.get('plan_summary') or '', rollback_plan='若当前 episode 异常，可通过 /company/transactions 找到招聘/扩张事务并回滚。').model_dump()

    def _jittered_target(self, zone_id: str, npc_id: str) -> Position:
        base = zone_center(zone_id)
        z = ZONES.get(zone_id) or ZONES['open_workspace']
        _, _, w, h = z['rect']
        hsh = int(hashlib.md5(f'{npc_id}-{self.tick_count}-{zone_id}'.encode()).hexdigest()[:8], 16)
        angle = hsh % 360 / 180 * pi
        radius = 10 + hsh % 34
        max_x = max(8, w / 2 - 18)
        max_y = max(8, h / 2 - 18)
        x = base.x + max(-max_x, min(max_x, cos(angle) * radius))
        y = base.y + max(-max_y, min(max_y, sin(angle) * radius))
        return Position(x=max(25, min(1055, x)), y=max(25, min(595, y)))

    def _confidence(self, npc_id: str, task_id: int, motive: str) -> float:
        h = int(hashlib.md5(f'{npc_id}-{task_id}-{self.tick_count}-{motive}'.encode()).hexdigest()[:4], 16)
        return round(0.7 + h % 27 / 100, 2)

    def _episode_id(self, npc_id: str, decision: EpisodeDecision, task: dict) -> str:
        raw = f"{npc_id}-{self.tick_count}-{decision.intention}-{task.get('id', 0)}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    def _maybe_create_ritual_event(self) -> int | None:
        if bool(getattr(self.settings, 'boss_task_mode_enabled', True)) and self.tasks.active_count() <= 0:
            return None
        if self.tick_count % 18 == 0:
            owners = [p.npc_id for p in random.sample(self.profiles, min(3, len(self.profiles)))]
            return self.events.publish('standup', '团队自然聚合成 10 分钟站会：每个 Agent 只同步正在执行的行动片段和阻塞。', owners)
        if self.tick_count % 29 == 0:
            return self.events.publish('demo_review', '产品经理发起 Demo Review：检查丝滑移动、行动耗时和 Agent 自主判断是否足够可感知。', ['tang_pm', 'yin_hr', 'guo_cto'])
        if self.tick_count % 37 == 0:
            return self.events.publish('incident', '监控角出现一次模型调用延迟异常，相关 Agent 会自行判断是否需要介入。', ['lu_ops', 'han_security'])
        return None

    def _maybe_inject_dynamic_task(self) -> None:
        if bool(getattr(self.settings, 'boss_task_mode_enabled', True)):
            return
        if self.tick_count < 4:
            return
        if self.tick_count % 11 != 0 and self.tasks.active_count() > 5:
            return
        profile = self.profiles[(self.tick_count + self._dynamic_task_counter) % len(self.profiles)]
        goal = profile.goals[(self.tick_count + len(profile.name)) % len(profile.goals)] if profile.goals else '提升 Agent 系统'
        skill = profile.skills[(self.tick_count + self._dynamic_task_counter) % len(profile.skills)] if profile.skills else profile.role
        titles = [f'围绕{skill}补充一个可量化验收点', f'把「{goal}」转成一段面试可讲的工程难点', f'复盘最近事件并生成下一轮办公室协作实验', f'验证{profile.department}视角下的 Agent 自主性体验', f'整理{profile.role}对当前 Demo 的改进建议']
        title = titles[self._dynamic_task_counter % len(titles)]
        existing = [t['title'] for t in self.tasks.list_tasks(limit=80)]
        if title in existing:
            self._dynamic_task_counter += 1
            return
        tags = self._tags_for_profile(profile, skill)
        self.tasks.create_task(title=title, description=f'由办公室运行时自动生成：{profile.name}基于自身目标和最近事件提出的自发工作上下文。', owner_npc_id=profile.npc_id, priority=3 + self.tick_count % 3, tags=tags)
        self._dynamic_task_counter += 1
        self.events.publish('self_directed_task', f'{profile.name}自发创建新工作上下文：{title}', [profile.npc_id])

    def _tags_for_profile(self, profile: NPCProfile, skill: str) -> list[str]:
        text = f'{profile.role} {profile.department} {skill}'.lower()
        tags = []
        mapping = {'产品': 'product', '增长': 'metrics', '算法': 'eval', 'prompt': 'prompt', 'memory': 'memory', '安全': 'security', '合规': 'audit', '前端': 'frontend', '交互': 'demo', '后端': 'backend', 'llmops': 'llmops', 'deepseek': 'deepseek', '招聘': 'interview', 'hr': 'resume', '架构': 'architecture'}
        for k, v in mapping.items():
            if k.lower() in text:
                tags.append(v)
        return list(dict.fromkeys(tags or ['agent', 'workspace']))

    async def _llm_coordinator_event(self, traces: list[dict]) -> int | None:
        status = self.llm.status()
        if status.get('active_mode') != 'real_llm':
            return None
        compact = [{'npc': t['npc_name'], 'phase': t.get('phase'), 'intention': t.get('intention'), 'progress': t.get('action_progress')} for t in traces[:7]]
        result = await self.llm.chat([{'role': 'system', 'content': '你是初创互联网公司办公室的多智能体调度总监。请根据各角色行动，用一句中文输出下一步协作建议，不超过80字。'}, {'role': 'user', 'content': json.dumps(compact, ensure_ascii=False)}], temperature=0.25, max_tokens=120)
        if not result.text:
            return None
        return self.events.publish('llm_coordinator', f'DeepSeek 协调器建议：{result.text[:160]}', [t['npc_id'] for t in traces[:3]])

    def _extract_json(self, text: str) -> dict[str, Any]:
        obj = self._extract_json_lenient(text)
        if not isinstance(obj, dict):
            raise ValueError('JSON is not object')
        return obj

    def last_trace_payload(self) -> dict:
        return {'tick': self.tick_count, 'agent_traces': self.last_traces, 'last_parallel_batch': self._last_parallel_batch}
