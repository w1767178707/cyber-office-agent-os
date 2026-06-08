# CyberOffice Agent OS API

## Core

| Method | Path | Description |
|---|---|---|
| GET | `/health` | 服务健康检查 |
| GET | `/llm/status` | LLM、LangChain、LangGraph、请求统计 |
| POST | `/llm/ping` | 测试 DeepSeek / Mock 连通性 |
| GET | `/npcs` | Agent persona 列表 |
| GET | `/npcs/status` | 当前 Agent 状态 |
| POST | `/dialogue` | 玩家与 Agent 对话 |

## Office Runtime

| Method | Path | Description |
|---|---|---|
| POST | `/simulate/tick` | 推进一次办公室 tick |
| GET | `/office/map` | 办公室地图区域 |
| GET | `/office/tasks` | 任务看板 |
| POST | `/office/tasks` | 创建任务 |
| GET | `/office/agent-traces` | Agent 决策 Trace，包含 tool_trace / observations / reflection |
| GET | `/office/llm-stats` | LLM 和最近工具调用统计 |
| GET | `/office/parallel-decision-stats` | 并行决策批次统计 |
| POST | `/office/reset-runtime` | 重置运行态 |
| GET | `/office/standup` | 站会摘要 |


## Boss Mission Runtime

| Method | Path | Description |
|---|---|---|
| POST | `/boss/tasks` | 老板发布主任务；系统创建主任务，并由 Agent 团队拆分子任务 |
| GET | `/boss/missions` | 查看老板任务/公司任务列表、任务完成度和报告状态 |
| GET | `/boss/missions/{mission_id}` | 查看单个老板任务详情、子任务和是否可生成报告 |
| POST | `/boss/missions/{mission_id}/report` | 完成后生成完整任务报告，并沉淀为内部共享资源与知识库文档 |

## LangChain Tool Runtime

| Method | Path | Description |
|---|---|---|
| GET | `/office/tools` | 工具注册表和 LangChain tool wrapper 数量 |
| GET | `/office/tool-audit` | 工具调用审计日志 |
| GET | `/office/messages` | Agent 间消息流 |
| GET | `/office/browser-runs` | 浏览器检索/入库运行记录 |


## Company Game Runtime

| Method | Path | Description |
|---|---|---|
| GET | `/company/status` | 公司级状态：规模、任务压力、招聘管线、扩张项目、最近事务 |
| GET | `/company/thoughts` | Agent 思考记录：独立判断、合作判断、招聘/扩张/回滚意图 |
| GET | `/company/hiring` | HR 行为面试与候选 Agent 管线 |
| GET | `/company/expansion` | 公司扩张项目列表 |
| GET | `/company/transactions` | 招聘/扩张事务与可回滚快照 |
| POST | `/company/trigger-cycle` | 手动触发公司智能循环：招聘判断、行为面试、扩张判断 |
| POST | `/company/rollback/{transaction_id}` | 回滚招聘或扩张事务，撤销动态 Agent 或扩张任务 |

## Memory Runtime

| Method | Path | Description |
|---|---|---|
| GET | `/memories` | 查看私有/公共记忆记录，可按 npc_id 与 player_name 过滤 |
| GET | `/memories/public` | 查看公共记忆池、统计和最近共享判定 |
| GET | `/memories/stats` | 查看记忆统计和摘要配置 |
| POST | `/memories/compact-all` | 对所有超过阈值的记忆流执行摘要压缩 |

## RAG Knowledge Base

| Method | Path | Description |
|---|---|---|
| GET | `/knowledge/docs` | 查看知识库文档 |
| POST | `/knowledge/ingest` | 写入知识库文档 |
| POST | `/knowledge/search` | 检索知识库片段 |
| POST | `/knowledge/browser-ingest` | 手动触发浏览器检索资料/文件并写入知识库 |
| POST | `/agent/rag-answer` | 基于检索片段生成带引用回答 |

## Example

```bash
curl -X POST http://127.0.0.1:8000/knowledge/search \
  -H "Content-Type: application/json" \
  -d '{"query":"工具安全策略","limit":3}'
```

```bash
curl -X POST http://127.0.0.1:8000/agent/rag-answer \
  -H "Content-Type: application/json" \
  -d '{"query":"系统如何做工具安全审计？","limit":3}'
```


## Browser ingest example

```bash
curl -X POST http://127.0.0.1:8000/knowledge/browser-ingest \
  -H "Content-Type: application/json" \
  -d '{"query":"LangChain tool calling docs","npc_id":"shen_algo","limit":3}'
```

## Public memory example

```bash
curl "http://127.0.0.1:8000/memories/public?query=工具调用&limit=10"
```

## Company game examples

```bash
curl http://127.0.0.1:8000/company/status
```

```bash
curl -X POST http://127.0.0.1:8000/company/trigger-cycle \
  -H "Content-Type: application/json" \
  -d '{"reason":"演示 HR 行为面试和公司扩张"}'
```

```bash
curl -X POST http://127.0.0.1:8000/company/rollback/1 \
  -H "Content-Type: application/json" \
  -d '{"reason":"演示完整退回机制"}'
```


## Boss mission example

```bash
curl -X POST http://127.0.0.1:8000/boss/tasks \
  -H "Content-Type: application/json" \
  -d '{"boss_name":"老板","title":"开发一个智能公司模拟游戏","description":"让 Agent 自主分工、判断是否调用工具，并在完成后形成报告","desired_outcome":"可演示的完整任务闭环"}'
```

```bash
curl http://127.0.0.1:8000/boss/missions
```

```bash
curl -X POST http://127.0.0.1:8000/boss/missions/1/report \
  -H "Content-Type: application/json" \
  -d '{"force":true,"note":"演示任务报告"}'
```
