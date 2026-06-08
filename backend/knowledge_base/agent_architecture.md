# CyberOffice Agent OS 架构说明

系统采用 FastAPI 提供接口，办公室运行时以 tick 推动多 Agent 状态机。Agent 通过 Observe、Plan、Tool、Act、Reflect 闭环工作，工具调用结果会写入审计日志与长期记忆。

核心能力包括多角色 persona、任务看板、Agent 间消息、长期记忆、RAG 知识检索、安全策略和可视化 Trace。
