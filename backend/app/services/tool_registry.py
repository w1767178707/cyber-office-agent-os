from __future__ import annotations
import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .audit_log import AuditLogStore
from .agent_message_bus import AgentMessageBus
from .browser_ingestor import BrowserKnowledgeIngestor
from .event_bus import EventBus
from .knowledge_store import KnowledgeStore
from .memory_router import MemoryRouter, PUBLIC_NPC_ID, PUBLIC_PLAYER_NAME
from .memory_store import MemoryStore
from .safety_guard import SafetyGuard
from .task_manager import TaskManager
from .company_resources import CompanyResourceCenter

try:
    from langchain_core.tools import StructuredTool
except Exception:
    StructuredTool = None

@dataclass
class ToolExecutionContext:
    npc_id: str
    npc_name: str = ''
    role: str = ''
    task: dict[str, Any] = field(default_factory=dict)
    tick: int = 0

@dataclass
class ToolSpec:
    name: str
    description: str
    func: Callable[..., Any]
    allowed_roles: set[str] = field(default_factory=set)
    risk: str = 'low'
    requires_approval: bool = False

class ToolPolicy:
    ROLE_TOOL_HINTS = {
        '安全': {'risk_check', 'write_audit_log', 'query_office_events', 'send_agent_message', 'search_knowledge', 'search_public_memory', 'share_memory', 'create_company_document', 'search_internal_resources', 'create_operating_checklist'},
        '算法': {'search_memory', 'search_public_memory', 'search_knowledge', 'standup_summary', 'send_agent_message', 'risk_check', 'browser_search_and_ingest', 'write_code_artifact', 'run_code_artifact', 'create_dynamic_tool', 'run_dynamic_tool', 'search_internal_resources'},
        '产品': {'create_task', 'update_task_status', 'standup_summary', 'send_agent_message', 'search_knowledge', 'search_public_memory', 'browser_search_and_ingest', 'create_company_document', 'create_operating_checklist', 'search_internal_resources'},
        '前端': {'update_task_status', 'query_office_events', 'send_agent_message', 'search_knowledge', 'search_public_memory', 'browser_search_and_ingest', 'write_code_artifact', 'run_code_artifact', 'create_company_document', 'search_internal_resources'},
        '后端': {'query_office_events', 'update_task_status', 'write_audit_log', 'send_agent_message', 'search_knowledge', 'search_public_memory', 'browser_search_and_ingest', 'write_code_artifact', 'run_code_artifact', 'create_dynamic_tool', 'run_dynamic_tool', 'list_dynamic_tools', 'search_internal_resources'},
        'LLMOps': {'query_office_events', 'update_task_status', 'write_audit_log', 'send_agent_message', 'search_knowledge', 'search_public_memory', 'browser_search_and_ingest', 'write_code_artifact', 'run_code_artifact', 'create_dynamic_tool', 'run_dynamic_tool', 'list_dynamic_tools', 'search_internal_resources'},
        '招聘': {'search_knowledge', 'standup_summary', 'send_agent_message', 'create_task', 'search_public_memory', 'browser_search_and_ingest', 'agent_think', 'request_hiring_cycle', 'conduct_behavioral_interview', 'create_company_document', 'create_operating_checklist', 'search_internal_resources'},
        '技术': {'create_task', 'update_task_status', 'query_office_events', 'standup_summary', 'send_agent_message', 'search_knowledge', 'search_public_memory', 'share_memory', 'browser_search_and_ingest', 'agent_think', 'collaborative_judgement', 'scale_company', 'rollback_company_transaction', 'create_company_document', 'write_code_artifact', 'run_code_artifact', 'create_dynamic_tool', 'run_dynamic_tool', 'list_dynamic_tools', 'search_internal_resources', 'create_operating_checklist'},
    }
    ALWAYS_ALLOWED = {'risk_check', 'query_office_events', 'standup_summary', 'send_agent_message', 'search_knowledge', 'search_public_memory', 'agent_think', 'collaborative_judgement', 'search_internal_resources', 'list_dynamic_tools'}

    def allowed(self, spec: ToolSpec, ctx: ToolExecutionContext) -> tuple[bool, str]:
        if spec.requires_approval:
            return False, '该工具需要人工审批，办公室自动运行时不会直接执行。'
        if spec.name in self.ALWAYS_ALLOWED:
            return True, '公共低风险工具允许执行。'
        if not spec.allowed_roles:
            return True, '工具未设置角色限制。'
        role_text = f'{ctx.role} {ctx.npc_name}'
        if any(k in role_text and spec.name in tools for k, tools in self.ROLE_TOOL_HINTS.items()):
            return True, '角色职责匹配。'
        if ctx.npc_id in spec.allowed_roles or ctx.role in spec.allowed_roles:
            return True, '显式角色白名单命中。'
        return False, f'{ctx.role} 不具备自动执行 {spec.name} 的权限。'

class ToolRegistry:
    def __init__(self, *, safety: SafetyGuard, audit: AuditLogStore | None=None, policy: ToolPolicy | None=None):
        self.safety = safety
        self.audit = audit
        self.policy = policy or ToolPolicy()
        self._tools: dict[str, ToolSpec] = {}
        self._aliases: dict[str, str] = {}
        self.dynamic_tool_executor: Callable[..., Any] | None = None
        self.dynamic_tool_lister: Callable[[], list[dict[str, Any]]] | None = None
        self.dynamic_tool_checker: Callable[[str], bool] | None = None
        self.last_traces: list[dict[str, Any]] = []

    def register(self, spec: ToolSpec, aliases: list[str] | None=None) -> None:
        self._tools[spec.name] = spec
        for alias in aliases or []:
            self._aliases[alias.lower()] = spec.name

    def bind_dynamic_tools(self, *, executor: Callable[..., Any], lister: Callable[[], list[dict[str, Any]]], checker: Callable[[str], bool]) -> None:
        self.dynamic_tool_executor = executor
        self.dynamic_tool_lister = lister
        self.dynamic_tool_checker = checker

    def list_tools(self) -> list[dict[str, Any]]:
        builtins = [{'name': s.name, 'description': s.description, 'risk': s.risk, 'requires_approval': s.requires_approval, 'allowed_roles': sorted(s.allowed_roles), 'dynamic': False} for s in self._tools.values()]
        dynamic = []
        if self.dynamic_tool_lister:
            try:
                dynamic = self.dynamic_tool_lister()
            except Exception:
                dynamic = []
        return builtins + dynamic

    def resolve_name(self, requested: str, *, role: str='', task: dict[str, Any] | None=None) -> str:
        text = str(requested or '').strip().lower()
        if text in self._tools:
            return text
        if text in self._aliases:
            return self._aliases[text]
        if self.dynamic_tool_checker and self.dynamic_tool_checker(text):
            return text
        hay = f'{text} {role} {task or {}}'.lower()
        heuristics = [
            (('hire', 'hiring', 'recruit', '招聘', '面试', '候选'), 'request_hiring_cycle'),
            (('interview', 'behavioral', '行为面试'), 'conduct_behavioral_interview'),
            (('scale', '扩张', '扩大', '公司规模'), 'scale_company'),
            (('rollback', '退回', '回滚', '撤销'), 'rollback_company_transaction'),
            (('think', '思考', '判断'), 'agent_think'),
            (('dynamic tool', '自定义工具', '动态工具', '写tool', '写工具', '工具生成'), 'create_dynamic_tool'),
            (('run dynamic', '执行动态工具', '运行动态工具'), 'run_dynamic_tool'),
            (('code', '代码', '脚本', '运行', '测试运行', 'python'), 'write_code_artifact'),
            (('run code', '运行代码', '执行代码', '代码执行'), 'run_code_artifact'),
            (('document', 'doc', 'prd', 'adr', 'runbook', '文档', '规范', '方案', '接口契约'), 'create_company_document'),
            (('checklist', '清单', '验收项', '操作清单'), 'create_operating_checklist'),
            (('resource', '内部资源', '共享资源', '资源库'), 'search_internal_resources'),
            (('browser', 'browse', 'web', 'search_web', '搜索', '浏览器', '资料', '文件'), 'browser_search_and_ingest'),
            (('risk', 'security', 'audit', '安全', '注入', '权限'), 'risk_check'),
            (('public', 'shared', '公共记忆', '共享记忆'), 'search_public_memory'),
            (('share', '共享', '广播'), 'share_memory'),
            (('memory', 'recall', '记忆', '上下文'), 'search_memory'),
            (('rag', 'knowledge', 'policy', '文档', '知识库', '引用'), 'search_knowledge'),
            (('task', 'kanban', '任务', '看板', '验收'), 'update_task_status'),
            (('standup', 'summary', '同步', '站会'), 'standup_summary'),
            (('message', '协作', '请求', '同步'), 'send_agent_message'),
            (('event', 'incident', '监控', '事件'), 'query_office_events'),
        ]
        for keys, name in heuristics:
            if any(k in hay for k in keys):
                return name
        return 'standup_summary'

    async def execute(self, requested_name: str, *, ctx: ToolExecutionContext, args: dict[str, Any] | None=None) -> dict[str, Any]:
        args = dict(args or {})
        name = self.resolve_name(requested_name, role=ctx.role, task=ctx.task)
        spec = self._tools.get(name)
        trace_id = hashlib.md5(f'{ctx.npc_id}-{ctx.tick}-{requested_name}-{time.time()}'.encode()).hexdigest()[:12]
        started = time.perf_counter()
        if not spec and self.dynamic_tool_checker and self.dynamic_tool_checker(name) and self.dynamic_tool_executor:
            verdict = self.safety.tool_allowed('run_dynamic_tool', actor_npc_id=ctx.npc_id, args={'tool_name': name, 'inputs': args})
            if not verdict.allowed:
                result = {'ok': False, 'blocked': True, 'error': verdict.reason, 'risk_level': verdict.risk_level, 'trace_id': trace_id, 'tool_name': name, 'dynamic': True}
                self._audit(trace_id, ctx, name, 'blocked', verdict.risk_level, verdict.reason, args, result, started)
                return result
            data = self.dynamic_tool_executor(ctx=ctx, tool_name=name, inputs=args)
            data = await data if asyncio.iscoroutine(data) else data
            if not isinstance(data, dict):
                data = {'result': data}
            result = {'ok': True, 'tool_name': name, 'trace_id': trace_id, 'risk_level': 'medium', 'dynamic': True, **data}
            self._audit(trace_id, ctx, name, 'ok', 'medium', '动态工具执行', args, result, started)
            return result
        if not spec:
            result = {'ok': False, 'error': f'unknown tool: {requested_name}', 'trace_id': trace_id, 'tool_name': name}
            self._audit(trace_id, ctx, name, 'unknown', 'medium', result['error'], args, result, started)
            return result
        verdict = self.safety.tool_allowed(name, actor_npc_id=ctx.npc_id, args=args)
        if not verdict.allowed:
            result = {'ok': False, 'blocked': True, 'error': verdict.reason, 'risk_level': verdict.risk_level, 'trace_id': trace_id, 'tool_name': name}
            self._audit(trace_id, ctx, name, 'blocked', verdict.risk_level, verdict.reason, args, result, started)
            return result
        allowed, reason = self.policy.allowed(spec, ctx)
        if not allowed:
            result = {'ok': False, 'blocked': True, 'error': reason, 'risk_level': spec.risk, 'trace_id': trace_id, 'tool_name': name}
            self._audit(trace_id, ctx, name, 'blocked', spec.risk, reason, args, result, started)
            return result
        try:
            maybe = spec.func(ctx=ctx, **args)
            data = await maybe if asyncio.iscoroutine(maybe) else maybe
            if not isinstance(data, dict):
                data = {'result': data}
            result = {'ok': True, 'tool_name': name, 'trace_id': trace_id, 'risk_level': spec.risk, **data}
            self._audit(trace_id, ctx, name, 'ok', spec.risk, reason, args, result, started)
            return result
        except Exception as exc:
            result = {'ok': False, 'tool_name': name, 'trace_id': trace_id, 'error': f'{exc.__class__.__name__}: {exc}', 'risk_level': spec.risk}
            self._audit(trace_id, ctx, name, 'error', spec.risk, result['error'], args, result, started)
            return result

    async def execute_plan(self, plan: list[dict[str, Any]], *, ctx: ToolExecutionContext, max_steps: int=4) -> list[dict[str, Any]]:
        traces: list[dict[str, Any]] = []
        for idx, step in enumerate(plan[:max(1, max_steps)]):
            if isinstance(step, str):
                name, args = step, {}
            else:
                name = str(step.get('tool') or step.get('name') or step.get('tool_name') or '')
                args = dict(step.get('args') or {})
                if step.get('query') and 'query' not in args:
                    args['query'] = step.get('query')
            if not name:
                continue
            result = await self.execute(name, ctx=ctx, args=args)
            result['step_index'] = idx
            traces.append(result)
            if result.get('blocked') or result.get('risk_level') == 'high':
                break
        return traces

    def as_langchain_tools(self) -> list[Any]:
        if StructuredTool is None:
            return []
        tools = []
        for spec in self._tools.values():
            def _factory(s: ToolSpec):
                def _run(query: str = '') -> str:
                    return f'{s.name} 是 CyberOffice Runtime 工具；真实执行由 ToolRegistry.execute 注入上下文并完成审计。query={query[:120]}'
                return _run
            tools.append(StructuredTool.from_function(func=_factory(spec), name=spec.name, description=spec.description))
        return tools

    def _audit(self, trace_id: str, ctx: ToolExecutionContext, tool_name: str, status: str, risk_level: str, reason: str, args: dict[str, Any], result: dict[str, Any], started: float) -> None:
        latency_ms = int((time.perf_counter() - started) * 1000)
        compact = {'trace_id': trace_id, 'npc_id': ctx.npc_id, 'npc_name': ctx.npc_name, 'tool_name': tool_name, 'status': status, 'risk_level': risk_level, 'reason': reason, 'args': args, 'result': result, 'latency_ms': latency_ms, 'tick': ctx.tick}
        self.last_traces.append(compact)
        self.last_traces = self.last_traces[-120:]
        if self.audit:
            self.audit.append(trace_id=trace_id, npc_id=ctx.npc_id, tool_name=tool_name, status=status, risk_level=risk_level, reason=reason, args=args, result=result, latency_ms=latency_ms)

def build_default_tool_registry(*, memory: MemoryStore, tasks: TaskManager, events: EventBus, knowledge: KnowledgeStore, message_bus: AgentMessageBus, audit: AuditLogStore, safety: SafetyGuard, memory_router: MemoryRouter | None=None, browser: BrowserKnowledgeIngestor | None=None, company_runtime: Any | None=None, resource_center: CompanyResourceCenter | None=None) -> ToolRegistry:
    registry = ToolRegistry(safety=safety, audit=audit)
    public_npc_id = getattr(memory_router, 'public_npc_id', PUBLIC_NPC_ID)
    public_player_name = getattr(memory_router, 'public_player_name', PUBLIC_PLAYER_NAME)

    def search_memory(*, ctx: ToolExecutionContext, query: str='', player_name: str='__office__', limit: int=5) -> dict[str, Any]:
        owner = player_name or '__office__'
        if owner not in {'__office__'} and owner != ctx.npc_id:
            owner = '__office__'
        rows = memory.search_memories(ctx.npc_id, owner, query or ctx.task.get('title', ''), limit=min(max(1, int(limit)), 8))
        return {'memories': rows, 'scope': 'private_agent_memory', 'summary': f'命中 {len(rows)} 条 {ctx.npc_name} 私有记忆'}

    def search_public_memory(*, ctx: ToolExecutionContext, query: str='', limit: int=5) -> dict[str, Any]:
        rows = memory.search_memories(public_npc_id, public_player_name, query or ctx.task.get('title', '') or ctx.role, limit=min(max(1, int(limit)), 8))
        return {'memories': rows, 'scope': 'public_memory_pool', 'summary': f'公共记忆池命中 {len(rows)} 条共享记忆'}

    async def share_memory(*, ctx: ToolExecutionContext, content: str='', importance: int=4, kind: str='public_candidate') -> dict[str, Any]:
        text = content or f'{ctx.npc_name} 认为当前任务「{ctx.task.get("title", "")}"存在需要共享的上下文。'
        if memory_router:
            routed = await memory_router.remember(npc_id=ctx.npc_id, player_name='__office__', content=text, importance=max(1, min(5, int(importance))), kind=kind, npc_name=ctx.npc_name, role=ctx.role, share_check=True)
            return {'private_memory_id': routed.private_memory_id, 'public_memory_id': routed.public_memory_id, 'shared': routed.shared, 'reason': routed.reason}
        public_id = memory.add_memory(public_npc_id, public_player_name, f'【公共记忆｜来源Agent={ctx.npc_name or ctx.npc_id}】\n{text}', importance=max(3, int(importance)), kind='public_shared')
        return {'public_memory_id': public_id, 'shared': True, 'reason': 'memory_router_unavailable_direct_public_write'}

    def create_task(*, ctx: ToolExecutionContext, title: str='', description: str='', owner_npc_id: str='', priority: int=3, tags: list[str] | None=None) -> dict[str, Any]:
        title = title or f'{ctx.npc_name} 发起的协作任务'
        task = tasks.create_task(title=title[:120], description=(description or f'由 {ctx.npc_name} 通过工具调用创建。')[:1000], owner_npc_id=owner_npc_id or ctx.npc_id, priority=max(1, min(5, int(priority))), tags=tags or ['agent-tool'])
        events.publish('task_created_by_tool', f'{ctx.npc_name} 通过工具创建任务：{task.get("title")}', [ctx.npc_id, task.get('owner_npc_id', '')])
        return {'task': task, 'summary': f'已创建任务 #{task.get("id")}: {task.get("title")}'}

    def update_task_status(*, ctx: ToolExecutionContext, task_id: int | None=None, status: str='doing') -> dict[str, Any]:
        status = status if status in {'todo', 'doing', 'review', 'blocked', 'done'} else 'doing'
        tid = task_id or ctx.task.get('id')
        if not tid:
            return {'updated': False, 'reason': '当前上下文没有 task_id'}
        tasks.update_status(int(tid), status)
        task = tasks.get_task(int(tid)) or {}
        return {'updated': True, 'task': task, 'summary': f'任务 #{tid} 状态更新为 {status}'}

    def query_office_events(*, ctx: ToolExecutionContext, query: str='', limit: int=6) -> dict[str, Any]:
        rows = events.recent(limit=min(max(1, int(limit)), 20), npc_id=ctx.npc_id if query == 'self' else None)
        if query and query != 'self':
            rows = [r for r in rows if query in r.get('content', '') or query in r.get('event_type', '')]
        return {'events': rows, 'summary': f'读取最近 {len(rows)} 条办公室事件'}

    def risk_check(*, ctx: ToolExecutionContext, text: str='') -> dict[str, Any]:
        verdict = safety.inspect(text or ctx.task.get('description') or ctx.task.get('title') or '', actor=ctx.npc_id)
        return {'allowed': verdict.allowed, 'risk_level': verdict.risk_level, 'reason': verdict.reason, 'categories': verdict.categories, 'sanitized_text': verdict.sanitized_text, 'summary': verdict.reason}

    def standup_summary(*, ctx: ToolExecutionContext, limit: int=8) -> dict[str, Any]:
        rows = tasks.list_tasks(limit=min(max(1, int(limit)), 20))
        active = [t for t in rows if t.get('status') in {'doing', 'review', 'blocked'}]
        summary = '；'.join(f"#{t['id']} {t['title']}({t['status']})" for t in active[:6]) or '暂无进行中任务，等待 Agent 自主领取。'
        return {'summary': summary, 'tasks': rows[:limit]}

    def write_audit_log(*, ctx: ToolExecutionContext, content: str='', risk_level: str='low') -> dict[str, Any]:
        events.publish('agent_audit_note', f'{ctx.npc_name} 审计记录：{content[:160]}', [ctx.npc_id])
        return {'written': True, 'content': content[:500], 'risk_level': risk_level, 'summary': '审计日志已写入事件流'}

    def send_agent_message(*, ctx: ToolExecutionContext, to_npc_id: str='', content: str='', message_type: str='request', related_task_id: int | None=None) -> dict[str, Any]:
        msg = message_bus.send(from_npc_id=ctx.npc_id, to_npc_id=to_npc_id, content=content or f'{ctx.npc_name} 请求协作同步。', message_type=message_type, related_task_id=related_task_id or ctx.task.get('id'))
        events.publish('agent_message', f'{ctx.npc_name} 发送协作消息：{msg.get("content", "")[:120]}', [ctx.npc_id, to_npc_id])
        return {'message': msg, 'summary': '协作消息已发送'}

    def search_knowledge(*, ctx: ToolExecutionContext, query: str='', limit: int=4) -> dict[str, Any]:
        hits = knowledge.search(query or ctx.task.get('title') or ctx.role, limit=min(max(1, int(limit)), 8))
        return {'hits': hits, 'summary': f'知识库命中 {len(hits)} 个片段'}

    async def browser_search_and_ingest(*, ctx: ToolExecutionContext, query: str='', limit: int=3) -> dict[str, Any]:
        if not browser:
            return {'ok': False, 'error': 'BrowserKnowledgeIngestor 未初始化', 'summary': '浏览器检索工具不可用'}
        return await browser.search_and_ingest(query=query or ctx.task.get('title') or ctx.role, role=ctx.role, task_title=ctx.task.get('title', ''), npc_id=ctx.npc_id, limit=min(max(1, int(limit)), 6))

    def search_internal_resources(*, ctx: ToolExecutionContext, query: str='', resource_type: str='', limit: int=8) -> dict[str, Any]:
        if not resource_center:
            return {'ok': False, 'summary': '内部共享资源中心未初始化'}
        q = query or ctx.task.get('title') or ctx.role
        hits = resource_center.search_resources(q, limit=min(max(1, int(limit)), 20))
        if resource_type:
            hits = [h for h in hits if h.get('resource_type') == resource_type]
        return {'hits': hits[:limit], 'summary': f'内部共享资源命中 {len(hits[:limit])} 个产物'}

    def create_company_document(*, ctx: ToolExecutionContext, title: str='', document_type: str='adr', topic: str='', context: str='', content: str='') -> dict[str, Any]:
        if not resource_center:
            return {'ok': False, 'summary': '内部共享资源中心未初始化'}
        topic = topic or title or ctx.task.get('title') or ctx.role
        ctx_text = context or ctx.task.get('description') or f'{ctx.npc_name} 围绕当前公司任务自动沉淀文档。'
        return resource_center.create_document(owner_npc_id=ctx.npc_id, title=title or f'{document_type}｜{topic}', document_type=document_type, topic=topic, context=ctx_text, content=content, publish=True, metadata={'created_by_tool': 'create_company_document', 'tick': ctx.tick})

    def create_operating_checklist(*, ctx: ToolExecutionContext, title: str='', topic: str='', items: list[str] | None=None) -> dict[str, Any]:
        if not resource_center:
            return {'ok': False, 'summary': '内部共享资源中心未初始化'}
        topic = topic or title or ctx.task.get('title') or '公司运营检查清单'
        items = items or ['确认目标与 Owner', '检索内部资源与知识库', '执行安全风险检查', '产物写入共享资源', '必要时同步公共记忆池', '准备回滚方案']
        content = '# Operating Checklist｜' + topic + '\n\n' + '\n'.join(f'- [ ] {str(x)[:160]}' for x in items[:20]) + '\n'
        return resource_center.create_document(owner_npc_id=ctx.npc_id, title=title or f'运营检查清单｜{topic}', document_type='test_plan', topic=topic, context=ctx.task.get('description') or '', content=content, publish=True, metadata={'created_by_tool': 'create_operating_checklist', 'tick': ctx.tick})

    def write_code_artifact(*, ctx: ToolExecutionContext, title: str='', purpose: str='', code: str='', language: str='python') -> dict[str, Any]:
        if not resource_center:
            return {'ok': False, 'summary': '内部共享资源中心未初始化'}
        return resource_center.write_code_artifact(owner_npc_id=ctx.npc_id, title=title or f'{ctx.npc_name} 代码工件', purpose=purpose or ctx.task.get('title') or ctx.role, code=code, language=language, publish=True)

    def run_code_artifact(*, ctx: ToolExecutionContext, resource_id: int | None=None, code: str='', stdin: str='', timeout_seconds: float=3.0) -> dict[str, Any]:
        if not resource_center:
            return {'ok': False, 'summary': '内部共享资源中心未初始化'}
        if not resource_id and not code:
            recent = resource_center.list_resources(resource_type='code', owner_npc_id=ctx.npc_id, limit=1) or resource_center.list_resources(resource_type='code', limit=1)
            resource_id = int(recent[0]['id']) if recent else None
        return resource_center.run_code_artifact(owner_npc_id=ctx.npc_id, resource_id=resource_id, code=code, stdin=stdin, timeout_seconds=timeout_seconds, publish=True)

    def list_dynamic_tools(*, ctx: ToolExecutionContext, owner_npc_id: str='', limit: int=20) -> dict[str, Any]:
        if not resource_center:
            return {'ok': False, 'summary': '内部共享资源中心未初始化'}
        tools = resource_center.list_dynamic_tools(owner_npc_id=owner_npc_id or None, limit=min(max(1, int(limit)), 80))
        return {'dynamic_tools': tools, 'summary': f'当前有 {len(tools)} 个动态工具'}

    def create_dynamic_tool(*, ctx: ToolExecutionContext, name: str='', description: str='', tool_kind: str='document_template', spec: dict[str, Any] | None=None) -> dict[str, Any]:
        if not resource_center:
            return {'ok': False, 'summary': '内部共享资源中心未初始化'}
        name = name or f'{ctx.npc_id}_tool_{ctx.tick}'
        description = description or f'{ctx.npc_name} 为任务「{ctx.task.get("title", "自发优化") }」创建的动态工具。'
        return resource_center.create_dynamic_tool(owner_npc_id=ctx.npc_id, name=name, description=description, tool_kind=tool_kind, spec=spec or {})

    def run_dynamic_tool(*, ctx: ToolExecutionContext, tool_name: str='', inputs: dict[str, Any] | None=None) -> dict[str, Any]:
        if not resource_center:
            return {'ok': False, 'summary': '内部共享资源中心未初始化'}
        chosen = tool_name
        if not chosen:
            tools = resource_center.list_dynamic_tools(owner_npc_id=ctx.npc_id, limit=1) or resource_center.list_dynamic_tools(limit=1)
            chosen = tools[0]['name'] if tools else ''
        if not chosen:
            return {'ok': False, 'summary': '没有可运行动态工具'}
        return resource_center.run_dynamic_tool(owner_npc_id=ctx.npc_id, tool_name=chosen, inputs=inputs or {'topic': ctx.task.get('title') or ctx.role, 'context': ctx.task.get('description') or ''})

    async def agent_think(*, ctx: ToolExecutionContext, focus: str='') -> dict[str, Any]:
        if not company_runtime:
            return {'ok': False, 'summary': '公司级思考运行时未初始化'}
        profile = getattr(getattr(company_runtime, 'bindings', None), 'agent_manager', None).get_profile(ctx.npc_id) if getattr(company_runtime, 'bindings', None) and getattr(company_runtime.bindings, 'agent_manager', None) else None
        state = getattr(company_runtime.bindings, 'state_manager', None).get_state(ctx.npc_id) if getattr(company_runtime, 'bindings', None) and getattr(company_runtime.bindings, 'state_manager', None) else {}
        if not profile:
            return {'ok': False, 'summary': f'未找到 Agent {ctx.npc_id}'}
        thought = await company_runtime.think_for_agent(profile=profile, state=state or {}, task=ctx.task or {}, tick=ctx.tick, teammates=company_runtime._teammates(ctx.npc_id))
        return {'thought': thought, 'summary': f'{ctx.npc_name} 完成独立思考与合作判断'}

    async def collaborative_judgement(*, ctx: ToolExecutionContext, topic: str='', partner_npc_id: str='') -> dict[str, Any]:
        content = topic or ctx.task.get('title') or '当前任务需要合作判断'
        if partner_npc_id:
            msg = message_bus.send(from_npc_id=ctx.npc_id, to_npc_id=partner_npc_id, content=f'请求合作判断：{content}', message_type='collaborative_judgement', related_task_id=ctx.task.get('id'))
            events.publish('collaborative_judgement', f'{ctx.npc_name} 请求 {partner_npc_id} 一起判断：{content[:120]}', [ctx.npc_id, partner_npc_id])
            return {'message': msg, 'summary': '合作判断请求已发送'}
        events.publish('collaborative_judgement', f'{ctx.npc_name} 形成合作判断：{content[:160]}', [ctx.npc_id])
        return {'summary': '合作判断已写入事件流', 'topic': content}

    async def request_hiring_cycle(*, ctx: ToolExecutionContext, reason: str='') -> dict[str, Any]:
        if not company_runtime:
            return {'ok': False, 'summary': '公司级招聘运行时未初始化'}
        return await company_runtime.request_hiring_cycle(reason=reason or f'{ctx.npc_name} 思考后认为需要招人')

    async def conduct_behavioral_interview(*, ctx: ToolExecutionContext, candidate_npc_id: str='', reason: str='') -> dict[str, Any]:
        if not company_runtime:
            return {'ok': False, 'summary': '公司级招聘运行时未初始化'}


        return await company_runtime.request_hiring_cycle(reason=reason or f'{ctx.npc_name} 发起行为面试流程')

    async def scale_company(*, ctx: ToolExecutionContext, reason: str='') -> dict[str, Any]:
        if not company_runtime:
            return {'ok': False, 'summary': '公司级扩张运行时未初始化'}
        return await company_runtime.request_expansion_cycle(reason=reason or f'{ctx.npc_name} 判断需要扩大公司规模')

    async def rollback_company_transaction(*, ctx: ToolExecutionContext, transaction_id: int | None=None, reason: str='') -> dict[str, Any]:
        if not company_runtime:
            return {'ok': False, 'summary': '公司级事务运行时未初始化'}
        if not transaction_id:
            recent = company_runtime.transactions(limit=1)
            transaction_id = int(recent[0]['id']) if recent else 0
        if not transaction_id:
            return {'ok': False, 'summary': '没有可回滚事务'}
        return await company_runtime.rollback_transaction(int(transaction_id), reason=reason or f'{ctx.npc_name} 触发自动回滚')

    if resource_center:
        registry.bind_dynamic_tools(
            executor=lambda *, ctx, tool_name, inputs=None: resource_center.run_dynamic_tool(owner_npc_id=ctx.npc_id, tool_name=tool_name, inputs=inputs or {}),
            lister=lambda: resource_center.dynamic_tool_specs_for_registry(),
            checker=lambda name: resource_center.has_dynamic_tool(name),
        )

    registry.register(ToolSpec('search_memory', '只检索当前 Agent 的私有长期记忆；禁止读取其他 Agent 私有记忆。', search_memory, risk='medium'), aliases=['memory_retriever', 'context_memory_search'])
    registry.register(ToolSpec('search_public_memory', '检索公共记忆池中经 LLM/策略判定可共享的团队记忆。', search_public_memory, risk='low'), aliases=['shared_memory_search', 'public_memory'])
    registry.register(ToolSpec('share_memory', '请求将当前 Agent 的关键记忆经过共享判定后写入公共记忆池。', share_memory, risk='medium'), aliases=['memory_share', 'publish_memory'])
    registry.register(ToolSpec('create_task', '在办公室任务看板中创建新任务。', create_task, allowed_roles={'guo_cto', 'tang_pm'}, risk='medium'), aliases=['task_creator', 'kanban_create'])
    registry.register(ToolSpec('update_task_status', '推进或修改当前任务状态。', update_task_status, risk='medium'), aliases=['task_progress', 'kanban_update', 'task_manager'])
    registry.register(ToolSpec('query_office_events', '查询最近办公室事件和 incident。', query_office_events, risk='low'), aliases=['event_reader', 'incident_reader'])
    registry.register(ToolSpec('risk_check', '对玩家输入、任务描述或工具参数做安全风险检查。', risk_check, risk='low'), aliases=['risk_probe_and_incident_triage', 'security_review', 'prompt_injection_guard'])
    registry.register(ToolSpec('standup_summary', '汇总当前看板与站会状态。', standup_summary, risk='low'), aliases=['context_sync_briefing_tool', 'standup_tool'])
    registry.register(ToolSpec('write_audit_log', '写入安全审计备注。', write_audit_log, allowed_roles={'han_security', 'lu_ops'}, risk='medium'), aliases=['audit_writer'])
    registry.register(ToolSpec('send_agent_message', '向其他 Agent 发送协作请求。', send_agent_message, risk='low'), aliases=['agent_message_bus', 'collaboration_tool'])
    registry.register(ToolSpec('search_knowledge', '检索 RAG 知识库并返回引用片段。', search_knowledge, risk='low'), aliases=['rag_search', 'knowledge_retriever'])
    registry.register(ToolSpec('browser_search_and_ingest', '当 Agent 遇到知识缺口/阻塞时，自动浏览器搜索所需资料/文件，抽取内容并写入知识库。', browser_search_and_ingest, risk='medium'), aliases=['browser_tool', 'web_search_ingest', 'search_web_and_ingest'])
    registry.register(ToolSpec('search_internal_resources', '检索公司内部共享资源：文档、代码、运行结果、动态工具规范。', search_internal_resources, risk='low'), aliases=['internal_resource_search', 'resource_search', 'shared_resources'])
    registry.register(ToolSpec('create_company_document', '生成公司运营所需文档，如 PRD、ADR、Runbook、API Contract、测试计划、安全评审，并写入知识库。', create_company_document, risk='medium'), aliases=['write_doc', 'document_writer', 'company_doc_writer'])
    registry.register(ToolSpec('create_operating_checklist', '生成可执行的公司运营/验收检查清单，并写入共享资源与知识库。', create_operating_checklist, risk='low'), aliases=['checklist_writer', 'operating_checklist'])
    registry.register(ToolSpec('write_code_artifact', '让 Agent 写入安全 Python 代码工件；禁止文件/网络/进程/交易相关代码。', write_code_artifact, risk='medium'), aliases=['code_writer', 'write_code', 'python_artifact_writer'])
    registry.register(ToolSpec('run_code_artifact', '在受限 Python 运行器中执行安全代码工件或代码片段，并把运行结果写入共享资源/知识库。', run_code_artifact, risk='medium'), aliases=['code_runner', 'run_code', 'safe_python_runner'])
    registry.register(ToolSpec('list_dynamic_tools', '列出 Agent 已创建的声明式动态工具。', list_dynamic_tools, risk='low'), aliases=['dynamic_tool_list'])
    registry.register(ToolSpec('create_dynamic_tool', '让 Agent 为自己创建声明式动态工具；支持文档模板、清单、决策矩阵、安全 Python 宏和资源检索宏。', create_dynamic_tool, risk='medium'), aliases=['dynamic_tool_creator', 'write_tool', 'make_tool'])
    registry.register(ToolSpec('run_dynamic_tool', '执行已创建的动态工具，生成文档/运行安全代码/检索资源，并将产物沉淀为共享资源。', run_dynamic_tool, risk='medium'), aliases=['dynamic_tool_runner', 'execute_dynamic_tool'])
    registry.register(ToolSpec('agent_think', '让当前 Agent 进行独立思考：观察、私有判断、合作判断、行动意图和回滚计划。', agent_think, risk='low'), aliases=['think', 'agent_judge', '思考'])
    registry.register(ToolSpec('collaborative_judgement', '让当前 Agent 向相关同事发起合作判断，或把合作判断写入事件流。', collaborative_judgement, risk='low'), aliases=['co_judge', 'collaborate', '合作判断'])
    registry.register(ToolSpec('request_hiring_cycle', '由 HR/管理层触发招聘需求评审，系统构建候选 Agent 并进入行为面试。', request_hiring_cycle, allowed_roles={'yin_hr', 'guo_cto', 'tang_pm'}, risk='medium'), aliases=['hire_agent', 'recruit_agent', '招聘'])
    registry.register(ToolSpec('conduct_behavioral_interview', '由 HR 组织行为面试；通过则新 Agent 加入，否则自动进入下一个候选人。', conduct_behavioral_interview, allowed_roles={'yin_hr'}, risk='medium'), aliases=['behavioral_interview', 'interview_candidate', '行为面试'])
    registry.register(ToolSpec('scale_company', '相关 Agent 判断公司需要扩大规模时，自动创建扩张开发任务与可回滚事务。', scale_company, allowed_roles={'guo_cto', 'tang_pm', 'lu_ops'}, risk='medium'), aliases=['expand_company', 'company_scale', '扩大公司'])
    registry.register(ToolSpec('rollback_company_transaction', '回滚最近或指定的公司招聘/扩张事务，撤销动态 Agent 或扩张任务。', rollback_company_transaction, allowed_roles={'guo_cto', 'lu_ops', 'han_security'}, risk='medium'), aliases=['rollback', '退回机制', '回滚'])
    return registry
