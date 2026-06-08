from __future__ import annotations
import hashlib
import time
from dataclasses import dataclass
from collections import deque
from datetime import datetime
from typing import Any
import httpx

try:
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
    LANGCHAIN_AVAILABLE = True
except Exception:
    ChatOpenAI = None
    HumanMessage = SystemMessage = AIMessage = None
    LANGCHAIN_AVAILABLE = False

try:
    import langgraph
    LANGGRAPH_AVAILABLE = True
except Exception:
    LANGGRAPH_AVAILABLE = False
from .config import Settings
DEEPSEEK_DEFAULT_BASE_URL = 'https://api.deepseek.com'
DEEPSEEK_DEFAULT_MODEL = 'deepseek-v4-flash'

@dataclass
class LLMResult:
    text: str
    raw: dict[str, Any] | None = None
    provider: str = 'mock'
    model: str = 'mock'
    degraded: bool = False
    error: str = ''
    call_purpose: str = ''

class LLMClient:

    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = (settings.llm_provider or 'mock').lower().strip()
        self.base_url = self._resolve_base_url()
        self.api_key = self._resolve_api_key()
        self.model = self._resolve_model()
        self.total_requests = 0
        self.real_requests = 0
        self.mock_requests = 0
        self.degraded_requests = 0
        self.last_request_at = ''
        self.last_latency_ms = 0
        self.last_error = ''
        self.last_url = ''
        self.last_call_mode = 'idle'
        self.recent_calls = deque(maxlen=80)
        self.langchain_available = LANGCHAIN_AVAILABLE
        self.langgraph_available = LANGGRAPH_AVAILABLE
        self._lc_model = None

    def status(self) -> dict[str, Any]:
        real_ready = self.provider in {'deepseek', 'openai_compatible'} and bool(self.api_key)
        return {'provider': self.provider, 'active_mode': 'real_llm' if real_ready else 'mock_fallback', 'base_url': self.base_url, 'model': self.model, 'has_api_key': bool(self.api_key), 'timeout_seconds': self.settings.llm_timeout, 'temperature': self.settings.llm_temperature, 'total_requests': self.total_requests, 'real_requests': self.real_requests, 'mock_requests': self.mock_requests, 'degraded_requests': self.degraded_requests, 'last_request_at': self.last_request_at, 'last_latency_ms': self.last_latency_ms, 'last_error': self.last_error, 'last_url': self.last_url, 'last_call_mode': self.last_call_mode, 'recent_calls': list(self.recent_calls)[-20:], 'langchain_enabled': bool(getattr(self.settings, 'langchain_enabled', True)), 'langchain_available': self.langchain_available, 'langgraph_available': self.langgraph_available, 'note': 'DeepSeek 已接入，当前使用真实 API。' if real_ready and self.provider == 'deepseek' else '已配置为 DeepSeek，但未检测到 API Key，当前自动降级到 Mock。' if self.provider == 'deepseek' else '当前使用本地 Mock 模式。' if self.provider == 'mock' else 'OpenAI-Compatible Provider 已配置；有 Key 时会请求真实模型。'}

    async def chat(self, messages: list[dict[str, str]], temperature: float | None=None, max_tokens: int | None=None, response_format: dict[str, Any] | None=None, call_purpose: str='chat') -> LLMResult:
        self.total_requests += 1
        self.last_request_at = datetime.now().isoformat(timespec='seconds')
        started = time.perf_counter()
        if self.provider in {'deepseek', 'openai_compatible'} and self.api_key:
            if bool(getattr(self.settings, 'langchain_enabled', True)) and LANGCHAIN_AVAILABLE:
                result = await self._langchain_chat(messages, temperature, max_tokens or self.settings.llm_max_tokens, response_format=response_format, call_purpose=call_purpose)
            else:
                result = await self._openai_compatible_chat(messages, temperature, max_tokens or self.settings.llm_max_tokens, response_format=response_format, call_purpose=call_purpose)
        else:
            self.mock_requests += 1
            result = LLMResult(text=self._mock_chat(messages), provider='mock', model='mock', degraded=self.provider != 'mock', error='missing_api_key_or_mock_provider' if self.provider != 'mock' else '', call_purpose=call_purpose)
            self.last_call_mode = 'mock_fallback'
        self.last_latency_ms = int((time.perf_counter() - started) * 1000)
        self._record_call(result, self.last_latency_ms)
        return result

    def _build_langchain_model(self, temperature: float | None=None, max_tokens: int | None=None):
        if not LANGCHAIN_AVAILABLE or ChatOpenAI is None:
            return None
        return ChatOpenAI(
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
            temperature=self.settings.llm_temperature if temperature is None else temperature,
            max_tokens=max_tokens or self.settings.llm_max_tokens,
            timeout=self.settings.llm_timeout,
        )

    async def _langchain_chat(self, messages: list[dict[str, str]], temperature: float | None, max_tokens: int, response_format: dict[str, Any] | None=None, call_purpose: str='chat') -> LLMResult:
        try:
            model = self._build_langchain_model(temperature=temperature, max_tokens=max_tokens)
            if model is None:
                raise RuntimeError('LangChain ChatOpenAI is unavailable')
            kwargs: dict[str, Any] = {}
            if response_format:
                kwargs['response_format'] = response_format
            lc_messages = []
            for m in messages:
                role = m.get('role')
                content = m.get('content', '')
                if role == 'system' and SystemMessage is not None:
                    lc_messages.append(SystemMessage(content=content))
                elif role == 'assistant' and AIMessage is not None:
                    lc_messages.append(AIMessage(content=content))
                elif HumanMessage is not None:
                    lc_messages.append(HumanMessage(content=content))
                else:
                    lc_messages.append((role or 'user', content))
            response = await model.ainvoke(lc_messages, **kwargs)
            text = getattr(response, 'content', '')
            if isinstance(text, list):
                text = ''.join(str(x.get('text', x)) if isinstance(x, dict) else str(x) for x in text)
            self.real_requests += 1
            self.last_error = ''
            self.last_call_mode = 'real_llm_langchain'
            return LLMResult(text=str(text).strip(), raw={'langchain': True, 'response_metadata': getattr(response, 'response_metadata', {})}, provider=self.provider, model=self.model, degraded=False, call_purpose=call_purpose)
        except Exception as exc:
            fallback = self._mock_chat(messages)
            self.mock_requests += 1
            self.degraded_requests += 1
            error_detail = f'{exc.__class__.__name__}: {exc}'
            self.last_error = error_detail
            self.last_call_mode = 'degraded_to_mock_langchain'
            return LLMResult(text=f'{fallback}\n\n[系统提示：LangChain 模型调用失败，已降级到本地 Mock。原因：{exc.__class__.__name__}]', raw={'error': error_detail, 'provider': self.provider, 'model': self.model, 'langchain': True}, provider='mock', model='mock', degraded=True, error=error_detail, call_purpose=call_purpose)

    async def _openai_compatible_chat(self, messages: list[dict[str, str]], temperature: float | None, max_tokens: int, response_format: dict[str, Any] | None=None, call_purpose: str='chat') -> LLMResult:
        base_url = self.base_url.rstrip('/')
        url = f'{base_url}/chat/completions'
        self.last_url = url
        payload = {'model': self.model, 'messages': messages, 'temperature': self.settings.llm_temperature if temperature is None else temperature, 'max_tokens': max_tokens}
        if response_format:
            payload['response_format'] = response_format
        headers = {'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'}
        try:
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                text = data['choices'][0]['message']['content'].strip()
                self.real_requests += 1
                self.last_error = ''
                self.last_call_mode = 'real_llm'
                return LLMResult(text=text, raw=data, provider=self.provider, model=self.model, degraded=False, call_purpose=call_purpose)
        except Exception as exc:
            fallback = self._mock_chat(messages)
            self.mock_requests += 1
            self.degraded_requests += 1
            error_detail = f'{exc.__class__.__name__}: {exc}'
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    error_detail += f' | response={exc.response.text[:800]}'
                except Exception:
                    pass
            self.last_error = error_detail
            self.last_call_mode = 'degraded_to_mock'
            return LLMResult(text=f'{fallback}\n\n[系统提示：真实模型调用失败，已降级到本地 Mock。原因：{exc.__class__.__name__}]', raw={'error': error_detail, 'provider': self.provider, 'model': self.model, 'url': url}, provider='mock', model='mock', degraded=True, error=error_detail, call_purpose=call_purpose)

    def _record_call(self, result: LLMResult, latency_ms: int) -> None:
        self.recent_calls.append({'time': self.last_request_at, 'purpose': result.call_purpose, 'mode': self.last_call_mode, 'provider': result.provider, 'model': result.model, 'degraded': result.degraded, 'latency_ms': latency_ms, 'error': result.error or self.last_error})

    def _resolve_base_url(self) -> str:
        if self.provider == 'deepseek':
            return (self.settings.llm_base_url or DEEPSEEK_DEFAULT_BASE_URL).rstrip('/')
        return (self.settings.llm_base_url or '').rstrip('/')

    def _resolve_api_key(self) -> str:
        if self.provider == 'deepseek':
            return (self.settings.deepseek_api_key or self.settings.llm_api_key or '').strip()
        return (self.settings.llm_api_key or '').strip()

    def _resolve_model(self) -> str:
        if self.provider == 'deepseek':
            return (self.settings.llm_model or DEEPSEEK_DEFAULT_MODEL).strip()
        return (self.settings.llm_model or '').strip()

    def _mock_chat(self, messages: list[dict[str, str]]) -> str:
        system = '\n'.join((m.get('content', '') for m in messages if m.get('role') == 'system'))
        user = next((m.get('content', '') for m in reversed(messages) if m.get('role') == 'user'), '')
        npc_name = self._extract_after(system, '你是', '，') or self._extract_after(system, '你是', ',') or '办公室成员'
        role = self._extract_after(system, '职业/身份：', '\n') or 'AI Agent 成员'
        level = self._extract_after(system, '当前关系等级：', '\n') or '陌生'
        digest = hashlib.md5((npc_name + user).encode('utf-8')).hexdigest()[:6]
        if any((word in user for word in ['简历', '投递', '秋招', '面试', '项目'])):
            advice = '建议把亮点写成：多智能体编排、长期记忆召回、关系状态建模、FastAPI 工程化部署、DeepSeek 接入和可演示前端闭环。'
        elif any((word in user for word in ['你好', '在吗', 'hi', 'hello'])):
            advice = '很高兴在赛博办公室见到你。你可以问我工作、任务、学习路线，或者让我帮你设计一个 Agent 功能。'
        elif any((word in user for word in ['记得', '上次', '之前'])):
            advice = '我会优先回忆我们的历史互动，并结合当前关系给出更贴近上下文的回答。'
        else:
            advice = '我会结合自己的角色、当前办公室状态和我们之间的关系来回答，并把重要信息写入长期记忆。'
        tone = {'陌生': '我会先保持礼貌和专业。', '熟悉': '我们已经熟悉一些了，我可以说得更具体。', '友好': '把你当朋友后，我会多分享一些实战经验。', '亲密': '既然关系很近，我会直接给你更深入的建议。', '挚友': '我们已经是挚友，我会毫无保留地帮你打磨方案。'}.get(level, '我会保持自然交流。')
        return f'我是{npc_name}，{role}。{tone}{advice}\n\n你刚才说：{user[:120]}\n[Mock-{digest}]'

    @staticmethod
    def _extract_after(text: str, prefix: str, suffix: str) -> str:
        try:
            start = text.index(prefix) + len(prefix)
            end = text.index(suffix, start)
            return text[start:end].strip()
        except ValueError:
            return ''

async def classify_importance(llm: LLMClient, text: str) -> int:
    keywords = ['喜欢', '讨厌', '目标', '计划', '简历', '面试', '项目', '姓名', '学校', '公司', '重要', '记住']
    score = 1 + sum((1 for k in keywords if k in text))
    if len(text) > 80:
        score += 1
    return max(1, min(5, score))
