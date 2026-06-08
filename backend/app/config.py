from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = 'CyberOffice Agent OS'
    host: str = '127.0.0.1'
    port: int = 8000
    data_dir: str = './data'
    log_dir: str = './logs'
    llm_provider: str = 'deepseek'
    llm_base_url: str = 'https://api.deepseek.com'
    llm_api_key: str = ''
    llm_model: str = 'deepseek-v4-flash'
    llm_timeout: int = 25
    llm_temperature: float = 0.65
    llm_max_tokens: int = 700
    langchain_enabled: bool = True
    langgraph_enabled: bool = True
    rag_enabled: bool = True
    knowledge_dir: str = './knowledge_base'
    cors_allow_origins: str = 'http://127.0.0.1:8000,http://localhost:8000'
    office_use_llm_planner: bool = False
    office_llm_planner_interval: int = 5
    office_move_step: float = 16.0
    office_action_min_ticks: int = 4
    office_action_max_ticks: int = 12
    office_decision_mode: str = 'llm_parallel'
    office_llm_decision_enabled: bool = True
    office_llm_decision_cooldown_ticks: int = 0
    office_llm_decision_budget_per_tick: int = 0
    office_llm_force_budget_per_tick: int = 0
    office_llm_parallel_concurrency: int = 8
    office_llm_decision_timeout_seconds: float = 25.0
    office_llm_decision_temperature: float = 0.72
    office_llm_decision_max_tokens: int = 600
    memory_compact_threshold: int = 72
    memory_compact_keep_recent: int = 24
    memory_compact_batch_size: int = 36
    memory_summary_use_llm: bool = True
    memory_summary_max_tokens: int = 420
    memory_public_npc_id: str = '__public__'
    memory_public_player_name: str = '__shared__'
    memory_share_use_llm: bool = True
    memory_share_min_importance: int = 4
    agent_tool_loop_max_steps: int = 12

    boss_task_mode_enabled: bool = True
    agent_autonomous_resource_enabled: bool = False
    agent_autonomous_doc_interval_ticks: int = 12
    agent_autonomous_code_interval_ticks: int = 18
    agent_autonomous_dynamic_tool_interval_ticks: int = 24
    agent_autonomous_browser_interval_ticks: int = 21
    agent_difficulty_auto_browse: bool = False
    browser_search_enabled: bool = True
    browser_search_provider: str = 'duckduckgo_html'
    browser_search_max_results: int = 4
    browser_fetch_timeout_seconds: float = 8.0
    browser_user_agent: str = 'CyberOfficeAgentOS/1.0 (+https://github.com/) LangChain-RAG-Browser-Ingest'
    company_game_enabled: bool = True
    company_thinking_enabled: bool = True
    company_thinking_use_llm: bool = False
    company_thinking_interval_ticks: int = 2
    company_thinking_agents_per_cycle: int = 12
    company_hiring_enabled: bool = True
    company_hiring_use_llm: bool = False
    company_hiring_interval_ticks: int = 4
    company_hiring_pass_score: int = 74
    company_scale_enabled: bool = True
    company_scale_interval_ticks: int = 9
    company_max_agents: int = 14
    company_resource_dir: str = './company_workspace'
    company_resource_publish_to_memory: bool = True
    dynamic_tools_enabled: bool = True
    safe_code_timeout_seconds: float = 3.0
    dialogue_influence_ttl_ticks: int = 8
    dialogue_interrupt_current_episode: bool = True
    deepseek_api_key: str = ''

    @property
    def knowledge_path(self) -> Path:
        p = Path(self.knowledge_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def company_resource_path(self) -> Path:
        p = Path(self.company_resource_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cors_origins(self) -> list[str]:
        raw = (self.cors_allow_origins or '').strip()
        if not raw or raw == '*':
            return ['*']
        return [x.strip() for x in raw.split(',') if x.strip()]
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def log_path(self) -> Path:
        p = Path(self.log_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

@lru_cache
def get_settings() -> Settings:
    return Settings()
