# CyberOffice Agent OS

CyberOffice Agent OS 是一个基于 FastAPI、DeepSeek、SQLite FTS 和 Canvas 的多智能体办公室模拟系统。项目将场景设定为一家初创互联网公司的办公空间，系统内置 CTO、产品经理、算法工程师、前端工程师、平台工程师、安全合规工程师和组织人才负责人等角色。每个 Agent 都拥有独立 persona、职责、技能、位置、长期记忆、玩家关系和行动状态。

项目重点不是普通聊天机器人，而是一个可运行、可演示、可写进简历的 Agent 应用工程：Agent 会在办公室地图中自主移动，独立并行调用 DeepSeek 生成下一段行为，执行带耗时的行动片段，并把每一步写入记忆流。玩家可以走近任意 Agent 对话，对话内容会影响该 Agent 的后续行为。

## 核心能力

|能力|说明|
|-|-|
|并行 LLM 决策|每个空闲 Agent 会独立调用 DeepSeek 生成下一段行动，后端使用 asyncio 并发执行，避免串行阻塞。|
|长时行动片段|行为不再是瞬时选择，而是 thinking、walking、acting、cooldown 的完整生命周期。|
|丝滑自主移动|后端维护真实坐标，前端使用 Canvas 插值渲染，DeepSeek 返回较慢时仍保持画面连续。|
|玩家干预行为|玩家对话会被提取为 player\_influence，并写入 Agent 状态，下一轮决策会优先响应该影响。|
|长短期记忆|对话、移动、到达、执行、完成、行为影响都会写入 SQLite 记忆流。|
|记忆压缩|记忆过长时自动生成 summary，保留最近细节，压缩旧的低层行为记忆。|
|工具与任务看板|内置工作任务、工具选择、进度推进、站会摘要和事件流。|
|可观测性|提供 LLM 请求统计、并行批次统计、Agent Trace、记忆统计和 Swagger API。|
|降级能力|未配置 DeepSeek Key 时可使用 Mock 模式演示；真实调用失败时会生成安全兜底行为。|

## 技术栈

|层级|技术|
|-|-|
|后端框架|FastAPI、Pydantic、Uvicorn|
|大模型接入|DeepSeek OpenAI-Compatible Chat Completions|
|并发决策|asyncio.gather、asyncio.Semaphore、per-agent timeout|
|存储|SQLite、FTS5 全文检索|
|前端|HTML、CSS、Canvas、原生 JavaScript|
|部署|Docker、Docker Compose|
|测试|pytest|

## 系统架构

```text
browser canvas ui
        |
        v
FastAPI REST API
        |
        +-- AgentManager: 玩家对话、persona prompt、关系更新、行为影响提取
        +-- OfficeAgentSimulator: 并行 LLM 决策、行动片段、移动和执行循环
        +-- StateManager: 坐标、目标点、行为阶段、能量、压力、专注、玩家影响
        +-- MemoryStore: SQLite FTS 记忆写入、检索、压缩标记
        +-- MemoryCompactor: 长记忆摘要生成和持久化
        +-- TaskManager: 办公室任务池、状态推进、动态任务生成
        +-- EventBus: 办公室事件流
        +-- LLMClient: DeepSeek、OpenAI-Compatible Provider、Mock fallback
```

## Agent 行为循环

每个 Agent 在空闲时进入独立决策流程：

```text
Observe -> Retrieve Memory -> Build Prompt -> DeepSeek Decision -> Repair Schema -> Create Episode -> Move -> Act -> Remember -> Compact Memory
```

LLM 负责决定：

```text
下一步要做什么
为什么要做
去哪个办公室区域
使用什么工具
行动持续多少 tick
目标和推理因子是什么
```

本地 runtime 负责执行：

```text
坐标移动
行动耗时
进度更新
能量、压力、社交需求、专注度变化
事件写入
记忆写入和摘要压缩
```

这种设计避免每一帧都调用模型，同时保证每段关键行为由 LLM 决定。

## 办公室角色

|角色|部门|主要职责|
|-|-|-|
|郭北辰|技术中台|架构评审、Agent 规划、工程拆解|
|唐晓鹿|产品增长|用户旅程、产品验收、Demo 转化|
|沈星河|算法应用|Prompt、记忆、评估、上下文工程|
|苏棠|体验工程|前端交互、可视化、演示体验|
|陆云帆|平台工程|LLMOps、后端接口、稳定性治理|
|韩砺|安全合规|Prompt Injection、权限、审计、合规|
|尹清和|组织人才|简历包装、面试问题、项目表达|

## 目录结构

```text
cyber-office-agent-os/
  backend/
    app/
      data/npcs.json
      services/
        agent\_manager.py
        office\_simulator.py
        state\_manager.py
        memory\_store.py
        memory\_compactor.py
        task\_manager.py
        event\_bus.py
        relationship.py
        office\_map.py
        app\_logger.py
      config.py
      llm.py
      main.py
      models.py
    tests/
    requirements.txt
    Dockerfile
    .env.example
    .env.deepseek.example
  frontend/
    index.html
    src/app.js
    src/styles.css
  docs/
    API.md
    INTERVIEW\_QA.md
  scripts/
    check\_deepseek.py
    publish\_github.bat
  docker-compose.yml
  Makefile
  LICENSE
  GITHUB\_UPLOAD.md
```

## 本地运行

### Windows CMD

```bat
cd cyber-office-agent-os\\backend
copy .env.deepseek.example .env
notepad .env
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
python -m app.main
```

打开浏览器：

```text
http://127.0.0.1:8000/ui
```

接口文档：

```text
http://127.0.0.1:8000/docs
```

### PowerShell

```powershell
cd cyber-office-agent-os\\backend
Copy-Item .env.deepseek.example .env
notepad .env
python -m venv .venv
.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
python -m app.main
```

### macOS 或 Linux

```bash
cd cyber-office-agent-os/backend
cp .env.deepseek.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

## DeepSeek 配置

编辑 `backend/.env`：

```env
LLM\_PROVIDER=deepseek
LLM\_BASE\_URL=https://api.deepseek.com
LLM\_MODEL=deepseek-v4-flash
DEEPSEEK\_API\_KEY=sk-your-deepseek-api-key
OFFICE\_DECISION\_MODE=llm\_parallel
OFFICE\_LLM\_DECISION\_ENABLED=true
OFFICE\_LLM\_PARALLEL\_CONCURRENCY=8
OFFICE\_LLM\_DECISION\_TIMEOUT\_SECONDS=25
OFFICE\_LLM\_DECISION\_MAX\_TOKENS=600
```

如果只是本地无 Key 演示，可以改为：

```env
LLM\_PROVIDER=mock
DEEPSEEK\_API\_KEY=
```

## 页面操作

|操作|说明|
|-|-|
|WASD|移动玩家角色|
|靠近成员按 E|打开对话窗口|
|推进 1 分钟|推进一次办公室模拟 tick|
|并行决策一次|触发空闲 Agent 进行 DeepSeek 并行决策|
|开始上班|自动推进模拟|
|站会纪要|查看当前任务和事件摘要|
|重置运行态|清空当前行动状态，保留数据库记忆、任务和事件|

## 玩家如何影响 Agent 行为

当玩家与某个 Agent 对话时，系统会提取行为影响：

```json
{
  "active": true,
  "directive": "请先去安全评审室检查权限控制风险",
  "target\_zone\_hint": "security\_room",
  "influence\_score": 0.85,
  "remaining\_ticks": 8
}
```

如果影响有效，当前行动会被打断，下一轮 DeepSeek 决策会把该影响作为高优先级上下文。适合演示的问题：

```text
请你先去安全评审室检查权限控制风险，并给出一个可量化验收点。
```

```text
请从面试官角度追问这个项目最难的工程点。
```

```text
请帮我把并行 LLM 决策这部分包装成简历亮点。
```

## 记忆系统

记忆类型包括：

|类型|含义|
|-|-|
|dialogue|玩家与 Agent 的对话|
|player\_influence|玩家对 Agent 行为产生的影响|
|agent\_episode\_start|行动片段开始|
|agent\_step\_walk|移动中的行为步骤|
|agent\_step\_arrive|到达目标区域|
|agent\_step\_act|执行动作中的行为步骤|
|agent\_episode|行动完成|
|memory\_summary|压缩后的长期摘要|

压缩流程：

```text
记忆数量超过阈值
保留最近 N 条细节
抽取更旧的行为记忆
调用 DeepSeek 或本地摘要器生成 memory\_summary
旧记忆标记为 compressed
检索时优先使用未压缩记忆和摘要记忆
```

默认配置：

```env
MEMORY\_COMPACT\_THRESHOLD=72
MEMORY\_COMPACT\_KEEP\_RECENT=24
MEMORY\_COMPACT\_BATCH\_SIZE=36
MEMORY\_SUMMARY\_USE\_LLM=true
MEMORY\_SUMMARY\_MAX\_TOKENS=420
```

## API 快速检查

```bat
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/llm/status
curl -X POST http://127.0.0.1:8000/llm/ping -H "Content-Type: application/json" -d "{\\"message\\":\\"请用一句话说明 DeepSeek 已接入成功。\\"}"
curl -X POST "http://127.0.0.1:8000/simulate/tick?use\_llm\_planner=true"
curl http://127.0.0.1:8000/office/parallel-decision-stats
```

## 关键接口

|方法|路径|用途|
|-|-|-|
|GET|`/ui`|浏览器演示界面|
|GET|`/health`|服务健康检查|
|GET|`/llm/status`|LLM Provider 状态和调用统计|
|POST|`/llm/ping`|DeepSeek 连通性测试|
|GET|`/npcs`|查询 Agent 配置|
|GET|`/npcs/status`|查询 Agent 当前状态|
|POST|`/dialogue`|玩家与 Agent 对话|
|GET|`/memories`|查询记忆|
|GET|`/memories/stats`|查询记忆压缩统计|
|GET|`/events`|查询办公室事件|
|POST|`/simulate/tick`|推进模拟|
|GET|`/office/map`|查询办公室地图|
|GET|`/office/tasks`|查询任务看板|
|POST|`/office/tasks`|创建任务|
|GET|`/office/agent-traces`|查询 Agent 决策轨迹|
|GET|`/office/llm-stats`|查询 LLM 决策统计|
|GET|`/office/parallel-decision-stats`|查询最近一次并行决策批次|
|POST|`/office/reset-runtime`|重置运行态|
|GET|`/office/standup`|生成站会摘要|

## Docker 运行

在项目根目录执行：

```bash
docker compose up --build
```

如果需要注入 DeepSeek Key：

```bash
DEEPSEEK\_API\_KEY=sk-your-deepseek-api-key docker compose up --build
```

## 测试

```bash
cd backend
pytest -q
```

