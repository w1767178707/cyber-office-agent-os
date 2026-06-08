from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .knowledge_store import KnowledgeStore
from .memory_router import MemoryRouter
from .event_bus import EventBus
from .safety_guard import SafetyGuard

def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')

def _slug(value: str, fallback: str = 'resource') -> str:
    text = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff_-]+', '_', str(value or '').strip()).strip('_')
    return (text or fallback)[:80]

@dataclass
class CodeSafetyResult:
    allowed: bool
    reason: str
    categories: list[str]

class SafePythonValidator(ast.NodeVisitor):

    ALLOWED_IMPORTS = {
        'math', 'json', 'statistics', 'random', 'datetime', 'collections',
        'itertools', 'functools', 're', 'typing', 'dataclasses'
    }
    BLOCKED_NAMES = {
        'open', 'exec', 'eval', 'compile', 'input', '__import__', 'breakpoint',
        'globals', 'locals', 'vars', 'dir', 'help', 'memoryview', 'classmethod',
        'staticmethod', 'property', 'super', 'exit', 'quit'
    }
    BLOCKED_MODULES = {
        'os', 'sys', 'subprocess', 'socket', 'requests', 'httpx', 'urllib',
        'pathlib', 'shutil', 'sqlite3', 'pickle', 'marshal', 'importlib', 'builtins',
        'ctypes', 'multiprocessing', 'threading', 'asyncio', 'ssl', 'ftplib', 'smtplib'
    }

    def __init__(self) -> None:
        self.errors: list[str] = []

    def visit_Import(self, node: ast.Import) -> Any:
        for alias in node.names:
            root = alias.name.split('.')[0]
            if root not in self.ALLOWED_IMPORTS or root in self.BLOCKED_MODULES:
                self.errors.append(f'禁止导入模块：{alias.name}')
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        root = (node.module or '').split('.')[0]
        if not root or root not in self.ALLOWED_IMPORTS or root in self.BLOCKED_MODULES:
            self.errors.append(f'禁止 from-import 模块：{node.module}')
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        name = ''
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name in self.BLOCKED_NAMES or name.startswith('__'):
            self.errors.append(f'禁止调用：{name}')
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        if node.attr.startswith('__'):
            self.errors.append(f'禁止访问双下划线属性：{node.attr}')
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> Any:
        if node.id in self.BLOCKED_NAMES or node.id.startswith('__'):
            self.errors.append(f'禁止使用名称：{node.id}')
        self.generic_visit(node)

class CompanyResourceCenter:

    DOC_TEMPLATES: dict[str, str] = {
        'prd': '# PRD｜{topic}\n\n## 背景\n{context}\n\n## 用户/角色\n- 目标用户：办公室 Agent、玩家、面试官\n- 关键角色：产品、技术、算法、安全、HR\n\n## 目标\n- 明确问题与成功标准\n- 让 Agent 可以基于本文档协作推进\n\n## 功能范围\n1. 核心流程\n2. 边界条件\n3. 可观测指标\n\n## 验收标准\n- 有可运行接口/页面\n- 有 Trace / 审计 / 回滚\n- 文档进入知识库，可被 RAG 查阅\n',
        'adr': '# ADR｜{topic}\n\n## 状态\n提议中\n\n## 背景\n{context}\n\n## 决策\n采用可审计、可回滚、可检索的内部资源流转机制。\n\n## 方案\n- Agent 通过工具生成文档/代码/运行结果\n- 资源写入内部共享空间\n- 关键资源进入知识库和公共记忆池\n- 高风险动作禁止自动执行\n\n## 影响\n- 提升公司模拟真实性\n- 便于面试讲解 Agent Runtime 的工程闭环\n\n## 回滚\n恢复到上一版工具注册表与资源索引即可。\n',
        'runbook': '# Runbook｜{topic}\n\n## 场景\n{context}\n\n## 触发条件\n- 任务阻塞\n- 工具调用失败\n- RAG 无引用\n- 代码运行异常\n\n## 处理步骤\n1. 查询公共记忆池\n2. 检索知识库\n3. 查看工具审计日志\n4. 生成修复任务\n5. 必要时执行回滚\n\n## 验证\n- 重新运行相关工具\n- 检查输出是否写入共享资源\n- 检查知识库是否可检索\n',
        'api_contract': '# API Contract｜{topic}\n\n## 背景\n{context}\n\n## Endpoint\n- Method: POST/GET\n- Path: /company/resources/...\n\n## Request\n```json\n{{"query": "...", "npc_id": "..."}}\n```\n\n## Response\n```json\n{{"ok": true, "resource": {{}}}}\n```\n\n## 错误处理\n- 400：参数非法\n- 403：安全策略阻断\n- 500：运行时异常，写入审计日志\n',
        'test_plan': '# Test Plan｜{topic}\n\n## 背景\n{context}\n\n## 测试范围\n- API 契约\n- 工具权限\n- 代码运行沙箱\n- RAG 检索\n- 公共记忆共享\n\n## 用例\n1. Agent 创建文档并入库\n2. Agent 写入安全 Python 代码并运行\n3. 高风险代码被拒绝\n4. 动态工具创建后可执行\n\n## 通过标准\n所有用例返回 ok=true，且资源可在知识库检索。\n',
        'security_review': '# Security Review｜{topic}\n\n## 背景\n{context}\n\n## 风险点\n- 动态工具生成可能引入越权行为\n- 代码运行可能访问文件、网络或进程\n- 文档可能污染公共记忆\n\n## 控制措施\n- 禁止真实世界交易工具\n- 禁止网络/文件/进程相关代码\n- 动态工具采用声明式模板，不执行任意系统命令\n- 所有结果写审计并可回滚\n\n## 结论\n允许低风险内部模拟行为，禁止交易、支付、真实采购、凭证读取与破坏性操作。\n',
        'meeting_notes': '# Meeting Notes｜{topic}\n\n## 会议背景\n{context}\n\n## 结论\n- 明确下一步工具化动作\n- 产物进入共享资源空间\n- 需要跨角色同步时写入公共记忆池\n\n## Action Items\n- Owner：当前 Agent\n- 输出：文档/代码/运行结果/动态工具\n- 验收：可检索、可复用、可审计\n',
    }

    def __init__(self, *, db_path: Path, workspace_dir: Path, knowledge: KnowledgeStore, safety: SafetyGuard, events: EventBus | None = None, memory_router: MemoryRouter | None = None):
        self.db_path = db_path
        self.workspace_dir = workspace_dir
        self.knowledge = knowledge
        self.safety = safety
        self.events = events
        self.memory_router = memory_router
        self.docs_dir = workspace_dir / 'docs'
        self.code_dir = workspace_dir / 'code'
        self.runs_dir = workspace_dir / 'runs'
        self.tools_dir = workspace_dir / 'dynamic_tools'
        for p in (self.docs_dir, self.code_dir, self.runs_dir, self.tools_dir):
            p.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS internal_resources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    owner_npc_id TEXT DEFAULT '',
                    path TEXT DEFAULT '',
                    source TEXT DEFAULT '',
                    content_hash TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS dynamic_tools (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    owner_npc_id TEXT NOT NULL,
                    description TEXT NOT NULL,
                    tool_kind TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            ''')
            conn.commit()


    def list_resources(self, *, resource_type: str | None = None, owner_npc_id: str | None = None, limit: int = 80) -> list[dict[str, Any]]:
        query = 'SELECT * FROM internal_resources WHERE 1=1'
        params: list[Any] = []
        if resource_type:
            query += ' AND resource_type = ?'
            params.append(resource_type)
        if owner_npc_id:
            query += ' AND owner_npc_id = ?'
            params.append(owner_npc_id)
        query += ' ORDER BY id DESC LIMIT ?'
        params.append(max(1, min(int(limit), 300)))
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_resource(r) for r in rows]

    def search_resources(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        q = str(query or '').strip().lower()
        rows = self.list_resources(limit=300)
        if not q:
            return rows[:limit]
        scored: list[tuple[int, dict[str, Any]]] = []
        terms = [x for x in re.split(r'\s+', q) if x]
        for r in rows:
            hay = f"{r.get('title','')} {r.get('resource_type','')} {r.get('source','')} {json.dumps(r.get('metadata') or {}, ensure_ascii=False)}".lower()
            score = sum(1 for t in terms if t in hay)
            if score:
                scored.append((score, r))
        scored.sort(key=lambda x: (x[0], x[1].get('id', 0)), reverse=True)
        return [r for _, r in scored[:max(1, min(int(limit), 30))]]

    def create_document(self, *, owner_npc_id: str, title: str, document_type: str = 'adr', topic: str = '', context: str = '', content: str = '', publish: bool = True, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        verdict = self.safety.inspect(' '.join([title, document_type, topic, context, content])[:4000], actor=f'agent:{owner_npc_id}', tool_name='create_company_document')
        if not verdict.allowed:
            return {'ok': False, 'blocked': True, 'reason': verdict.reason, 'categories': verdict.categories}
        doc_type = self._normalize_doc_type(document_type)
        real_topic = topic or title or doc_type
        body = content.strip() if content.strip() else self.DOC_TEMPLATES[doc_type].format(topic=real_topic, context=context or '由 Agent 根据当前公司模拟任务自动生成。')
        if not body.startswith('#'):
            body = f'# {title or real_topic}\n\n{body}'
        filename = f"{int(time.time()*1000)}_{_slug(doc_type)}_{_slug(real_topic)}.md"
        path = self.docs_dir / filename
        path.write_text(body, encoding='utf-8')
        resource = self._record_resource(title=title or f'{doc_type}:{real_topic}', resource_type='document', owner_npc_id=owner_npc_id, path=path, source=f'internal://docs/{filename}', metadata={'document_type': doc_type, 'topic': real_topic, **(metadata or {})})
        ingest = None
        if publish:
            ingest = self.knowledge.ingest_text(title=resource['title'], source=resource['source'], content=body, metadata={'resource_id': resource['id'], 'resource_type': 'document', 'owner_npc_id': owner_npc_id, 'document_type': doc_type})
            resource['knowledge_doc_id'] = ingest.get('doc_id')
        self._publish_public_resource_memory(owner_npc_id=owner_npc_id, title=resource['title'], resource_type='document', source=resource['source'], summary=body[:700])
        self._event('internal_document_created', f'Agent {owner_npc_id} 生成内部文档：{resource["title"]}', [owner_npc_id])
        return {'ok': True, 'resource': resource, 'content_preview': body[:700], 'knowledge_ingest': ingest, 'summary': f'已生成 {doc_type} 文档并写入共享资源/知识库：{resource["title"]}'}

    def write_code_artifact(self, *, owner_npc_id: str, title: str, purpose: str = '', code: str = '', language: str = 'python', publish: bool = True) -> dict[str, Any]:
        language = (language or 'python').lower().strip()
        if language not in {'python', 'py'}:
            return {'ok': False, 'blocked': True, 'reason': '当前公司游戏只允许安全 Python 代码工件。'}
        code = code.strip() or self._default_code_for_purpose(purpose or title)
        safety = self.validate_python(code)
        if not safety.allowed:
            return {'ok': False, 'blocked': True, 'reason': safety.reason, 'categories': safety.categories, 'summary': '代码未写入：安全校验失败'}
        filename = f"{int(time.time()*1000)}_{_slug(title or purpose, 'artifact')}.py"
        path = self.code_dir / filename
        path.write_text(code + '\n', encoding='utf-8')
        resource = self._record_resource(title=title or purpose or filename, resource_type='code', owner_npc_id=owner_npc_id, path=path, source=f'internal://code/{filename}', metadata={'language': 'python', 'purpose': purpose, 'safety': safety.reason})
        ingest = None
        if publish:
            ingest = self.knowledge.ingest_text(title=f'代码工件｜{resource["title"]}', source=resource['source'], content=path.read_text(encoding='utf-8'), metadata={'resource_id': resource['id'], 'resource_type': 'code', 'owner_npc_id': owner_npc_id})
            resource['knowledge_doc_id'] = ingest.get('doc_id')
        self._publish_public_resource_memory(owner_npc_id=owner_npc_id, title=resource['title'], resource_type='code', source=resource['source'], summary=code[:700])
        self._event('internal_code_written', f'Agent {owner_npc_id} 写入安全代码工件：{resource["title"]}', [owner_npc_id])
        return {'ok': True, 'resource': resource, 'code_preview': code[:900], 'knowledge_ingest': ingest, 'summary': f'代码工件已写入共享资源：{resource["title"]}'}

    def run_code_artifact(self, *, owner_npc_id: str, resource_id: int | None = None, code: str = '', stdin: str = '', timeout_seconds: float = 3.0, publish: bool = True) -> dict[str, Any]:
        source_title = 'ad-hoc safe python run'
        if resource_id:
            resource = self.get_resource(int(resource_id))
            if not resource:
                return {'ok': False, 'reason': f'资源 #{resource_id} 不存在'}
            if resource.get('resource_type') != 'code':
                return {'ok': False, 'reason': f'资源 #{resource_id} 不是代码工件'}
            path = Path(resource.get('path') or '')
            if not path.exists():
                return {'ok': False, 'reason': f'代码文件不存在：{path}'}
            code = path.read_text(encoding='utf-8')
            source_title = resource.get('title') or source_title
        code = code.strip()
        if not code:
            return {'ok': False, 'reason': '没有可运行代码'}
        safety = self.validate_python(code)
        if not safety.allowed:
            return {'ok': False, 'blocked': True, 'reason': safety.reason, 'categories': safety.categories}
        timeout = max(0.5, min(float(timeout_seconds), 5.0))
        run_id = f'{int(time.time()*1000)}_{_slug(source_title, "run")}'
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        script = run_dir / 'main.py'
        script.write_text(code + '\n', encoding='utf-8')
        started = time.perf_counter()
        env = {'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'}
        try:


            proc = subprocess.run([sys.executable, '-I', script.name], input=str(stdin or '')[:2000], capture_output=True, text=True, cwd=str(run_dir), timeout=timeout, env=env)
            latency_ms = int((time.perf_counter() - started) * 1000)
            output = {
                'returncode': proc.returncode,
                'stdout': (proc.stdout or '')[-4000:],
                'stderr': (proc.stderr or '')[-4000:],
                'latency_ms': latency_ms,
                'timeout_seconds': timeout,
            }
        except subprocess.TimeoutExpired as exc:
            output = {'returncode': -1, 'stdout': (exc.stdout or '')[-2000:] if isinstance(exc.stdout, str) else '', 'stderr': '代码运行超时，已终止。', 'latency_ms': int((time.perf_counter() - started) * 1000), 'timeout_seconds': timeout}
        result_md = f"# Code Run Result｜{source_title}\n\n- Owner: {owner_npc_id}\n- Return code: {output['returncode']}\n- Latency: {output['latency_ms']} ms\n\n## STDOUT\n```text\n{output['stdout']}\n```\n\n## STDERR\n```text\n{output['stderr']}\n```\n"
        result_path = run_dir / 'result.md'
        result_path.write_text(result_md, encoding='utf-8')
        run_resource = self._record_resource(title=f'运行结果｜{source_title}', resource_type='run_result', owner_npc_id=owner_npc_id, path=result_path, source=f'internal://runs/{run_id}/result.md', metadata={'returncode': output['returncode'], 'latency_ms': output['latency_ms'], 'source_resource_id': resource_id})
        ingest = None
        if publish:
            ingest = self.knowledge.ingest_text(title=run_resource['title'], source=run_resource['source'], content=result_md, metadata={'resource_id': run_resource['id'], 'resource_type': 'run_result', 'owner_npc_id': owner_npc_id})
            run_resource['knowledge_doc_id'] = ingest.get('doc_id')
        self._publish_public_resource_memory(owner_npc_id=owner_npc_id, title=run_resource['title'], resource_type='run_result', source=run_resource['source'], summary=result_md[:700])
        self._event('internal_code_run', f'Agent {owner_npc_id} 运行代码工件：{source_title}，returncode={output["returncode"]}', [owner_npc_id])
        return {'ok': output['returncode'] == 0, 'resource': run_resource, 'run': output, 'knowledge_ingest': ingest, 'summary': f'代码已在受限运行器执行，returncode={output["returncode"]}'}

    def get_resource(self, resource_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM internal_resources WHERE id = ?', (int(resource_id),)).fetchone()
        return self._row_to_resource(row) if row else None


    def create_dynamic_tool(self, *, owner_npc_id: str, name: str, description: str, tool_kind: str = 'document_template', spec: dict[str, Any] | None = None) -> dict[str, Any]:
        raw_name = _slug(name, 'agent_tool').lower()
        if not raw_name.startswith('agent_'):
            raw_name = f'agent_{raw_name}'
        raw_name = raw_name[:48]
        kind = (tool_kind or 'document_template').lower().strip()
        allowed = {'document_template', 'checklist', 'decision_matrix', 'safe_python_macro', 'resource_search_macro'}
        if kind not in allowed:
            return {'ok': False, 'blocked': True, 'reason': f'动态工具类型不允许：{kind}'}
        if any(x in raw_name for x in ['pay', 'purchase', 'trade', 'order', '真实交易', '采购']):
            return {'ok': False, 'blocked': True, 'reason': '禁止创建涉及真实世界交易的动态工具。'}
        spec = dict(spec or {})
        if kind == 'safe_python_macro':
            code = str(spec.get('code') or self._default_code_for_purpose(description or name))
            safety = self.validate_python(code)
            if not safety.allowed:
                return {'ok': False, 'blocked': True, 'reason': safety.reason, 'categories': safety.categories}
            spec['code'] = code
        else:
            spec.setdefault('template', self._default_dynamic_template(kind, description))
        now = _now()
        with self._connect() as conn:
            conn.execute('''
                INSERT INTO dynamic_tools(name, owner_npc_id, description, tool_kind, spec_json, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                ON CONFLICT(name) DO UPDATE SET owner_npc_id=excluded.owner_npc_id, description=excluded.description,
                    tool_kind=excluded.tool_kind, spec_json=excluded.spec_json, status='active', updated_at=excluded.updated_at
            ''', (raw_name, owner_npc_id, description[:600], kind, json.dumps(spec, ensure_ascii=False), now, now))
            conn.commit()
        tool_doc = f"# Dynamic Tool｜{raw_name}\n\n- Owner: {owner_npc_id}\n- Kind: {kind}\n- Description: {description}\n\n```json\n{json.dumps(spec, ensure_ascii=False, indent=2)}\n```\n"
        doc_result = self.create_document(owner_npc_id=owner_npc_id, title=f'动态工具规范｜{raw_name}', document_type='adr', topic=raw_name, context=tool_doc, content=tool_doc, publish=True, metadata={'dynamic_tool': raw_name, 'tool_kind': kind})
        self._event('dynamic_tool_created', f'Agent {owner_npc_id} 创建动态工具：{raw_name} ({kind})', [owner_npc_id])
        return {'ok': True, 'tool': self.get_dynamic_tool(raw_name), 'tool_name': raw_name, 'resource': doc_result.get('resource'), 'summary': f'动态工具 {raw_name} 已创建，可通过 run_dynamic_tool 执行。'}

    def list_dynamic_tools(self, *, owner_npc_id: str | None = None, limit: int = 80) -> list[dict[str, Any]]:
        query = "SELECT * FROM dynamic_tools WHERE status = 'active'"
        params: list[Any] = []
        if owner_npc_id:
            query += ' AND owner_npc_id = ?'
            params.append(owner_npc_id)
        query += ' ORDER BY id DESC LIMIT ?'
        params.append(max(1, min(int(limit), 200)))
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_dynamic_tool(r) for r in rows]

    def get_dynamic_tool(self, name: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM dynamic_tools WHERE name = ? AND status = 'active'", (name,)).fetchone()
        return self._row_to_dynamic_tool(row) if row else None

    def has_dynamic_tool(self, name: str) -> bool:
        return self.get_dynamic_tool(name) is not None

    def dynamic_tool_specs_for_registry(self) -> list[dict[str, Any]]:
        out = []
        for t in self.list_dynamic_tools(limit=200):
            out.append({'name': t['name'], 'description': '[动态工具] ' + t.get('description', ''), 'risk': 'medium' if t.get('tool_kind') == 'safe_python_macro' else 'low', 'requires_approval': False, 'allowed_roles': [t.get('owner_npc_id', '')], 'dynamic': True, 'tool_kind': t.get('tool_kind')})
        return out

    def run_dynamic_tool(self, *, owner_npc_id: str, tool_name: str, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        tool = self.get_dynamic_tool(tool_name)
        if not tool:
            return {'ok': False, 'reason': f'动态工具不存在：{tool_name}'}
        inputs = dict(inputs or {})
        kind = tool.get('tool_kind')
        spec = tool.get('spec') or {}
        topic = str(inputs.get('topic') or inputs.get('query') or tool.get('description') or tool_name)
        context = str(inputs.get('context') or inputs.get('content') or '')
        if kind == 'resource_search_macro':
            query = str(inputs.get('query') or topic)
            hits = self.search_resources(query, limit=int(inputs.get('limit') or 8))
            return {'ok': True, 'tool': tool, 'hits': hits, 'summary': f'动态工具 {tool_name} 检索到 {len(hits)} 个内部资源'}
        if kind == 'safe_python_macro':
            code = str(spec.get('code') or '')
            return self.run_code_artifact(owner_npc_id=owner_npc_id, code=code, stdin=json.dumps(inputs, ensure_ascii=False), publish=True)
        if kind == 'decision_matrix':
            options = inputs.get('options') or ['方案A', '方案B', '延后处理']
            criteria = inputs.get('criteria') or ['影响范围', '实现成本', '安全风险', '可回滚性']
            content = self._render_decision_matrix(topic, context, options, criteria)
            return self.create_document(owner_npc_id=owner_npc_id, title=f'决策矩阵｜{topic}', document_type='adr', topic=topic, context=context, content=content, publish=True, metadata={'dynamic_tool': tool_name})
        if kind == 'checklist':
            template = str(spec.get('template') or self._default_dynamic_template(kind, tool.get('description', '')))
            content = self._fill_template(template, topic=topic, context=context, inputs=inputs)
            return self.create_document(owner_npc_id=owner_npc_id, title=f'检查清单｜{topic}', document_type='test_plan', topic=topic, context=context, content=content, publish=True, metadata={'dynamic_tool': tool_name})
        template = str(spec.get('template') or self._default_dynamic_template('document_template', tool.get('description', '')))
        content = self._fill_template(template, topic=topic, context=context, inputs=inputs)
        return self.create_document(owner_npc_id=owner_npc_id, title=f'动态工具产物｜{topic}', document_type='adr', topic=topic, context=context, content=content, publish=True, metadata={'dynamic_tool': tool_name})


    def validate_python(self, code: str) -> CodeSafetyResult:
        text = str(code or '')
        verdict = self.safety.inspect(text[:4000], actor='agent-code', tool_name='safe_python_runner')
        if not verdict.allowed:
            return CodeSafetyResult(False, verdict.reason, verdict.categories)
        lowered = text.lower()
        transaction_terms = ['payment', 'pay ', 'purchase', 'buy ', 'sell ', 'trade', 'stripe', 'alipay', 'wechatpay', '真实交易', '支付', '下单', '采购']
        if any(t in lowered for t in transaction_terms):
            return CodeSafetyResult(False, '禁止生成或运行涉及真实世界交易、支付、采购、下单的代码。', ['real_world_transaction'])
        if len(text) > 12000:
            return CodeSafetyResult(False, '代码过长，超过当前公司游戏安全运行限制。', ['oversized_code'])
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            return CodeSafetyResult(False, f'Python 语法错误：{exc}', ['syntax_error'])
        visitor = SafePythonValidator()
        visitor.visit(tree)
        if visitor.errors:
            return CodeSafetyResult(False, '；'.join(visitor.errors[:6]), ['unsafe_python'])
        return CodeSafetyResult(True, '通过安全 Python 校验', [])


    def _record_resource(self, *, title: str, resource_type: str, owner_npc_id: str, path: Path, source: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        content = path.read_text(encoding='utf-8') if path.exists() else ''
        digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
        now = _now()
        with self._connect() as conn:
            cur = conn.execute('''
                INSERT INTO internal_resources(title, resource_type, owner_npc_id, path, source, content_hash, status, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            ''', (title[:220], resource_type, owner_npc_id, str(path), source, digest, json.dumps(metadata or {}, ensure_ascii=False), now, now))
            conn.commit()
            row = conn.execute('SELECT * FROM internal_resources WHERE id = ?', (cur.lastrowid,)).fetchone()
        return self._row_to_resource(row)

    @staticmethod
    def _row_to_resource(row: sqlite3.Row | None) -> dict[str, Any]:
        if not row:
            return {}
        data = dict(row)
        try:
            data['metadata'] = json.loads(data.get('metadata_json') or '{}')
        except Exception:
            data['metadata'] = {}
        return data

    @staticmethod
    def _row_to_dynamic_tool(row: sqlite3.Row | None) -> dict[str, Any]:
        if not row:
            return {}
        data = dict(row)
        try:
            data['spec'] = json.loads(data.get('spec_json') or '{}')
        except Exception:
            data['spec'] = {}
        return data

    def _publish_public_resource_memory(self, *, owner_npc_id: str, title: str, resource_type: str, source: str, summary: str) -> None:
        if not self.memory_router:
            return
        try:
            self.memory_router.memory.add_memory(
                npc_id=self.memory_router.public_npc_id,
                player_name=self.memory_router.public_player_name,
                content=f'【内部共享资源｜来源Agent={owner_npc_id}｜类型={resource_type}】\n标题：{title}\n来源：{source}\n摘要：{summary[:700]}',
                importance=4,
                kind='internal_resource',
            )
        except Exception:
            pass

    def _event(self, event_type: str, content: str, involved: list[str]) -> None:
        if self.events:
            try:
                self.events.publish(event_type, content, involved)
            except Exception:
                pass

    @staticmethod
    def _normalize_doc_type(value: str) -> str:
        text = str(value or '').lower().strip()
        mapping = {
            '产品': 'prd', '需求': 'prd', 'prd': 'prd',
            '架构决策': 'adr', '决策': 'adr', 'adr': 'adr', 'rfc': 'adr',
            '运行手册': 'runbook', '运维': 'runbook', 'runbook': 'runbook',
            '接口': 'api_contract', 'api': 'api_contract', 'contract': 'api_contract',
            '测试': 'test_plan', 'test': 'test_plan', '验收': 'test_plan',
            '安全': 'security_review', 'security': 'security_review',
            '会议': 'meeting_notes', 'meeting': 'meeting_notes', 'notes': 'meeting_notes',
        }
        return mapping.get(text, text if text in CompanyResourceCenter.DOC_TEMPLATES else 'adr')

    @staticmethod
    def _default_code_for_purpose(purpose: str) -> str:
        purpose = str(purpose or '计算公司模拟指标').replace(chr(34), '')[:300]
        return f'''\nfrom statistics import mean\n\ndef evaluate_company_signal(metrics):\n    values = [float(v) for v in metrics.values() if isinstance(v, (int, float))]\n    base = mean(values) if values else 0.0\n    risk_penalty = float(metrics.get("risk", 0)) * 0.4\n    velocity_bonus = float(metrics.get("velocity", 0)) * 0.2\n    return round(base + velocity_bonus - risk_penalty, 3)\n\nsample = {{"quality": 82, "velocity": 12, "risk": 8}}\nprint({{"purpose": "{purpose}", "score": evaluate_company_signal(sample)}})\n'''.strip()

    @staticmethod
    def _default_dynamic_template(kind: str, description: str) -> str:
        if kind == 'checklist':
            return '# Checklist｜{topic}\n\n背景：{context}\n\n- [ ] 目标清晰\n- [ ] 输入资源已检索\n- [ ] 关键风险已评审\n- [ ] 产物已写入知识库\n- [ ] 必要时已写入公共记忆池\n\n工具说明：{description}\n'
        if kind == 'decision_matrix':
            return '# Decision Matrix｜{topic}\n\n{context}\n'
        if kind == 'resource_search_macro':
            return 'query={topic}'
        return '# Dynamic Tool Output｜{topic}\n\n## 背景\n{context}\n\n## 产出\n该产物由 Agent 动态工具生成。\n\n## 工具说明\n{description}\n'

    @staticmethod
    def _fill_template(template: str, *, topic: str, context: str, inputs: dict[str, Any]) -> str:
        safe = {k: str(v)[:1000] for k, v in inputs.items() if isinstance(k, str)}
        safe.setdefault('topic', topic)
        safe.setdefault('context', context)
        safe.setdefault('description', str(inputs.get('description') or ''))
        try:
            return template.format(**safe)
        except Exception:
            return template + f'\n\n## Inputs\n```json\n{json.dumps(inputs, ensure_ascii=False, indent=2)}\n```\n'

    @staticmethod
    def _render_decision_matrix(topic: str, context: str, options: Any, criteria: Any) -> str:
        opts = options if isinstance(options, list) else [str(options)]
        crit = criteria if isinstance(criteria, list) else [str(criteria)]
        lines = [f'# Decision Matrix｜{topic}', '', f'## 背景\n{context or "由动态工具生成。"}', '', '| 方案 | ' + ' | '.join(map(str, crit)) + ' | 总结 |', '|---|' + '|'.join(['---'] * len(crit)) + '|---|']
        for opt in opts[:8]:
            lines.append('| ' + str(opt)[:80] + ' | ' + ' | '.join(['待评估'] * len(crit)) + ' | 可由相关 Agent 继续补充 |')
        return '\n'.join(lines) + '\n'
