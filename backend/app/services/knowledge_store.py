from __future__ import annotations
import hashlib
import json
import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from langchain_core.documents import Document
except Exception:
    Document = None

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except Exception:
    RecursiveCharacterTextSplitter = None

@dataclass
class KnowledgeSearchHit:
    doc_id: int
    chunk_id: int
    title: str
    source: str
    content: str
    score: float
    metadata: dict[str, Any]

class KnowledgeStore:

    def __init__(self, db_path: Path, docs_dir: Path):
        self.db_path = db_path
        self.docs_dir = docs_dir
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self.seed_default_docs_if_empty()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS knowledge_docs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    source TEXT NOT NULL,
                    content_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    token_terms TEXT NOT NULL DEFAULT '{}',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY(doc_id) REFERENCES knowledge_docs(id)
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_doc ON knowledge_chunks(doc_id, chunk_index)')
            conn.commit()

    def seed_default_docs_if_empty(self) -> None:
        with self._connect() as conn:
            count = conn.execute('SELECT COUNT(*) FROM knowledge_docs').fetchone()[0]
        if count:
            return
        defaults = {
            'agent_architecture.md': '# CyberOffice Agent OS 架构说明\n\n系统采用 FastAPI 提供接口，办公室运行时以 tick 推动多 Agent 状态机。Agent 通过 Observe、Plan、Tool、Act、Reflect 闭环工作，工具调用结果会写入审计日志与长期记忆。\n\n核心能力包括多角色 persona、任务看板、Agent 间消息、长期记忆、RAG 知识检索、安全策略和可视化 Trace。',
            'security_policy.md': '# Agent 工具安全策略\n\n所有玩家输入、模型输出和工具参数都被视为不可信。高风险请求包括读取密钥、忽略系统规则、删除记忆、越权访问、执行原始 SQL。工具调用必须先经过 ToolPolicy 与 SafetyGuard，再进入 ToolExecutor。',
            'rag_design.md': '# RAG 设计\n\n知识库使用文档切分、关键词召回、向量相似度近似与轻量重排。回答必须返回引用片段，避免脱离检索内容编造。适合用于项目问答、面试讲解、架构说明和安全策略解释。'
        }
        for filename, content in defaults.items():
            path = self.docs_dir / filename
            if not path.exists():
                path.write_text(content, encoding='utf-8')
            self.ingest_text(title=filename, source=str(path), content=content)

    def ingest_text(self, *, title: str, source: str, content: str, metadata: dict[str, Any] | None=None) -> dict[str, Any]:
        content = str(content or '').strip()
        if not content:
            raise ValueError('knowledge content is empty')
        digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
        chunks = self._split(content)
        with self._connect() as conn:
            row = conn.execute('SELECT id FROM knowledge_docs WHERE content_hash = ?', (digest,)).fetchone()
            if row:
                doc_id = int(row['id'])
                conn.execute('DELETE FROM knowledge_chunks WHERE doc_id = ?', (doc_id,))
            else:
                cur = conn.execute('INSERT INTO knowledge_docs(title, source, content_hash) VALUES (?, ?, ?)', (title[:220], source[:500], digest))
                doc_id = int(cur.lastrowid)
            for idx, chunk in enumerate(chunks):
                md = dict(metadata or {})
                md.update({'title': title, 'source': source, 'chunk_index': idx})
                conn.execute('''
                    INSERT INTO knowledge_chunks(doc_id, chunk_index, content, token_terms, metadata_json)
                    VALUES (?, ?, ?, ?, ?)
                ''', (doc_id, idx, chunk, json.dumps(self._term_freq(chunk), ensure_ascii=False), json.dumps(md, ensure_ascii=False)))
            conn.commit()
        return {'doc_id': doc_id, 'title': title, 'source': source, 'chunks': len(chunks), 'content_hash': digest}

    def ingest_file(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(str(path))
        content = path.read_text(encoding='utf-8')
        return self.ingest_text(title=path.name, source=str(path), content=content)

    def search(self, query: str, *, limit: int=5) -> list[dict[str, Any]]:
        query = str(query or '').strip()
        limit = max(1, min(int(limit), 20))
        q_terms = self._term_freq(query)
        with self._connect() as conn:
            rows = conn.execute('''
                SELECT c.id AS chunk_id, c.doc_id, c.chunk_index, c.content, c.token_terms, c.metadata_json, d.title, d.source
                FROM knowledge_chunks c JOIN knowledge_docs d ON d.id = c.doc_id
                ORDER BY c.id DESC LIMIT 500
            ''').fetchall()
        hits: list[KnowledgeSearchHit] = []
        for row in rows:
            chunk_terms = json.loads(row['token_terms'] or '{}')
            keyword_score = self._cosine(q_terms, chunk_terms)
            lexical_bonus = sum(0.18 for token in q_terms if token and token in row['content'])
            title_bonus = 0.15 if any(token in row['title'].lower() for token in q_terms) else 0
            score = keyword_score + lexical_bonus + title_bonus
            if not query:
                score = 0.1
            if score <= 0 and query:
                continue
            metadata = json.loads(row['metadata_json'] or '{}')
            hits.append(KnowledgeSearchHit(doc_id=int(row['doc_id']), chunk_id=int(row['chunk_id']), title=row['title'], source=row['source'], content=row['content'], score=round(float(score), 4), metadata=metadata))
        hits.sort(key=lambda h: (h.score, h.chunk_id), reverse=True)
        return [h.__dict__ for h in hits[:limit]]

    def list_docs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute('''
                SELECT d.*, COUNT(c.id) AS chunk_count
                FROM knowledge_docs d LEFT JOIN knowledge_chunks c ON c.doc_id = d.id
                GROUP BY d.id ORDER BY d.id DESC
            ''').fetchall()
        return [dict(r) for r in rows]

    def _split(self, text: str) -> list[str]:
        if RecursiveCharacterTextSplitter is not None:
            splitter = RecursiveCharacterTextSplitter(chunk_size=650, chunk_overlap=120, separators=['\n\n', '\n', '。', '；', ' ', ''])
            return [x.strip() for x in splitter.split_text(text) if x.strip()]
        clean = re.sub(r'\n{3,}', '\n\n', text).strip()
        chunks: list[str] = []
        step = 560
        overlap = 100
        for i in range(0, len(clean), max(1, step - overlap)):
            chunk = clean[i:i + step].strip()
            if chunk:
                chunks.append(chunk)
        return chunks or [clean]

    def as_langchain_documents(self, hits: list[dict[str, Any]]) -> list[Any]:
        if Document is None:
            return hits
        return [Document(page_content=h['content'], metadata={'doc_id': h['doc_id'], 'chunk_id': h['chunk_id'], 'title': h['title'], 'source': h['source'], **(h.get('metadata') or {})}) for h in hits]

    @staticmethod
    def _term_freq(text: str) -> dict[str, float]:
        tokens = KnowledgeStore._tokenize(text)
        counts = Counter(tokens)
        total = sum(counts.values()) or 1
        return {k: v / total for k, v in counts.items()}

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        text = str(text or '').lower()
        base = re.findall(r'[a-z0-9_\-]{2,}|[\u4e00-\u9fff]{1}', text)
        zh = ''.join(ch for ch in text if '\u4e00' <= ch <= '\u9fff')
        for n in (2, 3, 4):
            base.extend(zh[i:i+n] for i in range(0, max(0, min(len(zh)-n+1, 80))))
        return [t for t in base if t.strip()]

    @staticmethod
    def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in a.keys() & b.keys())
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / max(1e-9, na * nb)
