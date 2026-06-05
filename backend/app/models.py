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

class TickResponse(BaseModel):
    tick: int
    events: list[OfficeEvent]
    npc_states: list[NPCState]
    agent_traces: list[AgentDecision] = Field(default_factory=list)
    tasks: list[WorkplaceTask] = Field(default_factory=list)

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
