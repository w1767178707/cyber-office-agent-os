# CyberOffice Agent OS

## 项目背景

CyberOffice Agent OS 是一个模拟真实互联网公司的多智能体办公室游戏。玩家在系统中扮演老板，通过界面发布公司主任务；公司内的 Agent 根据岗位职责自行拆分任务、协作推进、判断是否需要调用工具，并在任务完成后生成完整任务报告。

系统内置 CTO、产品、算法、前端、平台、安全、HR 等角色。每个 Agent 拥有独立状态、私有记忆、任务执行过程、移动位置和工具调用轨迹。公共信息会在判定需要共享后进入公共记忆池，文档、代码、运行结果和任务报告会沉淀为公司内部共享资源，并进入知识库供 RAG 检索使用。

项目后端使用 FastAPI 提供接口，使用 LangChain 相关组件接入 OpenAI-Compatible LLM、消息结构、工具结构与文档切分能力；同时保留自研多 Agent Runtime，用于驱动办公室地图移动、老板任务、招聘入职、公司扩张、事务回滚、记忆隔离、工具审计和前端可视化。

## 目录结构

```text
cyber-office-agent-os-main/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── llm.py
│   │   ├── models.py
│   │   ├── data/npcs.json
│   │   └── services/
│   ├── knowledge_base/
│   ├── tests/
│   ├── requirements.txt
│   ├── .env.example
│   └── .env.deepseek.example
├── frontend/
│   ├── index.html
│   └── src/
├── docs/
├── scripts/
├── docker-compose.yml
└── README.md
```

## 本地运行

### 1. 进入后端目录

```bash
cd cyber-office-agent-os-main/backend
```

### 2. 创建虚拟环境

```bash
python -m venv .venv
source .venv/bin/activate
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置环境变量

无真实 LLM Key 时可以使用本地降级模式：

```bash
cp .env.example .env
```

使用 DeepSeek 时：

```bash
cp .env.deepseek.example .env
```

然后编辑 `.env`：

```env
DEEPSEEK_API_KEY=sk-your-deepseek-api-key
LLM_PROVIDER=deepseek
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LANGCHAIN_ENABLED=true
RAG_ENABLED=true
BOSS_TASK_MODE_ENABLED=true
```

### 5. 启动服务

```bash
python -m app.main
```

默认访问地址：

```text
http://127.0.0.1:8000/ui
```

接口文档地址：

```text
http://127.0.0.1:8000/docs
```

### 6. 运行测试

```bash
pytest -q
```

## Docker 运行

在项目根目录执行：

```bash
docker compose up --build
```

访问：

```text
http://127.0.0.1:8000/ui
```

## 环境变量说明

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `APP_NAME` | `CyberOffice Agent OS` | 应用名称 |
| `HOST` | `0.0.0.0` | 服务监听地址 |
| `PORT` | `8000` | 服务端口 |
| `DATA_DIR` | `./data` | SQLite 数据目录 |
| `LOG_DIR` | `./logs` | 日志目录 |
| `LLM_PROVIDER` | `deepseek` | LLM 提供方标识 |
| `LLM_BASE_URL` | `https://api.deepseek.com` | OpenAI-Compatible API 地址 |
| `LLM_MODEL` | `deepseek-v4-flash` | 默认模型名称 |
| `DEEPSEEK_API_KEY` | 空 | DeepSeek API Key |
| `LLM_API_KEY` | 空 | 通用 API Key，未设置时会读取 `DEEPSEEK_API_KEY` |
| `LLM_TIMEOUT` | `25` | 普通 LLM 请求超时秒数 |
| `LLM_TEMPERATURE` | `0.65` | 普通对话温度 |
| `LLM_MAX_TOKENS` | `700` | 普通对话最大输出长度 |
| `LANGCHAIN_ENABLED` | `true` | 是否启用 LangChain 调用路径 |
| `LANGGRAPH_ENABLED` | `true` | 是否启用 LangGraph 可用性检测 |
| `RAG_ENABLED` | `true` | 是否启用知识库能力 |
| `KNOWLEDGE_DIR` | `./knowledge_base` | 初始知识库目录 |
| `CORS_ALLOW_ORIGINS` | `http://127.0.0.1:8000,http://localhost:8000` | 允许跨域来源 |
| `BOSS_TASK_MODE_ENABLED` | `true` | 是否启用老板主任务模式 |
| `OFFICE_DECISION_MODE` | `llm_parallel` | Agent 决策模式 |
| `OFFICE_LLM_DECISION_ENABLED` | `true` | 是否启用 LLM 决策 |
| `OFFICE_LLM_PARALLEL_CONCURRENCY` | `8` | 并发决策数 |
| `OFFICE_LLM_DECISION_TIMEOUT_SECONDS` | `25` | Agent 决策超时秒数 |
| `OFFICE_LLM_DECISION_TEMPERATURE` | `0.72` | Agent 决策温度 |
| `OFFICE_LLM_DECISION_MAX_TOKENS` | `600` | Agent 决策最大输出长度 |
| `AGENT_TOOL_LOOP_MAX_STEPS` | `12` | 单个 Agent 每轮最多执行的工具步骤 |
| `AGENT_AUTONOMOUS_RESOURCE_ENABLED` | `false` | 是否启用系统节奏式资源生成；老板任务模式下默认关闭 |
| `AGENT_DIFFICULTY_AUTO_BROWSE` | `false` | 是否在困难场景中自动浏览器检索；老板任务模式下由 Agent 决策决定 |
| `BROWSER_SEARCH_ENABLED` | `true` | 是否允许浏览器检索入库工具执行 |
| `BROWSER_SEARCH_PROVIDER` | `duckduckgo_html` | 浏览器检索提供方 |
| `BROWSER_SEARCH_MAX_RESULTS` | `4` | 浏览器检索最大结果数 |
| `BROWSER_FETCH_TIMEOUT_SECONDS` | `8` | 网页抓取超时秒数 |
| `MEMORY_COMPACT_THRESHOLD` | `72` | 单条记忆流超过该数量后触发摘要压缩 |
| `MEMORY_COMPACT_KEEP_RECENT` | `24` | 压缩时保留的近期记忆数量 |
| `MEMORY_PUBLIC_NPC_ID` | `__public__` | 公共记忆池 Agent ID |
| `MEMORY_PUBLIC_PLAYER_NAME` | `__shared__` | 公共记忆池玩家标识 |
| `MEMORY_SHARE_USE_LLM` | `true` | 是否使用 LLM 判断记忆是否共享 |
| `COMPANY_GAME_ENABLED` | `true` | 是否启用公司经营循环 |
| `COMPANY_THINKING_ENABLED` | `true` | 是否启用 Agent 思考记录 |
| `COMPANY_HIRING_ENABLED` | `true` | 是否启用招聘流程 |
| `COMPANY_SCALE_ENABLED` | `true` | 是否启用公司扩张流程 |
| `COMPANY_MAX_AGENTS` | `14` | 公司最大 Agent 数 |
| `COMPANY_RESOURCE_DIR` | `./company_workspace` | 公司共享资源目录 |
| `DYNAMIC_TOOLS_ENABLED` | `true` | 是否允许创建动态工具 |
| `SAFE_CODE_TIMEOUT_SECONDS` | `3.0` | 安全代码执行超时秒数 |
| `DIALOGUE_INFLUENCE_TTL_TICKS` | `8` | 玩家对话影响持续 tick 数 |
| `DIALOGUE_INTERRUPT_CURRENT_EPISODE` | `true` | 对话后是否打断当前 episode |

## 使用手册

### 老板发布主任务

1. 打开 `http://127.0.0.1:8000/ui`。
2. 点击界面中的“发布项目”或“发布主任务”。
3. 填写任务标题、任务背景和期望结果。
4. 提交后系统会创建老板主任务，并由 Agent 团队拆分子任务。
5. 点击“开始经营”或自动运行，让 Agent 在办公室中移动、思考、协作和执行。
6. 在任务完成后点击“生成报告”，系统会形成完整任务报告并写入公司共享资源。

### 查看 Agent 状态

界面左侧或成员面板会显示每个 Agent 的位置、状态、行动阶段、当前任务、工具选择、决策来源、记忆摘要次数和玩家影响信息。新人通过招聘流程加入后会自动显示在地图上，并参与移动与任务循环。

### 与 Agent 对话

1. 在办公室地图中点击某个 Agent。
2. 在对话窗口中输入消息。
3. Agent 会结合角色设定、记忆、好感度、当前公司事件和工具结果进行回复。
4. 对话可能影响 Agent 后续行动，影响会持续若干 tick。

### 运行公司循环

点击“公司循环”可以手动触发招聘与扩张判断。系统会根据公司状态、任务阻塞情况、团队规模和岗位缺口创建候选人、行为面试、扩张项目或事务记录。

### 回滚公司事务

招聘和扩张相关操作会写入事务。需要撤销时调用：

```http
POST /company/rollback/{transaction_id}
```

回滚会撤销动态 Agent、扩张任务、候选人状态或相关项目状态。

### 使用知识库

知识库支持手动写入、浏览器检索入库和 RAG 问答。公司文档、任务报告、代码运行结果和动态工具说明可以进入知识库，Agent 与玩家可以通过检索接口查询。

### 使用共享资源

公司共享资源包括文档、代码、运行结果、动态工具规范、检查清单和任务报告。资源可以通过界面查看，也可以通过 `/company/resources` 查询。

### 使用动态工具

Agent 可以创建声明式动态工具。动态工具只用于公司模拟内部，不允许真实世界交易、支付、采购、下单、凭证读取或危险代码行为。

## API 接口手册

### 基础与页面

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/` | 重定向到 `/ui` |
| `GET` | `/ui` | 返回前端页面 |
| `GET` | `/health` | 服务健康检查 |
| `GET` | `/docs` | FastAPI 自动接口文档 |

#### GET /health

响应字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | string | 固定为 `ok` |
| `app` | string | 应用名称 |
| `llm_provider` | string | 当前 LLM 提供方 |
| `npc_count` | integer | 当前 Agent 数量 |
| `storage` | string | SQLite 文件路径 |
| `time` | string | 当前服务时间 |

### LLM 状态

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/llm/status` | 查看 LLM、LangChain、LangGraph 和 Agent 决策配置状态 |
| `POST` | `/llm/ping` | 发送一句测试消息，验证 LLM 连通性 |

#### POST /llm/ping

请求体：

```json
{
  "message": "请用一句话说明你已成功接入 DeepSeek。"
}
```

响应字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `provider` | string | 调用提供方 |
| `active_mode` | string | `real_llm` 或 `mock_fallback` |
| `model` | string | 模型名称 |
| `reply` | string | 模型回复 |
| `degraded` | boolean | 是否发生降级 |

### Agent 与对话

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/npcs` | 查看所有 Agent 角色配置 |
| `GET` | `/npcs/status` | 查看所有 Agent 实时状态 |
| `GET` | `/npcs/{npc_id}/status` | 查看指定 Agent 实时状态 |
| `POST` | `/dialogue` | 与指定 Agent 对话 |
| `GET` | `/affinity/{npc_id}/{player_name}` | 查看玩家与 Agent 的关系值 |

#### POST /dialogue

请求体：

```json
{
  "player_name": "老板",
  "npc_id": "guo_cto",
  "player_message": "我们要做一个新的 Agent 项目，请你判断技术路线。",
  "session_id": "default"
}
```

响应字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `npc_id` | string | Agent ID |
| `npc_name` | string | Agent 名称 |
| `npc_reply` | string | Agent 回复 |
| `affinity_score` | integer | 关系分 |
| `affinity_level` | string | 关系等级 |
| `score_delta` | integer | 本轮关系变化 |
| `retrieved_memories` | array | 检索到的相关记忆 |
| `office_events` | array | 最近办公室事件 |
| `latency_ms` | integer | 延迟毫秒数 |
| `behavior_influence` | object | 对后续行为的影响 |

### 记忆系统

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/memories` | 查询私有记忆或指定记忆流 |
| `GET` | `/memories/stats` | 查看记忆统计与摘要配置 |
| `GET` | `/memories/public` | 查询公共记忆池 |
| `POST` | `/memories/compact-all` | 对超过阈值的记忆流执行摘要压缩 |

#### GET /memories

查询参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `npc_id` | string | 否 | Agent ID |
| `player_name` | string | 否 | 玩家或记忆流名称 |
| `limit` | integer | 否 | 最大返回数量，最高 200 |
| `include_compressed` | boolean | 否 | 是否包含已压缩记忆 |

#### GET /memories/public

查询参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `query` | string | 否 | 检索关键词 |
| `limit` | integer | 否 | 最大返回数量 |

### 办公室运行

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/events` | 查看办公室事件流 |
| `POST` | `/simulate/tick` | 推进一次办公室模拟循环 |
| `GET` | `/office/map` | 获取办公室地图区域 |
| `GET` | `/office/tasks` | 查询任务列表 |
| `POST` | `/office/tasks` | 手动创建办公室任务 |
| `GET` | `/office/agent-traces` | 查看最近 Agent 决策轨迹 |
| `GET` | `/office/tools` | 查看工具注册表 |
| `GET` | `/office/tool-audit` | 查看工具调用审计 |
| `GET` | `/office/messages` | 查看 Agent 间消息 |
| `GET` | `/office/browser-runs` | 查看浏览器检索入库记录 |
| `GET` | `/office/llm-stats` | 查看 LLM 与工具执行统计 |
| `GET` | `/office/parallel-decision-stats` | 查看并行决策批次统计 |
| `POST` | `/office/reset-runtime` | 重置运行时状态 |
| `GET` | `/office/standup` | 获取站会摘要 |

#### POST /simulate/tick

查询参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `use_llm_planner` | boolean | 否 | 是否使用 LLM planner 覆盖默认设置 |

响应字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `tick` | integer | 当前 tick 数 |
| `events` | array | 最近事件 |
| `npc_states` | array | Agent 实时状态 |
| `agent_traces` | array | Agent 决策与工具调用轨迹 |
| `tasks` | array | 任务列表 |
| `npc_profiles` | array | 当前角色配置，包含动态入职新人 |

#### POST /office/tasks

请求体：

```json
{
  "title": "设计任务验收标准",
  "description": "为老板主任务补充验收清单。",
  "owner_npc_id": "han_pm",
  "priority": 4,
  "tags": ["manual"]
}
```

### 老板主任务

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/boss/tasks` | 老板发布主任务 |
| `GET` | `/boss/missions` | 查看老板任务列表 |
| `GET` | `/boss/missions/{mission_id}` | 查看单个老板任务详情 |
| `POST` | `/boss/missions/{mission_id}/report` | 生成任务报告 |

#### POST /boss/tasks

请求体：

```json
{
  "boss_name": "老板",
  "title": "构建一个智能公司模拟游戏",
  "description": "需要 Agent 自主分工、判断工具调用、形成可视化演示。",
  "desired_outcome": "完成后输出可运行系统和完整任务报告。",
  "priority": 5,
  "auto_dispatch": true
}
```

响应字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | boolean | 是否成功 |
| `mission` | object | 主任务记录 |
| `main_task` | object | 对应主任务看板记录 |
| `subtasks` | array | Agent 拆分出的子任务 |
| `events` | array | 事件 ID 列表 |

#### GET /boss/missions

查询参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | string | 否 | 按状态过滤，例如 `active`、`reported` |
| `limit` | integer | 否 | 最大返回数量 |

#### POST /boss/missions/{mission_id}/report

请求体：

```json
{
  "force": false,
  "note": "请生成完整交付报告。"
}
```

响应字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | boolean | 是否成功 |
| `mission` | object | 主任务记录 |
| `report` | string | 报告正文 |
| `resource` | object | 写入共享资源后的资源信息 |
| `ready` | boolean | 是否满足报告生成条件 |

### 知识库与 RAG

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/knowledge/docs` | 查看知识库文档 |
| `POST` | `/knowledge/ingest` | 手动写入知识库文档 |
| `POST` | `/knowledge/search` | 检索知识库 |
| `POST` | `/knowledge/browser-ingest` | 浏览器检索并写入知识库 |
| `POST` | `/agent/rag-answer` | 基于知识库生成 RAG 回答 |

#### POST /knowledge/ingest

请求体：

```json
{
  "title": "项目接口说明",
  "source": "manual",
  "content": "这里写入需要进入知识库的正文。"
}
```

#### POST /knowledge/search

请求体：

```json
{
  "query": "工具调用审计",
  "limit": 5
}
```

#### POST /knowledge/browser-ingest

请求体：

```json
{
  "query": "LangChain tool calling design",
  "npc_id": "shen_algo",
  "role": "算法工程师",
  "task_title": "工具调用方案调研",
  "limit": 3
}
```

#### POST /agent/rag-answer

请求体：

```json
{
  "query": "当前项目如何保证工具调用安全？",
  "npc_id": "shen_algo",
  "limit": 5
}
```

响应字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `answer` | string | 回答正文 |
| `citations` | array | 引用片段 |
| `degraded` | boolean | 是否降级为本地摘要 |
| `latency_ms` | integer | 延迟毫秒数 |

### 公司经营

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/company/status` | 查看公司状态总览 |
| `GET` | `/company/thoughts` | 查看 Agent 思考记录 |
| `GET` | `/company/hiring` | 查看招聘候选人 |
| `GET` | `/company/expansion` | 查看扩张项目 |
| `GET` | `/company/transactions` | 查看公司事务 |
| `POST` | `/company/trigger-cycle` | 手动触发招聘与扩张判断 |
| `POST` | `/company/rollback/{transaction_id}` | 回滚公司事务 |

#### POST /company/trigger-cycle

请求体：

```json
{
  "reason": "老板手动触发公司智能循环"
}
```

#### POST /company/rollback/{transaction_id}

请求体：

```json
{
  "reason": "撤销本次扩张测试"
}
```

### 公司共享资源

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/company/resources` | 查询共享资源 |
| `POST` | `/company/resources/document` | 创建公司文档资源 |
| `POST` | `/company/resources/code` | 写入代码工件 |
| `POST` | `/company/resources/run-code` | 运行安全代码并保存结果 |

#### GET /company/resources

查询参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `resource_type` | string | 否 | 资源类型过滤 |
| `owner_npc_id` | string | 否 | 资源创建 Agent 过滤 |
| `query` | string | 否 | 检索关键词 |
| `limit` | integer | 否 | 最大返回数量 |

#### POST /company/resources/document

请求体：

```json
{
  "npc_id": "guo_cto",
  "title": "架构决策记录",
  "document_type": "adr",
  "topic": "工具调用审计",
  "context": "需要记录工具调用、权限检查和结果沉淀。",
  "content": ""
}
```

#### POST /company/resources/code

请求体：

```json
{
  "npc_id": "lu_ops",
  "title": "任务统计脚本",
  "purpose": "统计不同状态任务数量",
  "language": "python",
  "code": "tasks = ['todo', 'done', 'done']\nprint({x: tasks.count(x) for x in set(tasks)})"
}
```

#### POST /company/resources/run-code

请求体：

```json
{
  "npc_id": "lu_ops",
  "resource_id": 1,
  "code": "print('hello cyber office')",
  "stdin": "",
  "timeout_seconds": 3
}
```

安全代码运行限制：禁止文件读写、网络访问、进程调用、环境变量读取、`eval`、`exec`、`open`、`__import__`、`subprocess`、`socket`、`requests`、`httpx`、真实交易、真实支付、真实采购和真实下单。

### 动态工具

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/company/dynamic-tools` | 查询动态工具 |
| `POST` | `/company/dynamic-tools` | 创建动态工具 |
| `POST` | `/company/dynamic-tools/{tool_name}/run` | 运行动态工具 |

#### POST /company/dynamic-tools

请求体：

```json
{
  "npc_id": "shen_algo",
  "name": "rag_review_checklist",
  "description": "生成 RAG 评审检查清单",
  "tool_kind": "checklist",
  "spec": {
    "items": ["检索是否命中", "引用是否完整", "回答是否越界"]
  }
}
```

#### POST /company/dynamic-tools/{tool_name}/run

请求体：

```json
{
  "npc_id": "shen_algo",
  "inputs": {
    "topic": "RAG 评审",
    "context": "检查任务报告是否引用知识库资料。"
  }
}
```

支持的动态工具类型包括：

| 类型 | 说明 |
|---|---|
| `document_template` | 根据模板生成文档 |
| `checklist` | 生成检查清单 |
| `decision_matrix` | 生成决策矩阵 |
| `safe_python_macro` | 执行受限 Python 宏 |
| `resource_search_macro` | 检索公司共享资源 |

## 常用 curl 示例

### 健康检查

```bash
curl http://127.0.0.1:8000/health
```

### 发布老板主任务

```bash
curl -X POST http://127.0.0.1:8000/boss/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "boss_name":"老板",
    "title":"构建智能公司模拟游戏",
    "description":"让 Agent 自主分工、调用工具、形成报告。",
    "desired_outcome":"完成可演示版本和完整任务报告。",
    "priority":5,
    "auto_dispatch":true
  }'
```

### 推进一次模拟

```bash
curl -X POST http://127.0.0.1:8000/simulate/tick
```

### 查看任务

```bash
curl http://127.0.0.1:8000/office/tasks
```

### 生成任务报告

```bash
curl -X POST http://127.0.0.1:8000/boss/missions/1/report \
  -H "Content-Type: application/json" \
  -d '{"force":true,"note":"生成阶段性报告"}'
```

### 检索知识库

```bash
curl -X POST http://127.0.0.1:8000/knowledge/search \
  -H "Content-Type: application/json" \
  -d '{"query":"工具调用审计","limit":5}'
```

### 创建公司文档

```bash
curl -X POST http://127.0.0.1:8000/company/resources/document \
  -H "Content-Type: application/json" \
  -d '{
    "npc_id":"guo_cto",
    "title":"工具调用审计 ADR",
    "document_type":"adr",
    "topic":"工具调用审计",
    "context":"记录工具权限、执行和回滚要求。",
    "content":""
  }'
```

## 数据与文件

| 路径 | 说明 |
|---|---|
| `backend/data/cyberoffice.sqlite3` | 运行时 SQLite 数据库 |
| `backend/logs/` | 服务日志目录 |
| `backend/knowledge_base/` | 初始知识库文件目录 |
| `backend/company_workspace/` | 公司共享资源落盘目录 |
| `backend/app/data/npcs.json` | 初始 Agent 角色配置 |

## 安全边界

系统允许公司模拟内部的文档生成、代码工件、受限代码运行、知识库检索、动态工具和任务报告。系统不提供真实世界交易、真实支付、真实采购、真实下单、凭证读取、系统命令执行、任意文件访问、网络扫描或破坏性操作能力。
