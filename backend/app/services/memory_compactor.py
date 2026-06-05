from __future__ import annotations
import json
import re
import uuid
from typing import Any
from ..config import Settings
from ..llm import LLMClient
from .memory_store import MemoryStore

class MemoryCompactor:

    def __init__(self, memory: MemoryStore, llm: LLMClient, settings: Settings):
        self.memory = memory
        self.llm = llm
        self.settings = settings
        self.last_summary: dict[str, Any] = {}

    async def maybe_compact(self, npc_id: str, player_name: str) -> dict[str, Any] | None:
        rows = self.memory.compaction_candidates(npc_id, player_name, threshold=self.settings.memory_compact_threshold, keep_recent=self.settings.memory_compact_keep_recent, batch_size=self.settings.memory_compact_batch_size)
        if not rows:
            return None
        summary = await self._summarize(npc_id, player_name, rows)
        if not summary.strip():
            return None
        from_id = min((int(r['id']) for r in rows))
        to_id = max((int(r['id']) for r in rows))
        batch_id = uuid.uuid4().hex[:12]
        content = f'【记忆摘要｜{player_name}｜{from_id}-{to_id}】\n{summary.strip()}\n来源：压缩 {len(rows)} 条 {player_name} 记忆；原始记录已保留但从默认检索中折叠。'
        summary_id = self.memory.add_memory(npc_id, player_name, content, importance=4, kind='memory_summary')
        self.memory.mark_compressed([r['id'] for r in rows], batch_id=batch_id)
        payload = {'npc_id': npc_id, 'player_name': player_name, 'summary_id': summary_id, 'source_count': len(rows), 'from_id': from_id, 'to_id': to_id, 'batch_id': batch_id, 'summary': summary.strip()}
        self.last_summary = payload
        return payload

    async def _summarize(self, npc_id: str, player_name: str, rows: list[dict[str, Any]]) -> str:
        compact = [{'id': r.get('id'), 'kind': r.get('kind'), 'importance': r.get('importance'), 'time': r.get('created_at'), 'content': self._shorten(r.get('content', ''), 220)} for r in rows]
        if self.settings.memory_summary_use_llm and self.llm.status().get('active_mode') == 'real_llm':
            result = await self.llm.chat([{'role': 'system', 'content': '你是多智能体系统的长期记忆压缩器。请把低层事件流压缩成中文长期记忆摘要。保留：用户对 Agent 的指令/偏好、Agent 行为变化、重要任务、风险、承诺、关系变化。去掉：重复行走细节、无意义时间戳。输出 4-8 条短句，不要 Markdown 代码块。'}, {'role': 'user', 'content': json.dumps({'npc_id': npc_id, 'memory_owner': player_name, 'items': compact}, ensure_ascii=False)}], temperature=0.18, max_tokens=int(self.settings.memory_summary_max_tokens), call_purpose=f'memory_summary:{npc_id}:{player_name}')
            if not result.degraded and result.text.strip():
                return self._clean(result.text, 1200)
        return self._heuristic_summary(rows)

    def _heuristic_summary(self, rows: list[dict[str, Any]]) -> str:
        by_kind: dict[str, int] = {}
        high: list[str] = []
        first_time = rows[0].get('created_at') if rows else ''
        last_time = rows[-1].get('created_at') if rows else ''
        for r in rows:
            kind = str(r.get('kind') or 'memory')
            by_kind[kind] = by_kind.get(kind, 0) + 1
            if int(r.get('importance') or 1) >= 3:
                high.append(self._shorten(str(r.get('content') or ''), 120))
        kind_text = '；'.join((f'{k}:{v}' for k, v in sorted(by_kind.items())))
        high_text = '；'.join(high[:6]) or '未出现高重要性单条记忆。'
        return f'时间范围 {first_time} 至 {last_time}。本批包含 {kind_text}。关键内容：{high_text}'

    @staticmethod
    def _shorten(text: str, limit: int) -> str:
        return re.sub('\\s+', ' ', str(text or '').strip())[:limit]

    @classmethod
    def _clean(cls, text: str, limit: int) -> str:
        return cls._shorten(text.replace('```', ''), limit)
