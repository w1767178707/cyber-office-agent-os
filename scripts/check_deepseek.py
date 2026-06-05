from __future__ import annotations
import asyncio
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'backend'
sys.path.insert(0, str(BACKEND))
from app.config import get_settings
from app.llm import LLMClient

async def main() -> int:
    settings = get_settings()
    client = LLMClient(settings)
    print('LLM status:', client.status())
    if client.provider == 'deepseek' and (not client.api_key):
        print('ERROR: 未检测到 DEEPSEEK_API_KEY 或 LLM_API_KEY。请先配置 backend/.env。')
        return 2
    result = await client.chat([{'role': 'system', 'content': '你是 CyberOffice Agent OS 的连通性测试助手。'}, {'role': 'user', 'content': '请用一句中文说明 DeepSeek 已经成功接入智能办公室。'}], temperature=0.2, max_tokens=120)
    print('Provider:', result.provider)
    print('Model:', result.model)
    print('Degraded:', result.degraded)
    print('Reply:', result.text)
    return 0 if not result.degraded else 1
if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
