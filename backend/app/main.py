from __future__ import annotations
from contextlib import asynccontextmanager
from datetime import datetime
from time import perf_counter
import json
from pathlib import Path
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from .config import get_settings
from .llm import LLMClient
from .models import BrowserIngestRequest, CodeArtifactRequest, CodeRunRequest, CompanyRollbackRequest, CompanyStatusResponse, CompanyTriggerRequest, DialogueRequest, DialogueResponse, DynamicToolCreateRequest, DynamicToolRunRequest, HealthResponse, KnowledgeIngestRequest, KnowledgeSearchRequest, LLMPingRequest, LLMPingResponse, LLMStatusResponse, OfficeMapResponse, RAGAnswerRequest, RAGAnswerResponse, ResourceDocumentRequest, StandupResponse, TaskCreateRequest, TickResponse, BossTaskRequest, BossTaskResponse, BossMissionReportRequest, BossMissionReportResponse
from .services.agent_manager import AgentManager
from .services.app_logger import setup_logger
from .services.event_bus import EventBus
from .services.memory_store import MemoryStore
from .services.memory_compactor import MemoryCompactor
from .services.relationship import RelationshipManager
from .services.state_manager import StateManager
from .services.task_manager import TaskManager
from .services.audit_log import AuditLogStore
from .services.agent_message_bus import AgentMessageBus
from .services.knowledge_store import KnowledgeStore
from .services.safety_guard import SafetyGuard
from .services.tool_registry import build_default_tool_registry
from .services.memory_router import MemoryRouter
from .services.browser_ingestor import BrowserKnowledgeIngestor
from .services.office_map import CANVAS_HEIGHT, CANVAS_WIDTH, list_zones
from .services.office_simulator import OfficeAgentSimulator
from .services.company_runtime import CompanyRuntime
from .services.company_resources import CompanyResourceCenter
from .services.boss_mission import BossMissionService
settings = get_settings()
logger = setup_logger(settings.log_path)
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info('CyberOffice Agent OS started. provider=%s agent_count=%s', settings.llm_provider, state_manager.count())
    event_bus.publish('public', 'CyberOffice 服务已启动，互联网创业公司成员正在进入自主 Agent 调度循环。', [])
    yield

app = FastAPI(title=settings.app_name, description='初创互联网公司办公室多智能体系统：自主移动、任务规划、DeepSeek 对话、记忆、工具轨迹与前端演示。', version='1.0.0', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True, allow_methods=['*'], allow_headers=['*'])
DB_PATH = settings.data_path / 'cyberoffice.sqlite3'
NPC_PATH = Path(__file__).parent / 'data' / 'npcs.json'
memory_store = MemoryStore(DB_PATH)
relationship_manager = RelationshipManager(DB_PATH)
event_bus = EventBus(DB_PATH)
task_manager = TaskManager(DB_PATH)
audit_log = AuditLogStore(DB_PATH)
message_bus = AgentMessageBus(DB_PATH)
knowledge_store = KnowledgeStore(DB_PATH, settings.knowledge_path)
safety_guard = SafetyGuard()
llm_client = LLMClient(settings)
memory_compactor = MemoryCompactor(memory_store, llm_client, settings)
memory_router = MemoryRouter(memory_store, llm_client, settings, safety_guard)
browser_ingestor = BrowserKnowledgeIngestor(knowledge_store, settings, safety_guard)
company_runtime = CompanyRuntime(DB_PATH, llm_client, settings, safety_guard)
resource_center = CompanyResourceCenter(db_path=DB_PATH, workspace_dir=settings.company_resource_path, knowledge=knowledge_store, safety=safety_guard, events=event_bus, memory_router=memory_router)
boss_mission_service = BossMissionService(db_path=DB_PATH, llm=llm_client, tasks=task_manager, events=event_bus, safety=safety_guard, memory_router=memory_router, resource_center=resource_center)
_tmp_agent_loader = AgentManager(NPC_PATH, llm_client, memory_store, relationship_manager, safety=safety_guard)
_dynamic_profiles = company_runtime.load_joined_profiles()
_initial_profiles = list(_tmp_agent_loader.profiles)
for _p in _dynamic_profiles:
    if _p.npc_id not in {x.npc_id for x in _initial_profiles}:
        _initial_profiles.append(_p)
state_manager = StateManager(_initial_profiles)
agent_manager = AgentManager(NPC_PATH, llm_client, memory_store, relationship_manager, state_manager, event_bus, memory_compactor, settings.dialogue_influence_ttl_ticks, settings.dialogue_interrupt_current_episode, safety=safety_guard, memory_router=memory_router)
for _p in _dynamic_profiles:
    agent_manager.add_profile(_p)
tool_registry = build_default_tool_registry(memory=memory_store, tasks=task_manager, events=event_bus, knowledge=knowledge_store, message_bus=message_bus, audit=audit_log, safety=safety_guard, memory_router=memory_router, browser=browser_ingestor, company_runtime=company_runtime, resource_center=resource_center)
office_simulator = OfficeAgentSimulator(agent_manager.profiles, state_manager, event_bus, memory_store, task_manager, llm_client, settings, memory_compactor, tools=tool_registry, memory_router=memory_router, company_runtime=company_runtime, boss_mission_service=boss_mission_service)
company_runtime.bind_runtime(agent_manager=agent_manager, state_manager=state_manager, office_simulator=office_simulator, task_manager=task_manager, event_bus=event_bus, memory_router=memory_router)
FRONTEND_DIR = Path(__file__).resolve().parents[2] / 'frontend'
if FRONTEND_DIR.exists():
    app.mount('/ui/assets', StaticFiles(directory=str(FRONTEND_DIR / 'src')), name='ui-assets')

@app.get('/', include_in_schema=False)
async def root():
    return RedirectResponse(url='/ui')

@app.get('/ui', include_in_schema=False)
async def ui():
    index = FRONTEND_DIR / 'index.html'
    if index.exists():
        return FileResponse(index)
    return {'message': 'frontend/index.html not found', 'docs': '/docs'}

@app.get('/health', response_model=HealthResponse)
async def health():
    return HealthResponse(status='ok', app=settings.app_name, llm_provider=settings.llm_provider, npc_count=state_manager.count(), storage=str(DB_PATH), time=datetime.now().isoformat(timespec='seconds'))

@app.get('/llm/status', response_model=LLMStatusResponse)
async def llm_status():
    payload = llm_client.status()
    payload.update({'office_llm_decision_enabled': settings.office_llm_decision_enabled, 'office_decision_mode': settings.office_decision_mode, 'office_llm_decision_cooldown_ticks': settings.office_llm_decision_cooldown_ticks, 'office_llm_decision_budget_per_tick': settings.office_llm_decision_budget_per_tick, 'office_llm_force_budget_per_tick': settings.office_llm_force_budget_per_tick, 'office_llm_parallel_concurrency': settings.office_llm_parallel_concurrency, 'office_llm_decision_timeout_seconds': settings.office_llm_decision_timeout_seconds, 'office_llm_decision_temperature': settings.office_llm_decision_temperature, 'office_llm_decision_max_tokens': settings.office_llm_decision_max_tokens, 'langchain_enabled': settings.langchain_enabled, 'langchain_available': payload.get('langchain_available', False), 'langgraph_available': payload.get('langgraph_available', False)})
    return payload

@app.post('/llm/ping', response_model=LLMPingResponse)
async def llm_ping(request: LLMPingRequest):
    status = llm_client.status()
    result = await llm_client.chat([{'role': 'system', 'content': '你是 CyberOffice Agent OS 的 LLM 连通性测试助手。请只用中文简短回复。'}, {'role': 'user', 'content': request.message}], temperature=0.2, max_tokens=120)
    return LLMPingResponse(provider=result.provider, active_mode='real_llm' if not result.degraded and status['has_api_key'] else 'mock_fallback', model=result.model, reply=result.text, degraded=result.degraded)

@app.get('/npcs')
async def list_npcs():
    return {'npcs': agent_manager.list_profiles()}

@app.get('/npcs/status')
async def npc_status():
    return {'npcs': state_manager.list_states()}

@app.get('/npcs/{npc_id}/status')
async def single_npc_status(npc_id: str):
    state = state_manager.get_state(npc_id)
    if not state:
        raise HTTPException(status_code=404, detail=f'Agent {npc_id} 不存在')
    return state

@app.post('/dialogue', response_model=DialogueResponse)
async def dialogue(request: DialogueRequest):
    if not agent_manager.has_npc(request.npc_id):
        raise HTTPException(status_code=404, detail=f'Agent {request.npc_id} 不存在')
    if not state_manager.acquire(request.npc_id):
        raise HTTPException(status_code=409, detail=f'Agent {request.npc_id} 正在与其他玩家对话')
    try:
        result = await agent_manager.dialogue(session_id=request.session_id, npc_id=request.npc_id, player_name=request.player_name, player_message=request.player_message)
        logger.info('dialogue npc=%s player=%s affinity=%s/%s latency=%sms', request.npc_id, request.player_name, result['affinity_level'], result['affinity_score'], result['latency_ms'])
        return result
    finally:
        state_manager.release(request.npc_id)

@app.get('/affinity/{npc_id}/{player_name}')
async def get_affinity(npc_id: str, player_name: str):
    if not agent_manager.has_npc(npc_id):
        raise HTTPException(status_code=404, detail=f'Agent {npc_id} 不存在')
    return relationship_manager.get_affinity(npc_id, player_name)

@app.get('/memories')
async def list_memories(npc_id: str | None=None, player_name: str | None=None, limit: int=50, include_compressed: bool=False):
    return {'memories': memory_store.list_memories(npc_id=npc_id, player_name=player_name, limit=min(limit, 200), include_compressed=include_compressed)}

@app.get('/memories/stats')
async def memory_stats(npc_id: str | None=None, player_name: str | None=None):
    payload = memory_store.stats(npc_id=npc_id, player_name=player_name)
    payload.update({'last_summary': memory_compactor.last_summary, 'compact_threshold': settings.memory_compact_threshold, 'keep_recent': settings.memory_compact_keep_recent, 'batch_size': settings.memory_compact_batch_size})
    return payload

@app.get('/memories/public')
async def public_memories(query: str='', limit: int=50):
    hits = memory_store.search_memories(memory_router.public_npc_id, memory_router.public_player_name, query, limit=min(limit, 200))
    return {'public_npc_id': memory_router.public_npc_id, 'public_player_name': memory_router.public_player_name, 'stats': memory_router.public_stats(), 'memories': hits, 'last_share_decisions': memory_router.last_decisions[-20:]}

@app.post('/memories/compact-all')
async def compact_all_memories():
    summaries = await memory_compactor.compact_all_over_limit(threshold=settings.memory_compact_threshold)
    return {'summaries': summaries, 'count': len(summaries)}

@app.get('/events')
async def list_events(limit: int=20):
    return {'events': event_bus.recent(limit=min(limit, 100))}

@app.post('/simulate/tick', response_model=TickResponse)
async def simulate_tick(use_llm_planner: bool | None=None):
    return await office_simulator.tick(use_llm_planner=use_llm_planner)

@app.get('/office/map', response_model=OfficeMapResponse)
async def office_map():
    return OfficeMapResponse(zones=list_zones(), canvas_width=CANVAS_WIDTH, canvas_height=CANVAS_HEIGHT)

@app.post('/boss/tasks', response_model=BossTaskResponse)
async def boss_create_task(request: BossTaskRequest):
    result = await boss_mission_service.submit_mission(
        boss_name=request.boss_name,
        title=request.title,
        description=request.description,
        desired_outcome=request.desired_outcome,
        priority=request.priority,
        profiles=agent_manager.profiles,
        auto_dispatch=request.auto_dispatch,
    )
    if result.get('blocked'):
        raise HTTPException(status_code=403, detail=result)
    return result

@app.get('/boss/missions')
async def boss_missions(status: str | None=None, limit: int=50):
    return {'missions': boss_mission_service.list_missions(status=status, limit=limit)}

@app.get('/boss/missions/{mission_id}')
async def boss_mission_detail(mission_id: int):
    mission = boss_mission_service.get_mission(mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail=f'mission {mission_id} 不存在')
    return {'mission': mission}

@app.post('/boss/missions/{mission_id}/report', response_model=BossMissionReportResponse)
async def boss_mission_report(mission_id: int, request: BossMissionReportRequest):
    result = await boss_mission_service.generate_report(mission_id=mission_id, profiles=agent_manager.profiles, force=request.force, note=request.note)
    if not result.get('ok') and result.get('reason'):
        return {'ok': False, 'mission': result.get('mission') or {}, 'report': '', 'resource': {}, 'ready': bool(result.get('ready'))}
    return {'ok': True, 'mission': result.get('mission') or {}, 'report': result.get('report') or '', 'resource': result.get('resource') or {}, 'ready': bool(result.get('ready', True))}

@app.get('/office/tasks')
async def office_tasks(status: str | None=None, owner_npc_id: str | None=None, limit: int=30):
    return {'tasks': task_manager.list_tasks(status=status, owner_npc_id=owner_npc_id, limit=min(limit, 100))}

@app.post('/office/tasks')
async def create_office_task(request: TaskCreateRequest):
    task = task_manager.create_task(request.title, request.description, request.owner_npc_id, request.priority, request.tags)
    event_bus.publish('task_created', f"新增办公室任务：{task['title']}，Owner={task.get('owner_npc_id') or '待分配'}。", [task.get('owner_npc_id', '')])
    return {'task': task}

@app.get('/office/agent-traces')
async def office_agent_traces():
    return office_simulator.last_trace_payload()

@app.get('/office/tools')
async def office_tools():
    return {'tools': tool_registry.list_tools(), 'langchain_tools_available': len(tool_registry.as_langchain_tools())}

@app.get('/office/tool-audit')
async def office_tool_audit(npc_id: str | None=None, limit: int=50):
    return {'logs': audit_log.recent(npc_id=npc_id, limit=limit)}

@app.get('/office/messages')
async def office_messages(limit: int=30):
    return {'messages': message_bus.recent(limit=limit)}

@app.get('/office/browser-runs')
async def office_browser_runs(limit: int=20):
    return {'runs': browser_ingestor.last_runs[-max(1, min(limit, 50)):]}

@app.get('/knowledge/docs')
async def knowledge_docs():
    return {'docs': knowledge_store.list_docs()}

@app.post('/knowledge/ingest')
async def knowledge_ingest(request: KnowledgeIngestRequest):
    result = knowledge_store.ingest_text(title=request.title, source=request.source or 'manual', content=request.content)
    event_bus.publish('knowledge_ingested', f"知识库新增文档：{result['title']}，切分 {result['chunks']} 个片段。", [])
    return result

@app.post('/knowledge/search')
async def knowledge_search(request: KnowledgeSearchRequest):
    return {'hits': knowledge_store.search(request.query, limit=request.limit)}

@app.post('/knowledge/browser-ingest')
async def knowledge_browser_ingest(request: BrowserIngestRequest):
    profile = agent_manager.get_profile(request.npc_id)
    role = request.role or (profile.role if profile else '')
    result = await browser_ingestor.search_and_ingest(query=request.query, role=role, task_title=request.task_title, npc_id=request.npc_id, limit=request.limit)
    event_bus.publish('browser_knowledge_ingest', f"浏览器检索入库：{result.get('query', request.query)[:120]}；{result.get('summary', '')[:120]}", [request.npc_id])
    return result

@app.post('/agent/rag-answer', response_model=RAGAnswerResponse)
async def rag_answer(request: RAGAnswerRequest):
    started = perf_counter()
    profile = agent_manager.get_profile(request.npc_id) or agent_manager.profiles[0]
    hits = knowledge_store.search(request.query, limit=request.limit)
    citations = [{'chunk_id': h['chunk_id'], 'doc_id': h['doc_id'], 'title': h['title'], 'source': h['source'], 'score': h['score'], 'preview': h['content'][:180]} for h in hits]
    context = '\n\n'.join(f"[引用 {i+1}] {h['title']}#{h['chunk_id']}\n{h['content']}" for i, h in enumerate(hits)) or '知识库没有命中片段。'
    if llm_client.status().get('active_mode') == 'real_llm' and hits:
        result = await llm_client.chat([
            {'role': 'system', 'content': f'你是{profile.name}，负责基于企业知识库做 RAG 问答。只能依据给定引用回答；如果引用不足，要明确说明不足。回答中文，最后用「引用：1,2」列出使用的编号。'},
            {'role': 'user', 'content': json.dumps({'question': request.query, 'retrieved_context': context}, ensure_ascii=False)}
        ], temperature=0.2, max_tokens=700, call_purpose=f'rag_answer:{profile.npc_id}')
        answer = result.text if not result.degraded else ''
        degraded = bool(result.degraded)
    else:
        degraded = True
        if hits:
            bullet = '；'.join(h['content'][:120].replace('\n', ' ') for h in hits[:3])
            answer = f'根据知识库检索结果，可以回答：{bullet}。当前未调用真实 LLM，因此这是基于检索片段的本地摘要。引用：' + ','.join(str(i + 1) for i in range(min(3, len(hits))))
        else:
            answer = '知识库没有找到足够相关的片段，建议先上传/补充项目文档后再问。'
    latency_ms = int((perf_counter() - started) * 1000)
    return RAGAnswerResponse(answer=answer, citations=citations, degraded=degraded, latency_ms=latency_ms)

@app.get('/company/status', response_model=CompanyStatusResponse)
async def company_status():
    return company_runtime.status(agent_manager.profiles, task_manager)

@app.get('/company/thoughts')
async def company_thoughts(npc_id: str | None=None, limit: int=50):
    return {'thoughts': company_runtime.recent_thoughts(npc_id=npc_id, limit=limit)}

@app.get('/company/hiring')
async def company_hiring(limit: int=50):
    return {'candidates': company_runtime.candidates(limit=limit)}

@app.get('/company/expansion')
async def company_expansion(limit: int=50):
    return {'projects': company_runtime.expansion_projects(limit=limit)}

@app.get('/company/transactions')
async def company_transactions(limit: int=50):
    return {'transactions': company_runtime.transactions(limit=limit)}

@app.post('/company/trigger-cycle')
async def company_trigger_cycle(request: CompanyTriggerRequest):
    hire = await company_runtime.request_hiring_cycle(reason=request.reason)
    scale = await company_runtime.request_expansion_cycle(reason=request.reason)
    return {'hiring': hire, 'expansion': scale, 'status': company_runtime.status(agent_manager.profiles, task_manager)}

@app.post('/company/rollback/{transaction_id}')
async def company_rollback(transaction_id: int, request: CompanyRollbackRequest):
    return await company_runtime.rollback_transaction(transaction_id, reason=request.reason)

@app.get('/company/resources')
async def company_resources(resource_type: str | None=None, owner_npc_id: str | None=None, query: str='', limit: int=50):
    if query:
        return {'resources': resource_center.search_resources(query, limit=limit)}
    return {'resources': resource_center.list_resources(resource_type=resource_type, owner_npc_id=owner_npc_id, limit=limit)}

@app.post('/company/resources/document')
async def company_resource_document(request: ResourceDocumentRequest):
    result = resource_center.create_document(owner_npc_id=request.npc_id, title=request.title, document_type=request.document_type, topic=request.topic, context=request.context, content=request.content, publish=True, metadata={'api': '/company/resources/document'})
    if result.get('blocked'):
        raise HTTPException(status_code=403, detail=result)
    return result

@app.post('/company/resources/code')
async def company_resource_code(request: CodeArtifactRequest):
    result = resource_center.write_code_artifact(owner_npc_id=request.npc_id, title=request.title, purpose=request.purpose, code=request.code, language=request.language, publish=True)
    if result.get('blocked'):
        raise HTTPException(status_code=403, detail=result)
    return result

@app.post('/company/resources/run-code')
async def company_resource_run_code(request: CodeRunRequest):
    result = resource_center.run_code_artifact(owner_npc_id=request.npc_id, resource_id=request.resource_id, code=request.code, stdin=request.stdin, timeout_seconds=request.timeout_seconds, publish=True)
    if result.get('blocked'):
        raise HTTPException(status_code=403, detail=result)
    return result

@app.get('/company/dynamic-tools')
async def company_dynamic_tools(owner_npc_id: str | None=None, limit: int=50):
    return {'tools': resource_center.list_dynamic_tools(owner_npc_id=owner_npc_id, limit=limit)}

@app.post('/company/dynamic-tools')
async def company_dynamic_tool_create(request: DynamicToolCreateRequest):
    result = resource_center.create_dynamic_tool(owner_npc_id=request.npc_id, name=request.name, description=request.description, tool_kind=request.tool_kind, spec=request.spec)
    if result.get('blocked'):
        raise HTTPException(status_code=403, detail=result)
    return result

@app.post('/company/dynamic-tools/{tool_name}/run')
async def company_dynamic_tool_run(tool_name: str, request: DynamicToolRunRequest):
    result = resource_center.run_dynamic_tool(owner_npc_id=request.npc_id, tool_name=tool_name, inputs=request.inputs)
    if result.get('blocked'):
        raise HTTPException(status_code=403, detail=result)
    return result

@app.get('/office/llm-stats')
async def office_llm_stats():
    payload = llm_client.status()
    payload.update({'tick': office_simulator.tick_count, 'last_traces': office_simulator.last_trace_payload().get('agent_traces', []), 'decision_enabled': settings.office_llm_decision_enabled, 'decision_mode': settings.office_decision_mode, 'decision_cooldown_ticks': settings.office_llm_decision_cooldown_ticks, 'decision_budget_per_tick': settings.office_llm_decision_budget_per_tick, 'force_budget_per_tick': settings.office_llm_force_budget_per_tick, 'parallel_concurrency': settings.office_llm_parallel_concurrency, 'decision_timeout_seconds': settings.office_llm_decision_timeout_seconds, 'decision_temperature': settings.office_llm_decision_temperature, 'decision_max_tokens': settings.office_llm_decision_max_tokens, 'last_parallel_batch': office_simulator.last_trace_payload().get('last_parallel_batch', {}), 'last_tool_traces': tool_registry.last_traces[-12:], 'tool_audit_count': len(audit_log.recent(limit=12)), 'diagnosis': '若界面来源显示 llm_parallel_local_fallback，请查看 last_parallel_batch.errors 与 recent_calls 中的 error 字段。'})
    return payload

@app.get('/office/parallel-decision-stats')
async def office_parallel_decision_stats():
    payload = office_simulator.last_trace_payload().get('last_parallel_batch', {})
    payload = dict(payload)
    payload.update({'tick': office_simulator.tick_count, 'real_requests': llm_client.status().get('real_requests', 0), 'degraded_requests': llm_client.status().get('degraded_requests', 0), 'last_error': llm_client.status().get('last_error', ''), 'recent_calls': llm_client.status().get('recent_calls', [])[-12:], 'errors': payload.get('errors', [])})
    return payload

@app.post('/office/reset-runtime')
async def reset_office_runtime():
    state_manager.reset_all(agent_manager.profiles)
    office_simulator.reset_runtime()
    event_bus.publish('runtime_reset', '办公室运行态已重置；下一轮空闲 Agent 会重新并行请求 DeepSeek 生成 episode。', [])
    return {'ok': True, 'message': 'runtime reset', 'npc_count': state_manager.count()}

@app.get('/office/standup', response_model=StandupResponse)
async def office_standup():
    tasks = task_manager.list_tasks(limit=20)
    states = state_manager.list_states()
    events = event_bus.recent(limit=8)
    doing = [t for t in tasks if t['status'] in {'doing', 'review', 'blocked'}]
    summary = '今日站会：' + '；'.join([f"{t['title']}({t['status']})" for t in doing[:6]])
    if not doing:
        summary = '今日站会：任务池等待 Agent 自动领取，建议先点击自动运行观察 Agent 协作。'
    return StandupResponse(summary=summary, tasks=tasks, npc_states=states, events=events)
if __name__ == '__main__':
    uvicorn.run('app.main:app', host=settings.host, port=settings.port, reload=False)
