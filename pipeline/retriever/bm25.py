"""BM25 稀疏检索引擎 — 基于词频的关键词匹配。

在 BM25 索引创建前先读取 ChromaDB 中的文档内容，
为每个集合建立独立的 BM25 索引。
"""

from __future__ import annotations

import logging
import re
import string
from pathlib import Path
from typing import Any

from rank_bm25 import BM25Okapi

from pipeline.vectordb import VectorStore

logger = logging.getLogger(__name__)

# ── 分词器 ────────────────────────────────────────────────────


def _tokenize(text: str) -> list[str]:
    """英文按空格/标点分词，中文按单字+二元组混合。

    BM25 对中文单字匹配较差，加入二元组提升 recall。
    """
    text = text.lower()
    # 英文 token
    en_tokens = re.findall(r"[a-z0-9]+(?:[-.][a-z0-9]+)*", text)
    # 中文单字 + 二元组
    cn_chars = re.findall(r"[一-鿿]", text)
    cn_bigrams = [f"{cn_chars[i]}{cn_chars[i+1]}" for i in range(len(cn_chars) - 1)]
    return en_tokens + cn_chars + cn_bigrams


# ── 索引管理器 ────────────────────────────────────────────────


class BM25Index:
    """BM25 索引，按集合独立管理。"""

    def __init__(self, store: VectorStore | None = None) -> None:
        self._store = store or VectorStore()
        self._indexes: dict[str, BM25Okapi] = {}
        self._documents: dict[str, list[dict[str, Any]]] = {}

    def build(self, collection: str) -> None:
        """从 ChromaDB 读取文档，构建 BM25 索引。"""
        try:
            import chromadb  # noqa: F401
            coll = self._store._client.get_collection(collection)
        except Exception:
            logger.warning("[BM25] 集合 %s 不存在", collection)
            return

        records = coll.get(include=["documents", "metadatas"])
        docs = records.get("documents", []) or []
        metas = records.get("metadatas", []) or []

        corpus: list[str] = []
        doc_items: list[dict[str, Any]] = []

        for i, doc_text in enumerate(docs):
            if not doc_text:
                continue
            # 用文档文本 + 元数据中的标题共同构建语料
            title = (metas[i] or {}).get("title", "") if i < len(metas) else ""
            full_text = f"{title} {doc_text}"
            corpus.append(full_text)
            doc_items.append({
                "text": doc_text,
                "metadata": metas[i] if i < len(metas) else {},
            })

        if not corpus:
            logger.warning("[BM25] %s 语料为空", collection)
            return

        tokenized_corpus = [_tokenize(t) for t in corpus]
        self._indexes[collection] = BM25Okapi(tokenized_corpus)
        self._documents[collection] = doc_items
        logger.info("[BM25] %s 索引构建完成: %d 篇", collection, len(corpus))

    def build_all(self) -> None:
        """为所有集合构建索引。"""
        for coll in ["news", "policy", "price"]:
            self.build(coll)

    def search(
        self, query: str, collection: str, top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """BM25 检索。

        :returns: [{text, metadata, score}, ...]
        """
        index = self._indexes.get(collection)
        if index is None:
            self.build(collection)
            index = self._indexes.get(collection)
        if index is None:
            return []

        tokenized_query = _tokenize(query)
        scores = index.get_scores(tokenized_query)
        doc_items = self._documents.get(collection, [])

        # 按分数排序取 top_k
        ranked = sorted(
            [(i, scores[i]) for i in range(len(scores)) if scores[i] > 0],
            key=lambda x: -x[1],
        )[:top_k]

        results: list[dict[str, Any]] = []
        for idx, score in ranked:
            if idx < len(doc_items):
                results.append({
                    "text": doc_items[idx]["text"],
                    "metadata": doc_items[idx]["metadata"],
                    "score": round(float(score), 4),
                    "source": collection,
                    "method": "bm25",
                })
        return results
