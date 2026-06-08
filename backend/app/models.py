from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field

class Position(BaseModel):
    x: float
    y: float

class OfficeZone(BaseModel):
    zone_id: str
    name: str
    kind: str
    rect: list[float]
    description: str = ''

class NPCProfile(BaseModel):
    npc_id: str
    name: str
    role: str
    title: str = ''
    department: str = ''
    personality: str
    goals: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    speaking_style: str = '自然、简洁、符合角色'
    movement_style: str = '根据任务在办公室内自主移动'
    home_zone: str = 'open_workspace'
    position: Position
    color: str = '#5B8DEF'

class NPCState(BaseModel):
    npc_id: str
    name: str
    role: str
    department: str = ''
    position: Position
    target_position: Position | None = None
    target_zone: str = ''
    location: str = ''
    is_busy: bool = False
    current_action: str = 'idle'
    current_task: str = ''
    mood: str = 'calm'
    energy: int = 100
    plan_summary: str = ''
    selected_tool: str = ''
    last_interaction: datetime | str | None = None
    action_phase: Literal['thinking', 'walking', 'acting', 'cooldown', 'talking'] = 'thinking'
    intention: str = ''
    episode_id: str = ''
    action_started_tick: int = 0
    action_duration_ticks: int = 1
    action_remaining_ticks: int = 0
    action_progress: float = 0.0
    speed: float = 0.0
    velocity: Position = Field(default_factory=lambda: Position(x=0, y=0))
    autonomy: float = 0.8
    stress: int = 20
    social_need: int = 40
    focus: int = 70
    reasoning_factors: list[str] = Field(default_factory=list)
    action_kind: str = 'work'
    recent_zones: list[str] = Field(default_factory=list)
    zone_visit_counts: dict[str, int] = Field(default_factory=dict)
    completed_episodes: int = 0
    coffee_streak: int = 0
    llm_decision_count: int = 0
    last_decision_source: str = ''
    player_influence: dict[str, Any] = Field(default_factory=dict)
    memory_summary_count: int = 0
    last_memory_summary: str = ''

class DialogueRequest(BaseModel):
    player_name: str = Field(default='候选人', min_length=1, max_length=32)
    npc_id: str = Field(..., min_length=1)
    player_message: str = Field(..., min_length=1, max_length=2000)
    session_id: str = Field(default='default')

class RetrievedMemory(BaseModel):
    content: str
    score: float = 0
    importance: int = 1
    created_at: str

class DialogueResponse(BaseModel):
    npc_id: str
    npc_name: str
    npc_reply: str
    affinity_score: int
    affinity_level: str
    score_delta: int
    retrieved_memories: list[RetrievedMemory] = Field(default_factory=list)
    office_events: list[str] = Field(default_factory=list)
    latency_ms: int
    behavior_influence: dict[str, Any] = Field(default_factory=dict)

class AffinityInfo(BaseModel):
    npc_id: str
    player_name: str
    score: int
    level: str
    interaction_count: int
    updated_at: str

class MemoryRecord(BaseModel):
    id: int
    npc_id: str
    player_name: str
    content: str
    importance: int
    kind: str
    created_at: str

class OfficeEvent(BaseModel):
    id: int
    event_type: str
    content: str
    involved_npcs: list[str] = Field(default_factory=list)
    created_at: str

class WorkplaceTask(BaseModel):
    id: int
    title: str
    description: str = ''
    owner_npc_id: str = ''
    status: Literal['todo', 'doing', 'review', 'blocked', 'done'] = 'todo'
    priority: int = 3
    tags: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str

class ToolTraceItem(BaseModel):
    trace_id: str = ''
    tool_name: str = ''
    status: str = ''
    risk_level: str = 'low'
    reason: str = ''
    result: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int = 0

class AgentMessage(BaseModel):
    id: int
    from_npc_id: str
    to_npc_id: str = ''
    message_type: str = 'request'
    content: str
    related_task_id: int | None = None
    status: str = 'open'
    created_at: str

class KnowledgeIngestRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=180)
    content: str = Field(..., min_length=1, max_length=20000)
    source: str = Field(default='manual')

class KnowledgeSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    limit: int = Field(default=5, ge=1, le=20)

class BrowserIngestRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    npc_id: str = Field(default='shen_algo')
    role: str = Field(default='')
    task_title: str = Field(default='')
    limit: int = Field(default=3, ge=1, le=8)

class RAGAnswerRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    npc_id: str = Field(default='shen_algo')
    limit: int = Field(default=5, ge=1, le=12)

class RAGAnswerResponse(BaseModel):
    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    degraded: bool = False
    latency_ms: int = 0

class ResourceDocumentRequest(BaseModel):
    npc_id: str = Field(default='guo_cto')
    title: str = Field(..., min_length=1, max_length=180)
    document_type: str = Field(default='adr', max_length=40)
    topic: str = Field(default='', max_length=220)
    context: str = Field(default='', max_length=4000)
    content: str = Field(default='', max_length=20000)

class CodeArtifactRequest(BaseModel):
    npc_id: str = Field(default='lu_ops')
    title: str = Field(..., min_length=1, max_length=180)
    purpose: str = Field(default='', max_length=1000)
    code: str = Field(default='', max_length=12000)
    language: str = Field(default='python')

class CodeRunRequest(BaseModel):
    npc_id: str = Field(default='lu_ops')
    resource_id: int | None = None
    code: str = Field(default='', max_length=12000)
    stdin: str = Field(default='', max_length=2000)
    timeout_seconds: float = Field(default=3.0, ge=0.5, le=5.0)

class DynamicToolCreateRequest(BaseModel):
    npc_id: str = Field(default='shen_algo')
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(..., min_length=1, max_length=600)
    tool_kind: str = Field(default='document_template', max_length=40)
    spec: dict[str, Any] = Field(default_factory=dict)

class DynamicToolRunRequest(BaseModel):
    npc_id: str = Field(default='shen_algo')
    inputs: dict[str, Any] = Field(default_factory=dict)

class BossTaskRequest(BaseModel):
    boss_name: str = Field(default='老板', min_length=1, max_length=32)
    title: str = Field(..., min_length=1, max_length=160)
    description: str = Field(default='', max_length=6000)
    desired_outcome: str = Field(default='', max_length=3000)
    priority: int = Field(default=5, ge=1, le=5)
    auto_dispatch: bool = True

class BossMissionReportRequest(BaseModel):
    force: bool = False
    note: str = Field(default='', max_length=1000)

class BossTaskResponse(BaseModel):
    ok: bool = True
    mission: dict[str, Any] = Field(default_factory=dict)
    main_task: dict[str, Any] = Field(default_factory=dict)
    subtasks: list[dict[str, Any]] = Field(default_factory=list)
    events: list[int] = Field(default_factory=list)

class BossMissionReportResponse(BaseModel):
    ok: bool = True
    mission: dict[str, Any] = Field(default_factory=dict)
    report: str = ''
    resource: dict[str, Any] = Field(default_factory=dict)
    ready: bool = False

class AgentDecision(BaseModel):
    npc_id: str
    npc_name: str
    thought: str
    goal: str
    selected_tool: str
    action: str
    target_zone: str
    target_position: Position
    task_id: int | None = None
    confidence: float = 0.8
    phase: str = 'thinking'
    intention: str = ''
    action_duration_ticks: int = 1
    action_remaining_ticks: int = 0
    action_progress: float = 0.0
    decision_source: str = 'local_generative'
    reasoning_factors: list[str] = Field(default_factory=list)
    tool_trace: list[ToolTraceItem] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    reflection: str = ''
    tool_plan: list[dict[str, Any]] = Field(default_factory=list)
    thinking_trace: dict[str, Any] = Field(default_factory=dict)
    collaboration_judgement: str = ''
    rollback_plan: str = ''
    company_actions: list[dict[str, Any]] = Field(default_factory=list)

class TickResponse(BaseModel):
    tick: int
    events: list[OfficeEvent]
    npc_states: list[NPCState]
    agent_traces: list[AgentDecision] = Field(default_factory=list)
    tasks: list[WorkplaceTask] = Field(default_factory=list)
    npc_profiles: list[NPCProfile] = Field(default_factory=list)

class OfficeMapResponse(BaseModel):
    zones: list[OfficeZone]
    canvas_width: int = 1080
    canvas_height: int = 620

class TaskCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default='', max_length=1000)
    owner_npc_id: str = ''
    priority: int = Field(default=3, ge=1, le=5)
    tags: list[str] = Field(default_factory=list)

class CompanyRollbackRequest(BaseModel):
    reason: str = Field(default='手动回滚公司事务', max_length=500)

class CompanyTriggerRequest(BaseModel):
    reason: str = Field(default='手动触发公司智能循环', max_length=500)

class CompanyStatusResponse(BaseModel):
    agent_count: int
    max_agents: int
    active_task_count: int
    blocked_task_count: int
    open_candidate_count: int
    hired_dynamic_count: int
    recent_thoughts: list[dict[str, Any]] = Field(default_factory=list)
    hiring_pipeline: list[dict[str, Any]] = Field(default_factory=list)
    expansion_projects: list[dict[str, Any]] = Field(default_factory=list)
    recent_transactions: list[dict[str, Any]] = Field(default_factory=list)
    last_cycle: dict[str, Any] = Field(default_factory=dict)

class StandupResponse(BaseModel):
    summary: str
    tasks: list[WorkplaceTask]
    npc_states: list[NPCState]
    events: list[OfficeEvent]

class HealthResponse(BaseModel):
    status: Literal['ok']
    app: str
    llm_provider: str
    npc_count: int
    storage: str
    time: str

class LLMStatusResponse(BaseModel):
    provider: str
    active_mode: str
    base_url: str
    model: str
    has_api_key: bool
    timeout_seconds: int
    temperature: float
    note: str
    total_requests: int = 0
    real_requests: int = 0
    mock_requests: int = 0
    degraded_requests: int = 0
    last_request_at: str = ''
    last_latency_ms: int = 0
    last_error: str = ''
    last_url: str = ''
    last_call_mode: str = ''
    office_llm_decision_enabled: bool = False
    office_decision_mode: str = 'hybrid'
    office_llm_decision_cooldown_ticks: int = 0
    office_llm_decision_budget_per_tick: int = 0
    office_llm_force_budget_per_tick: int = 0
    office_llm_parallel_concurrency: int = 8
    office_llm_decision_timeout_seconds: float = 9.0
    office_llm_decision_temperature: float = 0.72
    office_llm_decision_max_tokens: int = 280
    langchain_enabled: bool = True
    langchain_available: bool = False
    langgraph_available: bool = False

class LLMPingRequest(BaseModel):
    message: str = Field(default='请用一句话说明你已成功接入 DeepSeek。', min_length=1, max_length=300)

class LLMPingResponse(BaseModel):
    provider: str
    active_mode: str
    model: str
    reply: str
    degraded: bool = False

class ErrorResponse(BaseModel):
    detail: str | dict[str, Any]
