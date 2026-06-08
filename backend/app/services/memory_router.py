from __future__ import annotations
import json
import re
from dataclasses import dataclass
from typing import Any

from ..config import Settings
from ..llm import LLMClient
from .memory_store import MemoryStore
from .safety_guard import SafetyGuard

PUBLIC_NPC_ID = '__public__'
PUBLIC_PLAYER_NAME = '__shared__'

@dataclass
class MemoryRouteResult:
    private_memory_id: int
    public_memory_id: int | None = None
    shared: bool = False
    reason: str = ''
    confidence: float = 0.0

class MemoryRouter:

    SHARE_KEYWORDS = [
        '阻塞', '风险', '安全', '漏洞', '注入', '权限', '合规', '审计', 'incident', '故障',
        '架构', '接口', '协议', '依赖', '协作', '跨角色', '公共', '团队', '站会',
        '验收', '指标', 'RAG', '知识库', '工具调用', 'Tool', 'LangChain', 'LangGraph',
        '上线', '回滚', '成本', '延迟', '超时', '评估', '基准', '文档'
    ]
    PRIVATE_HINTS = ['手机号', '身份证', '密码', '密钥', 'token', '私人', '隐私', '个人住址']

    def __init__(self, memory: MemoryStore, llm: LLMClient, settings: Settings, safety: SafetyGuard | None = None):
        self.memory = memory
        self.llm = llm
        self.settings = settings
        self.safety = safety or SafetyGuard()
        self.public_npc_id = getattr(settings, 'memory_public_npc_id', PUBLIC_NPC_ID) or PUBLIC_NPC_ID
        self.public_player_name = getattr(settings, 'memory_public_player_name', PUBLIC_PLAYER_NAME) or PUBLIC_PLAYER_NAME
        self.last_decisions: list[dict[str, Any]] = []

    async def remember(self, *, npc_id: str, player_name: str, content: str, importance: int = 1, kind: str = 'memory', npc_name: str = '', role: str = '', share_check: bool = True) -> MemoryRouteResult:
        private_id = self.memory.add_memory(npc_id=npc_id, player_name=player_name, content=content, importance=importance, kind=kind)
        result = MemoryRouteResult(private_memory_id=private_id)
        if not share_check:
            result.reason = 'share_check_disabled'
            return result
        decision = await self.should_share(npc_id=npc_id, player_name=player_name, content=content, importance=importance, kind=kind, npc_name=npc_name, role=role)
        self.last_decisions.append(decision)
        self.last_decisions = self.last_decisions[-80:]
        result.shared = bool(decision.get('share'))
        result.reason = str(decision.get('reason') or '')
        result.confidence = float(decision.get('confidence') or 0.0)
        if result.shared:
            public_content = self._public_content(npc_id=npc_id, npc_name=npc_name, role=role, source_owner=player_name, kind=kind, content=content, reason=result.reason)
            result.public_memory_id = self.memory.add_memory(npc_id=self.public_npc_id, player_name=self.public_player_name, content=public_content, importance=max(importance, 3), kind='public_shared')
        return result

    async def should_share(self, *, npc_id: str, player_name: str, content: str, importance: int, kind: str, npc_name: str = '', role: str = '') -> dict[str, Any]:
        text = str(content or '')
        verdict = self.safety.inspect(text, actor=f'memory:{npc_id}')
        if not verdict.allowed:
            return {'share': False, 'reason': f'安全拒绝共享：{verdict.reason}', 'confidence': 0.95, 'source': 'safety'}
        if any(h in text for h in self.PRIVATE_HINTS):
            return {'share': False, 'reason': '疑似包含隐私/凭证信息，只保留在私有记忆。', 'confidence': 0.9, 'source': 'heuristic'}
        use_llm = bool(getattr(self.settings, 'memory_share_use_llm', True)) and self.llm.status().get('active_mode') == 'real_llm'
        if use_llm:
            try:
                result = await self.llm.chat([
                    {'role': 'system', 'content': '你是多智能体系统的记忆路由器。判断一条 Agent 私有记忆是否需要进入公共记忆池。只有跨角色协作、公共风险、架构决策、接口契约、知识库更新、任务阻塞、可复用经验才共享；个人隐私、玩家私人偏好、低层移动日志不要共享。只输出 JSON：{"share":true/false,"reason":"...","confidence":0-1}。'},
                    {'role': 'user', 'content': json.dumps({'npc_id': npc_id, 'npc_name': npc_name, 'role': role, 'owner': player_name, 'kind': kind, 'importance': importance, 'memory': text[:1600]}, ensure_ascii=False)}
                ], temperature=0.05, max_tokens=180, response_format={'type': 'json_object'}, call_purpose=f'memory_share:{npc_id}')
                obj = self._extract_json(result.text)
                return {'share': bool(obj.get('share')), 'reason': str(obj.get('reason') or 'LLM判定'), 'confidence': self._confidence(obj.get('confidence')), 'source': 'llm'}
            except Exception:
                pass
        return self._heuristic_share(text, importance=importance, kind=kind, owner=player_name)

    def search_context(self, *, npc_id: str, query: str, player_name: str = '__office__', private_limit: int = 5, public_limit: int = 5) -> dict[str, list[dict[str, Any]]]:
        private_hits = self.memory.search_memories(npc_id, player_name, query, limit=private_limit)
        public_hits = self.memory.search_memories(self.public_npc_id, self.public_player_name, query, limit=public_limit)
        return {'private_memories': private_hits, 'public_memories': public_hits}

    def public_stats(self) -> dict[str, Any]:
        return self.memory.stats(npc_id=self.public_npc_id, player_name=self.public_player_name)

    def _heuristic_share(self, text: str, *, importance: int, kind: str, owner: str) -> dict[str, Any]:
        keyword_hits = [k for k in self.SHARE_KEYWORDS if k.lower() in text.lower()]
        high_importance = importance >= int(getattr(self.settings, 'memory_share_min_importance', 4) or 4)
        share = bool(keyword_hits) and (high_importance or kind in {'agent_episode', 'tool_result', 'browser_ingest', 'public_candidate', 'safety_refusal'})
        if owner not in {'__office__', self.public_player_name} and not any(k in text for k in ['项目', '任务', '安全', '架构', 'RAG', '工具', '面试']):
            share = False
        return {'share': share, 'reason': f"heuristic keywords={','.join(keyword_hits[:6]) or 'none'} importance={importance} kind={kind}", 'confidence': 0.76 if share else 0.62, 'source': 'heuristic'}

    def _public_content(self, *, npc_id: str, npc_name: str, role: str, source_owner: str, kind: str, content: str, reason: str) -> str:
        who = npc_name or npc_id
        return f'【公共记忆｜来源Agent={who}｜角色={role or "未知"}｜原始owner={source_owner}｜kind={kind}】\n{content[:1800]}\n共享理由：{reason[:300]}'

    @staticmethod
    def _confidence(value: Any) -> float:
        try:
            v = float(value)
            if v > 1:
                v /= 100
            return max(0.0, min(1.0, v))
        except Exception:
            return 0.7

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        raw = str(text or '').strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
        try:
            obj = json.loads(raw)
        except Exception:
            m = re.search(r'\{.*\}', raw, flags=re.S)
            obj = json.loads(m.group(0)) if m else {}
        return obj if isinstance(obj, dict) else {}
