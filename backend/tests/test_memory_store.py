from pathlib import Path
from app.services.memory_store import MemoryStore

def test_memory_add_and_search(tmp_path: Path):
    store = MemoryStore(tmp_path / 'm.sqlite3')
    store.add_memory('npc', 'me', '玩家正在准备 AI Agent 简历项目，需要强调 FastAPI 和长期记忆。', 5)
    rows = store.search_memories('npc', 'me', 'Agent 简历', limit=3)
    assert rows
    assert 'Agent' in rows[0]['content']
