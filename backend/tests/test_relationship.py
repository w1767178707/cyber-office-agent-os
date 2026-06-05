from pathlib import Path
from app.services.relationship import RelationshipManager

def test_affinity_update_positive(tmp_path: Path):
    mgr = RelationshipManager(tmp_path / 't.sqlite3')
    before = mgr.get_affinity('npc', 'me')
    after = mgr.update_affinity('npc', 'me', '谢谢你，帮我很多，这个项目很棒')
    assert before['score'] == 0
    assert after['score'] > 0
    assert after['interaction_count'] == 1

def test_affinity_update_negative(tmp_path: Path):
    mgr = RelationshipManager(tmp_path / 't.sqlite3')
    after = mgr.update_affinity('npc', 'me', '你这个方案太差劲了')
    assert after['score'] == 0
    assert after['score_delta'] < 0
