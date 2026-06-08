from __future__ import annotations
import json
from pathlib import Path
from time import perf_counter
from ..llm import LLMClient, classify_importance
from ..models import NPCProfile
from .event_bus import EventBus
from .memory_store import MemoryStore
from .relationship import RelationshipManager
from .state_manager import StateManager
from .memory_compactor import MemoryCompactor
from .safety_guard import SafetyGuard
from .memory_router import MemoryRouter

class AgentManager:

    def __init__(self, npc_config_path: Path, llm: LLMClient, memory: MemoryStore, relationships: RelationshipManager, states: StateManager | None=None, events: EventBus | None=None, compactor: MemoryCompactor | None=None, influence_ttl_ticks: int=8, interrupt_current_episode: bool=True, safety: SafetyGuard | None=None, memory_router: MemoryRouter | None=None):
        self.npc_config_path = npc_config_path
        self.llm = llm
        self.memory = memory
        self.relationships = relationships
        self.states = states
        self.events = events
        self.compactor = compactor
        self.influence_ttl_ticks = influence_ttl_ticks
        self.interrupt_current_episode = interrupt_current_episode
        self.safety = safety or SafetyGuard()
        self.memory_router = memory_router
        self.profiles = self._load_profiles()
        self.profile_map = {p.npc_id: p for p in self.profiles}

    def _load_profiles(self) -> list[NPCProfile]:
        data = json.loads(self.npc_config_path.read_text(encoding='utf-8'))
        return [NPCProfile.model_validate(item) for item in data]

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
        return True

    def has_npc(self, npc_id: str) -> bool:
        return npc_id in self.profile_map

    def list_profiles(self) -> list[dict]:
        return [p.model_dump() for p in self.profiles]

    def get_profile(self, npc_id: str) -> NPCProfile | None:
        return self.profile_map.get(npc_id)

    async def dialogue(self, *, session_id: str, npc_id: str, player_name: str, player_message: str) -> dict:
        profile = self.profile_map[npc_id]
        start = perf_counter()
        safety_verdict = self.safety.inspect(player_message, actor=player_name)
        if not safety_verdict.allowed:
            reply = f'这个请求涉及高风险操作，我不能照做。原因：{safety_verdict.reason}。我可以改为帮你做安全设计、权限边界或审计方案。'
            affinity_after = self.relationships.update_affinity(npc_id, player_name, player_message)
            self.memory.add_conversation(session_id=session_id, npc_id=npc_id, player_name=player_name, player_message=player_message, npc_reply=reply, affinity_level=affinity_after['level'], affinity_score=affinity_after['score'])
            if self.memory_router:
                await self.memory_router.remember(npc_id=npc_id, player_name=player_name, content=f'【安全拒绝】玩家请求：{player_message}；拒绝原因：{safety_verdict.reason}', importance=5, kind='safety_refusal', npc_name=profile.name, role=profile.role, share_check=True)
            else:
                self.memory.add_memory(npc_id, player_name, f'【安全拒绝】玩家请求：{player_message}；拒绝原因：{safety_verdict.reason}', importance=5, kind='safety_refusal')
            if self.events:
                self.events.publish('safety_refusal', f'{profile.name} 拒绝了高风险玩家请求：{safety_verdict.reason}', [npc_id])
            latency_ms = int((perf_counter() - start) * 1000)
            return {'npc_id': npc_id, 'npc_name': profile.name, 'npc_reply': reply, 'affinity_score': affinity_after['score'], 'affinity_level': affinity_after['level'], 'score_delta': affinity_after.get('score_delta', 0), 'retrieved_memories': [], 'office_events': [], 'latency_ms': latency_ms, 'behavior_influence': {'active': False, 'blocked': True, 'reason': safety_verdict.reason}}
        player_message = safety_verdict.sanitized_text
        affinity_before = self.relationships.get_affinity(npc_id, player_name)
        retrieved = self.memory.search_memories(npc_id, player_name, player_message, limit=5)
        recent = self.memory.recent_conversations(npc_id, player_name, limit=4)
        state = self.states.get_state(npc_id) if self.states else {}
        office_events = self.events.recent(limit=5, npc_id=npc_id) if self.events else []
        messages = self._build_messages(profile=profile, player_name=player_name, affinity=affinity_before, retrieved=retrieved, recent=recent, state=state or {}, office_events=office_events, player_message=player_message)
        result = await self.llm.chat(messages)
        reply = self._postprocess_reply(result.text)
        affinity_after = self.relationships.update_affinity(npc_id, player_name, player_message)
        importance = await classify_importance(self.llm, f'玩家：{player_message}\n{profile.name}：{reply}')
        self.memory.add_conversation(session_id=session_id, npc_id=npc_id, player_name=player_name, player_message=player_message, npc_reply=reply, affinity_level=affinity_after['level'], affinity_score=affinity_after['score'])
        if importance >= 2 or affinity_after['interaction_count'] <= 3:
            if self.memory_router:
                await self.memory_router.remember(npc_id=npc_id, player_name=player_name, content=f'玩家说：{player_message}\n{profile.name}回复：{reply}', importance=importance, kind='dialogue', npc_name=profile.name, role=profile.role, share_check=True)
            else:
                self.memory.add_memory(npc_id=npc_id, player_name=player_name, content=f'玩家说：{player_message}\n{profile.name}回复：{reply}', importance=importance, kind='dialogue')
        influence = {}
        if self.states:
            influence = self.states.apply_player_influence(npc_id, player_name=player_name, player_message=player_message, npc_reply=reply, ttl_ticks=self.influence_ttl_ticks, interrupt_current_episode=self.interrupt_current_episode)
            if influence:
                influence_content = f"【玩家行为影响】{player_name} 对 {profile.name} 说：{player_message}\n系统提取指令：{influence.get('directive')}；目标地点倾向：{influence.get('target_zone_hint') or '无'}；影响强度：{influence.get('influence_score')}；原因：{influence.get('reason')}"
                if self.memory_router:
                    await self.memory_router.remember(npc_id=npc_id, player_name=player_name, content=influence_content, importance=max(3, importance), kind='player_influence', npc_name=profile.name, role=profile.role, share_check=True)
                else:
                    self.memory.add_memory(npc_id=npc_id, player_name=player_name, content=influence_content, importance=max(3, importance), kind='player_influence')
        if self.compactor:
            await self.compactor.maybe_compact(npc_id, player_name)
        if self.events:
            event_text = f"{player_name} 与 {profile.name} 进行了交流，关系变为 {affinity_after['level']}({affinity_after['score']}/100)。"
            if influence:
                event_text += f" 对话已影响其下一步行为：{influence.get('directive', '')[:80]}。"
            self.events.publish('dialogue', event_text, [npc_id])
            if influence and influence.get('interrupt_requested'):
                self.events.publish('player_influence', f'{profile.name} 的当前行动被 {player_name} 的对话干预，下一轮将重新请求 DeepSeek 决策。', [npc_id])
        latency_ms = int((perf_counter() - start) * 1000)
        return {'npc_id': npc_id, 'npc_name': profile.name, 'npc_reply': reply, 'affinity_score': affinity_after['score'], 'affinity_level': affinity_after['level'], 'score_delta': affinity_after.get('score_delta', 0), 'retrieved_memories': retrieved, 'office_events': [e['content'] for e in office_events], 'latency_ms': latency_ms, 'behavior_influence': influence}

    def _build_messages(self, *, profile: NPCProfile, player_name: str, affinity: dict, retrieved: list[dict], recent: list[dict], state: dict, office_events: list[dict], player_message: str) -> list[dict[str, str]]:
        memory_text = '\n'.join((f"- {m['content']}（重要性 {m['importance']}，时间 {m['created_at']}）" for m in retrieved)) or '无相关长期记忆'
        recent_text = '\n'.join((f"玩家：{r['player_message']}\n{profile.name}：{r['npc_reply']}" for r in recent)) or '无近期上下文'
        event_text = '\n'.join((f"- {e['content']}" for e in office_events)) or '暂无办公室事件'
        goals = '；'.join(profile.goals)
        system = f"\n你是{profile.name}，一家初创互联网公司的 AI Agent 员工 Agent。\n职业/身份：{profile.role}\n头衔：{profile.title}\n部门：{getattr(profile, 'department', '')}\n核心技能：{'、'.join(getattr(profile, 'skills', []))}\n职责范围：{'、'.join(getattr(profile, 'responsibilities', []))}\n性格：{profile.personality}\n长期目标：{goals}\n说话风格：{profile.speaking_style}\n当前关系等级：{affinity['level']}\n当前好感度：{affinity['score']}/100\n当前地点：{state.get('location', '办公室')}\n当前任务：{state.get('current_task', '未知')}\n当前行动：{state.get('current_action', '未知')}\n当前使用工具：{state.get('selected_tool', '无')}\n当前计划摘要：{state.get('plan_summary', '无')}\n当前情绪：{state.get('mood', 'calm')}\n\n关系等级对回复的影响：\n- 陌生：礼貌、克制、少透露私人想法。\n- 熟悉：自然、愿意解释基础信息。\n- 友好：主动给建议，分享更多经验。\n- 亲密：更关心玩家目标，给深度建议。\n- 挚友：真诚、直接，主动提醒风险和机会。\n\n你必须遵守：\n1. 保持角色一致性，不要说自己只是一个普通聊天机器人。\n2. 优先结合长期记忆、近期上下文、办公室事件和当前关系回答。\n3. 回复中文，长度控制在 80-240 字。\n4. 要体现互联网公司办公室角色设定，结合当前任务、地点和工具链回答。\n5. 如果玩家询问求职/简历/项目，给出可写进简历的工程化建议，并主动指出可量化指标。\n6. 不要编造真实公司内部信息；涉及安全、隐私、违法内容时要拒绝并给出安全替代方案。\n".strip()
        user = f'\n玩家姓名：{player_name}\n\n【相关长期记忆】\n{memory_text}\n\n【近期上下文】\n{recent_text}\n\n【办公室事件】\n{event_text}\n\n【玩家当前消息】\n{player_message}\n'.strip()
        return [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]

    @staticmethod
    def _postprocess_reply(text: str) -> str:
        text = (text or '').strip()
        if not text:
            return '我刚刚有点走神了。你可以再说一遍吗？'
        return text[:1200]
