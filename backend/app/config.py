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
    dialogue_influence_ttl_ticks: int = 8
    dialogue_interrupt_current_episode: bool = True
    deepseek_api_key: str = ''
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
