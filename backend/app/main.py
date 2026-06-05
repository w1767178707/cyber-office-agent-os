from __future__ import annotations
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from .config import get_settings
from .llm import LLMClient
from .models import DialogueRequest, DialogueResponse, HealthResponse, LLMPingRequest, LLMPingResponse, LLMStatusResponse, OfficeMapResponse, StandupResponse, TaskCreateRequest, TickResponse
from .services.agent_manager import AgentManager
from .services.app_logger import setup_logger
from .services.event_bus import EventBus
from .services.memory_store import MemoryStore
from .services.memory_compactor import MemoryCompactor
from .services.relationship import RelationshipManager
from .services.state_manager import StateManager
from .services.task_manager import TaskManager
from .services.office_map import CANVAS_HEIGHT, CANVAS_WIDTH, list_zones
from .services.office_simulator import OfficeAgentSimulator
settings = get_settings()
logger = setup_logger(settings.log_path)
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info('CyberOffice Agent OS started. provider=%s agent_count=%s', settings.llm_provider, state_manager.count())
    event_bus.publish('public', 'CyberOffice 服务已启动，互联网创业公司成员正在进入自主 Agent 调度循环。', [])
    yield
app = FastAPI(title=settings.app_name, description='初创互联网公司办公室多智能体系统：自主移动、任务规划、DeepSeek 对话、记忆、工具轨迹与前端演示。', version='1.0.0', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])
DB_PATH = settings.data_path / 'cyberoffice.sqlite3'
NPC_PATH = Path(__file__).parent / 'data' / 'npcs.json'
memory_store = MemoryStore(DB_PATH)
relationship_manager = RelationshipManager(DB_PATH)
event_bus = EventBus(DB_PATH)
task_manager = TaskManager(DB_PATH)
llm_client = LLMClient(settings)
memory_compactor = MemoryCompactor(memory_store, llm_client, settings)
_tmp_agent_loader = AgentManager(NPC_PATH, llm_client, memory_store, relationship_manager)
state_manager = StateManager(_tmp_agent_loader.profiles)
agent_manager = AgentManager(NPC_PATH, llm_client, memory_store, relationship_manager, state_manager, event_bus, memory_compactor, settings.dialogue_influence_ttl_ticks, settings.dialogue_interrupt_current_episode)
office_simulator = OfficeAgentSimulator(agent_manager.profiles, state_manager, event_bus, memory_store, task_manager, llm_client, settings, memory_compactor)
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
    payload.update({'office_llm_decision_enabled': settings.office_llm_decision_enabled, 'office_decision_mode': settings.office_decision_mode, 'office_llm_decision_cooldown_ticks': settings.office_llm_decision_cooldown_ticks, 'office_llm_decision_budget_per_tick': settings.office_llm_decision_budget_per_tick, 'office_llm_force_budget_per_tick': settings.office_llm_force_budget_per_tick, 'office_llm_parallel_concurrency': settings.office_llm_parallel_concurrency, 'office_llm_decision_timeout_seconds': settings.office_llm_decision_timeout_seconds, 'office_llm_decision_temperature': settings.office_llm_decision_temperature, 'office_llm_decision_max_tokens': settings.office_llm_decision_max_tokens})
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

@app.get('/events')
async def list_events(limit: int=20):
    return {'events': event_bus.recent(limit=min(limit, 100))}

@app.post('/simulate/tick', response_model=TickResponse)
async def simulate_tick(use_llm_planner: bool | None=None):
    return await office_simulator.tick(use_llm_planner=use_llm_planner)

@app.get('/office/map', response_model=OfficeMapResponse)
async def office_map():
    return OfficeMapResponse(zones=list_zones(), canvas_width=CANVAS_WIDTH, canvas_height=CANVAS_HEIGHT)

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

@app.get('/office/llm-stats')
async def office_llm_stats():
    payload = llm_client.status()
    payload.update({'tick': office_simulator.tick_count, 'last_traces': office_simulator.last_trace_payload().get('agent_traces', []), 'decision_enabled': settings.office_llm_decision_enabled, 'decision_mode': settings.office_decision_mode, 'decision_cooldown_ticks': settings.office_llm_decision_cooldown_ticks, 'decision_budget_per_tick': settings.office_llm_decision_budget_per_tick, 'force_budget_per_tick': settings.office_llm_force_budget_per_tick, 'parallel_concurrency': settings.office_llm_parallel_concurrency, 'decision_timeout_seconds': settings.office_llm_decision_timeout_seconds, 'decision_temperature': settings.office_llm_decision_temperature, 'decision_max_tokens': settings.office_llm_decision_max_tokens, 'last_parallel_batch': office_simulator.last_trace_payload().get('last_parallel_batch', {}), 'diagnosis': '若界面来源显示 llm_parallel_local_fallback，请查看 last_parallel_batch.errors 与 recent_calls 中的 error 字段。'})
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
