from fastapi.testclient import TestClient
from app.main import app

def create_boss_mission(client: TestClient, title: str = '开发一个智能公司模拟游戏') -> dict:
    resp = client.post('/boss/tasks', json={
        'boss_name': '老板',
        'title': title,
        'description': '由老板发布主任务；Agent 需要自主分工，并自行判断是否检索、写文档、写代码、运行代码或创建动态工具。',
        'desired_outcome': '完成可演示闭环，并形成完整任务报告。',
        'priority': 5,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data['ok'] is True
    assert data['subtasks']
    return data

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

def test_boss_mode_starts_empty_then_agents_dispatch_and_tick():
    client = TestClient(app)
    client.post('/office/reset-runtime')
    task_resp = client.get('/office/tasks')
    assert task_resp.status_code == 200



    mission = create_boss_mission(client, title='老板任务：实现任务制公司模拟')
    assert mission['main_task']['tags'][0] == 'boss_mission'
    assert all('boss_subtask' in t['tags'] for t in mission['subtasks'])

    tick_resp = client.post('/simulate/tick')
    assert tick_resp.status_code == 200
    payload = tick_resp.json()
    assert payload['npc_states']
    assert payload['agent_traces']
    first = payload['npc_states'][0]
    assert 'position' in first and 'target_position' in first
    assert first['current_task']
    trace = payload['agent_traces'][0]
    assert 'tool_plan' in trace

    assert trace['decision_source'] in {'local_agent_planner', 'llm_parallel_local_fallback', 'deepseek_episode', 'deepseek_episode_repaired', 'llm_parallel_not_ready_fallback'}

def test_office_map():
    client = TestClient(app)
    resp = client.get('/office/map')
    assert resp.status_code == 200
    data = resp.json()
    assert data['zones']
    assert any((z['zone_id'] == 'meeting_room' for z in data['zones']))

def test_office_no_global_coffee_collapse_after_many_ticks():
    client = TestClient(app)
    create_boss_mission(client, title='老板任务：长期运行稳定性观察')
    payload = None
    for _ in range(12):
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

def test_langchain_tool_and_rag_endpoints():
    client = TestClient(app)
    tools = client.get('/office/tools')
    assert tools.status_code == 200
    names = {t['name'] for t in tools.json()['tools']}
    assert {'search_memory', 'search_knowledge', 'risk_check'} <= names

    create_boss_mission(client, title='老板任务：工具安全审计与 RAG')
    tick = client.post('/simulate/tick')
    assert tick.status_code == 200
    trace = tick.json()['agent_traces'][0]
    assert 'tool_trace' in trace

    docs = client.get('/knowledge/docs')
    assert docs.status_code == 200
    assert docs.json()['docs']

    search = client.post('/knowledge/search', json={'query': '工具安全策略', 'limit': 3})
    assert search.status_code == 200
    assert search.json()['hits']

    answer = client.post('/agent/rag-answer', json={'query': '系统如何做工具安全审计？', 'limit': 3})
    assert answer.status_code == 200
    payload = answer.json()
    assert payload['answer']
    assert 'citations' in payload

def test_full_agent_tool_flow_memory_and_browser_contracts():
    client = TestClient(app)
    tools = client.get('/office/tools').json()['tools']
    names = {t['name'] for t in tools}
    assert {'search_public_memory', 'share_memory', 'browser_search_and_ingest'} <= names

    create_boss_mission(client, title='老板任务：浏览器检索与公共记忆验证')
    tick = client.post('/simulate/tick')
    assert tick.status_code == 200
    traces = tick.json()['agent_traces']
    assert any('tool_plan' in trace for trace in traces)

    public = client.get('/memories/public')
    assert public.status_code == 200
    payload = public.json()
    assert payload['public_npc_id'] == '__public__'
    assert 'last_share_decisions' in payload

    compact = client.post('/memories/compact-all')
    assert compact.status_code == 200
    assert 'count' in compact.json()

    browser_runs = client.get('/office/browser-runs')
    assert browser_runs.status_code == 200
    assert 'runs' in browser_runs.json()

def test_company_game_thinking_hiring_scale_and_rollback():
    client = TestClient(app)
    create_boss_mission(client, title='老板任务：组织扩张与招聘判断')

    tick = client.post('/simulate/tick')
    assert tick.status_code == 200
    traces = tick.json()['agent_traces']
    assert traces
    assert 'thinking_trace' in traces[0]
    assert 'collaboration_judgement' in traces[0]
    assert 'rollback_plan' in traces[0]

    status = client.get('/company/status')
    assert status.status_code == 200
    data = status.json()
    assert data['agent_count'] >= 7
    assert 'recent_thoughts' in data

    tools = client.get('/office/tools').json()['tools']
    names = {t['name'] for t in tools}
    assert {'agent_think', 'request_hiring_cycle', 'conduct_behavioral_interview', 'scale_company', 'rollback_company_transaction'} <= names

    trigger = client.post('/company/trigger-cycle', json={'reason': '测试公司智能循环'})
    assert trigger.status_code == 200
    after_trigger = trigger.json()['status']
    assert 'hiring_pipeline' in after_trigger
    assert 'recent_transactions' in after_trigger

    client.post('/company/trigger-cycle', json={'reason': '继续行为面试'})
    transactions = client.get('/company/transactions').json()['transactions']
    assert transactions
    rollback_target = next((tx for tx in transactions if tx['status'] == 'committed'), None)
    if rollback_target:
        rolled = client.post(f"/company/rollback/{rollback_target['id']}", json={'reason': '测试完整退回机制'})
        assert rolled.status_code == 200
        assert rolled.json()['ok'] is True

def test_company_internal_resources_code_and_dynamic_tools():
    client = TestClient(app)
    tools = client.get('/office/tools').json()['tools']
    names = {t['name'] for t in tools}
    assert {'create_company_document', 'write_code_artifact', 'run_code_artifact', 'create_dynamic_tool', 'run_dynamic_tool', 'search_internal_resources'} <= names

    doc = client.post('/company/resources/document', json={
        'npc_id': 'tang_pm',
        'title': '内部资源系统 PRD',
        'document_type': 'prd',
        'topic': 'Agent 共享资源与知识库',
        'context': '测试 Agent 写文档后进入共享资源和 RAG。'
    })
    assert doc.status_code == 200
    assert doc.json()['ok'] is True
    assert doc.json()['resource']['resource_type'] == 'document'

    code = client.post('/company/resources/code', json={
        'npc_id': 'lu_ops',
        'title': '公司指标计算脚本',
        'purpose': '计算模拟公司质量分',
        'code': 'def score(x):\n    return x + 1\nprint(score(41))'
    })
    assert code.status_code == 200
    code_payload = code.json()
    assert code_payload['ok'] is True
    rid = code_payload['resource']['id']

    run = client.post('/company/resources/run-code', json={'npc_id': 'lu_ops', 'resource_id': rid})
    assert run.status_code == 200
    assert run.json()['run']['stdout'].strip().endswith('42')

    bad = client.post('/company/resources/run-code', json={'npc_id': 'lu_ops', 'code': 'import os\nprint(os.listdir("/"))'})
    assert bad.status_code == 403

    dyn = client.post('/company/dynamic-tools', json={
        'npc_id': 'shen_algo',
        'name': 'rag_eval_checklist',
        'description': '为 RAG 评估生成检查清单',
        'tool_kind': 'checklist',
        'spec': {'template': '# RAG Eval Checklist｜{topic}\n\n{context}\n\n- [ ] 有检索\n- [ ] 有引用\n- [ ] 有拒答边界\n'}
    })
    assert dyn.status_code == 200
    tool_name = dyn.json()['tool_name']
    tools_after = client.get('/office/tools').json()['tools']
    assert tool_name in {t['name'] for t in tools_after}

    dyn_run = client.post(f'/company/dynamic-tools/{tool_name}/run', json={'npc_id': 'shen_algo', 'inputs': {'topic': 'RAG 检索评估', 'context': '动态工具产物需要进入知识库。'}})
    assert dyn_run.status_code == 200
    assert dyn_run.json()['ok'] is True

def test_boss_report_generation_force():
    client = TestClient(app)
    mission = create_boss_mission(client, title='老板任务：形成完整报告')
    mission_id = mission['mission']['id']

    report = client.post(f'/boss/missions/{mission_id}/report', json={'force': True, 'note': '测试阶段强制形成报告。'})
    assert report.status_code == 200
    data = report.json()
    assert data['ok'] is True
    assert '完整任务报告' in data['report']
    assert data['resource'].get('ok') is True
    assert data['resource'].get('resource', {}).get('resource_type') == 'document'
