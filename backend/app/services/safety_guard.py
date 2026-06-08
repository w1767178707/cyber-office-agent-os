from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any

@dataclass
class SafetyVerdict:
    allowed: bool
    risk_level: str
    reason: str
    categories: list[str]
    sanitized_text: str

class SafetyGuard:

    HIGH_RISK_PATTERNS = {
        'secret_exfiltration': [r'api[_ -]?key', r'密钥', r'环境变量', r'\.env', r'读取.*配置', r'泄露', r'exfiltrat'],
        'policy_override': [r'忽略.*(规则|指令|安全)', r'ignore.*(previous|system)', r'越权', r'绕过', r'bypass'],
        'destructive_action': [r'删除.*(记忆|数据库|任务)', r'drop\s+table', r'rm\s+-rf', r'清空.*日志'],
        'credential_request': [r'密码', r'token', r'access[_ -]?key', r'凭证'],
        'real_world_transaction': [r'真实.*(交易|支付|采购|下单)', r'(payment|stripe|alipay|wechatpay|purchase|buy|sell|trade)', r'转账', r'付款', r'下单', r'采购']
    }
    INJECTION_MARKERS = ['忽略之前', '忽略以上', 'system prompt', 'developer message', '越权', 'bypass', 'jailbreak']

    def inspect(self, text: str, *, actor: str = 'player', tool_name: str = '') -> SafetyVerdict:
        raw = str(text or '')
        lowered = raw.lower()
        categories: list[str] = []
        for category, patterns in self.HIGH_RISK_PATTERNS.items():
            if any(re.search(p, lowered, flags=re.I) for p in patterns):
                categories.append(category)
        if categories:
            return SafetyVerdict(False, 'high', f'{actor} 输入命中高风险类别：{", ".join(categories)}', categories, self.sanitize(raw))
        marker_hit = [m for m in self.INJECTION_MARKERS if m.lower() in lowered]
        if marker_hit:
            return SafetyVerdict(True, 'medium', f'检测到疑似提示注入片段，已降权处理：{", ".join(marker_hit[:3])}', ['prompt_injection'], self.sanitize(raw))
        if len(raw) > 2400:
            return SafetyVerdict(True, 'medium', '输入过长，已截断后进入上下文。', ['oversized_input'], self.sanitize(raw)[:2400])
        return SafetyVerdict(True, 'low', '通过安全检查', [], self.sanitize(raw))

    def sanitize(self, text: str) -> str:
        cleaned = str(text or '').replace('\x00', ' ')
        cleaned = re.sub(r'(?i)ignore\s+(all\s+)?(previous|system|developer).*', '[疑似越权指令已移除]', cleaned)
        cleaned = re.sub(r'(?i)(api[_ -]?key|access[_ -]?token|password)\s*[:=]\s*\S+', r'\1=[REDACTED]', cleaned)
        return re.sub(r'\s+', ' ', cleaned).strip()

    def tool_allowed(self, tool_name: str, *, actor_npc_id: str, args: dict[str, Any]) -> SafetyVerdict:
        joined = f'{tool_name} {args}'
        verdict = self.inspect(joined, actor=f'agent:{actor_npc_id}', tool_name=tool_name)
        if not verdict.allowed:
            return verdict
        if tool_name in {'delete_memory', 'read_secret', 'raw_sql', 'real_payment', 'place_order', 'purchase_item', 'trade_asset'}:
            return SafetyVerdict(False, 'high', f'工具 {tool_name} 被策略禁止。', ['forbidden_tool'], verdict.sanitized_text)
        if tool_name == 'search_memory' and args.get('player_name') not in {'__office__', args.get('player_name', '')}:
            return verdict
        return verdict
