from __future__ import annotations
import hashlib
import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx

from ..config import Settings
from .knowledge_store import KnowledgeStore
from .safety_guard import SafetyGuard

@dataclass
class BrowserSearchResult:
    title: str
    url: str
    snippet: str = ''
    content: str = ''
    ingested: bool = False
    doc_id: int | None = None
    chunks: int = 0
    error: str = ''

class _DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._in_link = False
        self._link_href = ''
        self._link_text: list[str] = []
        self._snippet_text: list[str] = []
        self._in_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attrs_dict = {k: v or '' for k, v in attrs}
        cls = attrs_dict.get('class', '')
        if tag == 'a' and ('result__a' in cls or attrs_dict.get('rel') == 'nofollow'):
            self._in_link = True
            self._link_href = attrs_dict.get('href', '')
            self._link_text = []
        if tag in {'a', 'div'} and ('result__snippet' in cls or 'result__body' in cls):
            self._in_snippet = True
            self._snippet_text = []

    def handle_endtag(self, tag: str):
        if tag == 'a' and self._in_link:
            title = self._clean(''.join(self._link_text))
            url = self._unwrap_ddg(self._link_href)
            if title and url and url.startswith(('http://', 'https://')):
                self.results.append({'title': title, 'url': url, 'snippet': ''})
            self._in_link = False
        if tag in {'a', 'div'} and self._in_snippet:
            snippet = self._clean(''.join(self._snippet_text))
            if snippet and self.results and not self.results[-1].get('snippet'):
                self.results[-1]['snippet'] = snippet
            self._in_snippet = False

    def handle_data(self, data: str):
        if self._in_link:
            self._link_text.append(data)
        if self._in_snippet:
            self._snippet_text.append(data)

    @staticmethod
    def _unwrap_ddg(url: str) -> str:
        url = html.unescape(url or '')
        if url.startswith('//'):
            url = 'https:' + url
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if 'uddg' in qs:
            return unquote(qs['uddg'][0])
        return url

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r'\s+', ' ', html.unescape(text or '')).strip()

class _TextExtractor(HTMLParser):
    SKIP = {'script', 'style', 'noscript', 'svg', 'canvas', 'nav', 'footer'}
    def __init__(self):
        super().__init__()
        self.skip_stack: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag in self.SKIP:
            self.skip_stack.append(tag)
        if tag in {'p', 'li', 'h1', 'h2', 'h3', 'br'} and not self.skip_stack:
            self.parts.append('\n')

    def handle_endtag(self, tag: str):
        if self.skip_stack and self.skip_stack[-1] == tag:
            self.skip_stack.pop()
        if tag in {'p', 'li', 'h1', 'h2', 'h3'} and not self.skip_stack:
            self.parts.append('\n')

    def handle_data(self, data: str):
        if not self.skip_stack:
            data = html.unescape(data or '')
            if data.strip():
                self.parts.append(data)

    def text(self) -> str:
        return re.sub(r'\n{3,}', '\n\n', re.sub(r'[ \t]+', ' ', ''.join(self.parts))).strip()

class BrowserKnowledgeIngestor:

    def __init__(self, knowledge: KnowledgeStore, settings: Settings, safety: SafetyGuard | None = None):
        self.knowledge = knowledge
        self.settings = settings
        self.safety = safety or SafetyGuard()
        self.last_runs: list[dict[str, Any]] = []

    async def search_and_ingest(self, *, query: str, role: str = '', task_title: str = '', npc_id: str = '', limit: int | None = None) -> dict[str, Any]:
        query = self._build_query(query, role=role, task_title=task_title)
        limit = limit or int(getattr(self.settings, 'browser_search_max_results', 4) or 4)
        limit = max(1, min(limit, 8))
        verdict = self.safety.inspect(query, actor=f'browser:{npc_id or role}')
        if not verdict.allowed:
            return {'ok': False, 'query': query, 'results': [], 'ingested_docs': [], 'error': verdict.reason}
        query = verdict.sanitized_text
        if not bool(getattr(self.settings, 'browser_search_enabled', True)):
            fallback = self._ingest_fallback(query=query, reason='browser_search_disabled', role=role, task_title=task_title)
            return fallback
        try:
            results = await self._duckduckgo_search(query, limit=limit)
            enriched: list[BrowserSearchResult] = []
            for item in results[:limit]:
                enriched.append(await self._fetch_and_ingest(item, query=query, role=role, task_title=task_title, npc_id=npc_id))
            if not enriched:
                return self._ingest_fallback(query=query, reason='no_search_results', role=role, task_title=task_title)
            payload = {'ok': True, 'query': query, 'results': [r.__dict__ for r in enriched], 'ingested_docs': [r.__dict__ for r in enriched if r.ingested], 'summary': f'浏览器检索完成，入库 {sum(1 for r in enriched if r.ingested)} 个资料片段。'}
            self._remember_run(payload)
            return payload
        except Exception as exc:
            payload = self._ingest_fallback(query=query, reason=f'{exc.__class__.__name__}: {exc}', role=role, task_title=task_title)
            self._remember_run(payload)
            return payload

    async def _duckduckgo_search(self, query: str, *, limit: int) -> list[dict[str, str]]:
        url = f'https://duckduckgo.com/html/?q={quote_plus(query)}'
        headers = {'User-Agent': getattr(self.settings, 'browser_user_agent', 'CyberOfficeAgentOS/1.0')}
        timeout = float(getattr(self.settings, 'browser_fetch_timeout_seconds', 8.0) or 8.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        parser = _DuckDuckGoHTMLParser()
        parser.feed(resp.text)
        seen: set[str] = set()
        out: list[dict[str, str]] = []
        for item in parser.results:
            clean_url = item.get('url', '').strip()
            if not clean_url or clean_url in seen:
                continue
            seen.add(clean_url)
            out.append(item)
            if len(out) >= limit:
                break
        return out

    async def _fetch_and_ingest(self, item: dict[str, str], *, query: str, role: str, task_title: str, npc_id: str) -> BrowserSearchResult:
        title = item.get('title') or 'browser_result'
        url = item.get('url') or ''
        snippet = item.get('snippet') or ''
        result = BrowserSearchResult(title=title[:220], url=url[:800], snippet=snippet[:600])
        try:
            content = await self._fetch_text(url)
            if len(content) < 180:
                content = f'{title}\nURL: {url}\nSnippet: {snippet}\nQuery: {query}'
            content = content[:12000]
            md = {'query': query, 'role': role, 'task_title': task_title, 'npc_id': npc_id, 'browser_url': url, 'browser_title': title}
            ingested = self.knowledge.ingest_text(title=f'[browser] {title[:160]}', source=url, content=content, metadata=md)
            result.content = content[:1000]
            result.ingested = True
            result.doc_id = int(ingested.get('doc_id'))
            result.chunks = int(ingested.get('chunks') or 0)
        except Exception as exc:
            result.error = f'{exc.__class__.__name__}: {exc}'
            if snippet:
                content = f'# Browser search snippet\n\nTitle: {title}\nURL: {url}\nQuery: {query}\n\n{snippet}'
                try:
                    ingested = self.knowledge.ingest_text(title=f'[browser-snippet] {title[:140]}', source=url, content=content, metadata={'query': query, 'role': role, 'task_title': task_title, 'npc_id': npc_id, 'snippet_only': True})
                    result.content = content
                    result.ingested = True
                    result.doc_id = int(ingested.get('doc_id'))
                    result.chunks = int(ingested.get('chunks') or 0)
                except Exception:
                    pass
        return result

    async def _fetch_text(self, url: str) -> str:
        headers = {'User-Agent': getattr(self.settings, 'browser_user_agent', 'CyberOfficeAgentOS/1.0')}
        timeout = float(getattr(self.settings, 'browser_fetch_timeout_seconds', 8.0) or 8.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        content_type = resp.headers.get('content-type', '').lower()
        text = resp.text
        if 'html' in content_type or '<html' in text[:500].lower():
            parser = _TextExtractor()
            parser.feed(text)
            return parser.text()
        return re.sub(r'\s+', ' ', text).strip()

    def _ingest_fallback(self, *, query: str, reason: str, role: str, task_title: str) -> dict[str, Any]:
        digest = hashlib.md5(f'{query}:{reason}'.encode()).hexdigest()[:8]
        content = f'''# Browser Search Fallback Report {digest}

Query: {query}
Role: {role}
Task: {task_title}
Status: browser unavailable or no results
Reason: {reason}

This fallback document is intentionally added to the knowledge base so the Agent flow remains auditable. In a network-enabled runtime, the same tool searches the web, fetches result pages/files, extracts text, chunks it, and adds it to RAG.

Recommended next step: configure network access or replace BrowserKnowledgeIngestor with Tavily/SerpAPI/enterprise search; the ToolRegistry contract remains the same.
'''
        ingested = self.knowledge.ingest_text(title=f'[browser-fallback] {query[:120]}', source=f'browser://fallback/{digest}', content=content, metadata={'query': query, 'role': role, 'task_title': task_title, 'fallback_reason': reason})
        payload = {'ok': True, 'query': query, 'results': [], 'ingested_docs': [{'title': ingested['title'], 'source': ingested['source'], 'doc_id': ingested['doc_id'], 'chunks': ingested['chunks'], 'fallback': True}], 'summary': f'浏览器不可用，已写入可审计 fallback 文档：{reason}'}
        self._remember_run(payload)
        return payload

    def _build_query(self, query: str, *, role: str, task_title: str) -> str:
        base = re.sub(r'\s+', ' ', str(query or task_title or role or 'AI Agent tool calling RAG').strip())
        if not any(x in base.lower() for x in ['filetype:', 'github', 'docs', '文档', '论文', 'paper', 'spec']):
            base = f'{base} docs filetype:md OR filetype:pdf OR filetype:txt'
        return base[:500]

    def _remember_run(self, payload: dict[str, Any]) -> None:
        self.last_runs.append(payload)
        self.last_runs = self.last_runs[-30:]
