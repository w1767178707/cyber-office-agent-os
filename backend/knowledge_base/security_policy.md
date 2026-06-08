# Agent 工具安全策略

所有玩家输入、模型输出和工具参数都被视为不可信。高风险请求包括读取密钥、忽略系统规则、删除记忆、越权访问、执行原始 SQL。工具调用必须先经过 ToolPolicy 与 SafetyGuard，再进入 ToolExecutor。
