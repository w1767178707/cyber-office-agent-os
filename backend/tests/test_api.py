from fastapi.testclient import TestClient
from app.main import app

def test_health():
    client = TestClient(app)
    resp = client.get('/health')
    assert resp.status_code == 200
    assert resp.json()['status'] == 'ok'

def test_dialogue_mock():
    client = TestClient(app)
    npcs = client.get('/npcs').json()['npcs']
    resp = client.post('/dialogue', json={'player_name': '测试者', 'npc_id': npcs[0]['npc_id'], 'player_message': '你好，请帮我把这个 Agent 项目写进简历。'})
    assert resp.status_code == 200
    data = resp.json()
    assert data['npc_reply']
    assert data['affinity_score'] >= 0

def test_llm_status_and_ping():
    client = TestClient(app)
    status = client.get('/llm/status')
    assert status.status_code == 200
    payload = status.json()
    assert payload['provider'] in {'deepseek', 'mock', 'openai_compatible'}
    assert payload['model']
    ping = client.post('/llm/ping', json={'message': '请用一句话测试 DeepSeek 接入。'})
    assert ping.status_code == 200
    assert ping.json()['reply']

def test_office_autonomous_tick_and_tasks():
    client = TestClient(app)
    task_resp = client.get('/office/tasks')
    assert task_resp.status_code == 200
    assert task_resp.json()['tasks']
    tick_resp = client.post('/simulate/tick')
    assert tick_resp.status_code == 200
    payload = tick_resp.json()
    assert payload['npc_states']
    assert payload['agent_traces']
    first = payload['npc_states'][0]
    assert 'position' in first and 'target_position' in first
    assert first['current_task']

def test_office_map():
    client = TestClient(app)
    resp = client.get('/office/map')
    assert resp.status_code == 200
    data = resp.json()
    assert data['zones']
    assert any((z['zone_id'] == 'meeting_room' for z in data['zones']))

def test_office_no_global_coffee_collapse_after_many_ticks():
    client = TestClient(app)
    payload = None
    for _ in range(60):
        resp = client.post('/simulate/tick')
        assert resp.status_code == 200
        payload = resp.json()
    states = payload['npc_states']
    coffee_count = sum((1 for s in states if s.get('target_zone') == 'coffee_bar'))
    assert coffee_count < len(states)
    assert max((s.get('energy', 0) for s in states)) > 45

def test_office_llm_stats_endpoint():
    client = TestClient(app)
    resp = client.get('/office/llm-stats')
    assert resp.status_code == 200
    data = resp.json()
    assert 'total_requests' in data
    assert data['decision_enabled'] in {True, False}

def test_parallel_decision_stats_endpoint():
    client = TestClient(app)
    resp = client.get('/office/parallel-decision-stats')
    assert resp.status_code == 200
    data = resp.json()
    assert 'requested_agents' in data
    assert 'concurrency' in data
