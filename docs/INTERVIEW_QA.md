# 面试问答参考

## Q1：这个项目和普通 ChatBot 有什么区别？

普通 ChatBot 主要处理对话输入和回复输出。本项目把 Agent 放入一个动态办公室环境中，每个 Agent 都有岗位、职责、目标、状态、位置、任务、长期记忆和玩家关系。Agent 不只是回复消息，还会自主移动、执行耗时行为、处理任务、写入事件和记忆。

## Q2：为什么使用行动片段 episode？

如果每一帧都调用 LLM，成本和延迟都会很高，并且会让行为抖动。项目使用 episode 作为行为粒度，让 LLM 只决定下一段行为，本地 runtime 负责移动和执行。这样既能保证智能决策来自 LLM，又能保持前端流畅。

## Q3：并行 LLM 决策如何实现？

后端在每个 tick 中筛选空闲 Agent，然后用 asyncio.gather 同时发起请求，并用 Semaphore 控制最大并发。每个 Agent 都有独立 prompt、独立 timeout 和独立 fallback，避免单个 Agent 卡住整个系统。

## Q4：DeepSeek 返回格式不稳定怎么办？

项目要求模型使用 JSON 输出，但仍然会做 schema repair。系统会修复 duration_ticks、confidence、target_zone、嵌套 decision、自然语言地点等字段。如果 HTTP 成功但字段轻微异常，会标记为 deepseek_episode_repaired，而不是直接丢弃。

## Q5：玩家对话如何影响 Agent 行为？

玩家与 Agent 对话后，AgentManager 会提取 directive、target_zone_hint、influence_score 和 remaining_ticks，并写入 StateManager。下一轮 OfficeAgentSimulator 构建 LLM prompt 时会把 player_influence 作为高优先级上下文。如果指令有效，当前行动会被打断并重新决策。

## Q6：记忆系统如何设计？

项目把对话、移动、到达、执行、完成和玩家影响都写入 SQLite。MemoryStore 支持 FTS 检索，MemoryCompactor 会在记忆过长时生成 memory_summary，并把旧的低层行为记忆标记为 compressed。检索时优先使用未压缩短期记忆和长期摘要。

## Q7：如何保证前端不卡顿？

前端设置请求闸门，避免多个 tick 请求堆积。Canvas 使用本地插值和保守预测，即使 DeepSeek 请求需要数秒，画面也会继续流畅移动。右侧状态面板低频刷新，减少 DOM 压力。

## Q8：如何防止 Agent 行为坍缩？

StateManager 维护能量、压力、社交需求、专注度、recent_zones 和 coffee_streak 等状态。OfficeAgentSimulator 会对重复地点和重复恢复行为做约束，避免所有 Agent 长期聚集到咖啡区。

## Q9：这个项目可以如何优化？

可以加入向量数据库、工具真实执行、WebSocket 推送、前端 React 化、多人协作、真实日程系统、Agent 评估集、权限模型和链路追踪。当前版本已经具备可演示的 Agent 架构闭环。

## Q10：简历中最值得强调什么？

重点强调并行 LLM 决策、玩家对话干预、长短期记忆压缩、行动生命周期、可观测性和前后端完整演示。这些能力比单纯接入一个聊天接口更能体现工程深度。
