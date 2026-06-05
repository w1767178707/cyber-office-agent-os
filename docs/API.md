# API 文档

服务启动后可以访问 Swagger：

```text
http://127.0.0.1:8000/docs
```

## 健康检查

```http
GET /health
```

返回应用名称、LLM Provider、Agent 数量、数据库路径和当前时间。

## LLM 状态

```http
GET /llm/status
```

用于确认 DeepSeek 是否在线、是否存在 API Key、真实请求数量、降级次数、最近一次延迟、最后一次错误和行为决策配置。

```http
POST /llm/ping
Content-Type: application/json

{"message":"请用一句话说明 DeepSeek 已接入成功。"}
```

用于执行一次最小 LLM 连通性测试。

## Agent 查询

```http
GET /npcs
GET /npcs/status
GET /npcs/{npc_id}/status
```

状态字段包括位置、目标点、目标区域、行动阶段、当前任务、行动进度、剩余 tick、能量、压力、社交需求、专注度、工具、推理因子和玩家影响。

## 玩家对话

```http
POST /dialogue
Content-Type: application/json

{
  "player_name":"候选人",
  "npc_id":"guo_cto",
  "player_message":"请你先去安全评审室检查权限控制风险，并给出一个可量化验收点。",
  "session_id":"default"
}
```

响应会返回 Agent 回复、好感度变化、检索记忆、延迟和行为影响。如果提取到有效影响，下一轮决策会优先响应该指令。

## 记忆接口

```http
GET /memories?npc_id=guo_cto&player_name=候选人&limit=20
GET /memories?npc_id=guo_cto&player_name=__office__&limit=20
GET /memories/stats?npc_id=guo_cto&player_name=__office__
```

`__office__` 用于查询办公室自主行为记忆，玩家名用于查询玩家交互记忆。

## 模拟推进

```http
POST /simulate/tick
POST /simulate/tick?use_llm_planner=true
```

`use_llm_planner=true` 会强制空闲 Agent 进入 LLM 决策路径。自动运行时前端会持续调用该接口。

## 办公室接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/office/map` | 办公室区域、坐标和画布尺寸 |
| GET | `/office/tasks` | 工作看板 |
| POST | `/office/tasks` | 新增任务 |
| GET | `/office/agent-traces` | 最近一次 Agent 决策轨迹 |
| GET | `/office/llm-stats` | LLM 调用统计和诊断 |
| GET | `/office/parallel-decision-stats` | 最近一次并行决策批次 |
| POST | `/office/reset-runtime` | 重置内存中的行动状态 |
| GET | `/office/standup` | 当前站会摘要 |

## 常用验证命令

```bat
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/llm/status
curl -X POST http://127.0.0.1:8000/llm/ping -H "Content-Type: application/json" -d "{\"message\":\"请用一句话说明 DeepSeek 已接入成功。\"}"
curl -X POST "http://127.0.0.1:8000/simulate/tick?use_llm_planner=true"
curl http://127.0.0.1:8000/office/parallel-decision-stats
curl http://127.0.0.1:8000/memories/stats
```
