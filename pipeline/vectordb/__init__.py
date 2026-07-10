"""向量数据库封装 — ChromaDB 本地持久化"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings

from pipeline.config import settings as _settings

logger = logging.getLogger(__name__)

DEFAULT_PERSIST_DIR = _settings.VECTORDB_PATH


class VectorStore:
    """ChromaDB 封装，支持多集合管理。

    :param persist_dir: 持久化目录（默认 data/vectordb/）
    """

    def __init__(self, persist_dir: str | Path | None = None) -> None:
        self.persist_dir = Path(persist_dir or DEFAULT_PERSIST_DIR)
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        logger.info("ChromaDB 持久化目录: %s", self.persist_dir)

    # ── 集合管理 ──────────────────────────────────────────────

    def get_or_create_collection(self, name: str, dimension: int = 384) -> chromadb.Collection:
        """获取或创建集合。

        :param name:      集合名称（如 news, policy, price）
        :param dimension: 向量维度（bge-small-en-v1.5 = 384）
        """
        try:
            collection = self._client.get_collection(name)
            logger.info("集合 %s 已存在 (size=%d)", name, collection.count())
        except (ValueError, chromadb.errors.NotFoundError):
            collection = self._client.create_collection(
                name=name,
                metadata={"hnsw:space": "cosine", "dimension": dimension},
            )
            logger.info("创建集合 %s", name)
        return collection

    def delete_collection(self, name: str) -> None:
        """删除集合（重建时用）。"""
        try:
            self._client.delete_collection(name)
            logger.info("删除集合 %s", name)
        except (ValueError, chromadb.errors.NotFoundError):
            pass

    # ── 增 ────────────────────────────────────────────────────

    def add(
        self,
        collection: str,
        ids: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
        documents: list[str],
    ) -> None:
        """批量添加文档到集合。

        :param collection: 集合名称
        :param ids:        id 列表
        :param embeddings: 向量列表
        :param metadatas:  元数据列表（自动清洗 None/非标准类型）
        :param documents:  文本列表
        """
        # 清洗元数据：ChromaDB 只接受 str/int/float/bool
        cleaned: list[dict[str, Any]] = []
        for md in metadatas:
            clean: dict[str, Any] = {}
            for k, v in md.items():
                if isinstance(v, str):
                    clean[k] = v[:512]  # 截断过长字符串
                elif isinstance(v, (int, float, bool)):
                    clean[k] = v
                elif v is not None:
                    clean[k] = str(v)
                # None 值跳过
            cleaned.append(clean)

        coll = self.get_or_create_collection(collection)
        coll.add(ids=ids, embeddings=embeddings, metadatas=cleaned, documents=documents)
        logger.info("[%s] 添加 %d 条", collection, len(ids))

    # ── 查 ────────────────────────────────────────────────────

    def search(
        self,
        collection: str,
        query_embedding: list[float],
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """向量相似度搜索。

        :param collection:      集合名称
        :param query_embedding: 查询向量
        :param top_k:           Top-K
        :returns:               [{id, document, metadata, distance}, ...]
        """
        try:
            coll = self._client.get_collection(collection)
        except (ValueError, chromadb.errors.NotFoundError):
            logger.warning("集合 %s 不存在", collection)
            return []
        results = coll.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, coll.count()),
        )
        items: list[dict[str, Any]] = []
        for i in range(len(results["ids"][0])):
            items.append({
                "id": results["ids"][0][i],
                "document": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i],
            })
        return items

    # ── 清 ────────────────────────────────────────────────────

    def count(self, collection: str) -> int:
        """集合中文档数量。"""
        try:
            return self._client.get_collection(collection).count()
        except (ValueError, chromadb.errors.NotFoundError):
            return 0

    def __repr__(self) -> str:
        return f"VectorStore({self.persist_dir})"
