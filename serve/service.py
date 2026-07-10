"""RAG Service — 业务逻辑层，封装 RAG 管道的创建与管理。"""

from __future__ import annotations

import logging
import time
from typing import Any

from pipeline.rag import RAGPipeline
from pipeline.retriever import HybridRetriever

logger = logging.getLogger(__name__)


class RAGService:
    """RAG 服务，管理检索器与生成管道的生命周期。"""

    def __init__(self) -> None:
        logger.info("初始化 RAGService ...")
        t0 = time.perf_counter()
        self.retriever = HybridRetriever()
        self.rag = RAGPipeline(retriever=self.retriever)
        logger.info("RAGService 就绪 (%.1fs)", time.perf_counter() - t0)

    def query(self, question: str, top_k: int = 3) -> dict[str, Any]:
        """完整 RAG 查询：检索 + 生成。"""
        t0 = time.perf_counter()
        result = self.rag.query(question, top_k=top_k)
        elapsed = time.perf_counter() - t0
        logger.info(
            "query | len=%d intent=%s sources=%d retrieval=%dms llm=%dms total=%dms",
            len(question), result.get("intent", ""),
            len(result.get("sources", [])),
            result.get("latency_ms", {}).get("retrieval", 0),
            result.get("latency_ms", {}).get("llm", 0),
            result.get("latency_ms", {}).get("total", 0),
        )
        return result

    def search(self, question: str, top_k: int = 3) -> dict[str, Any]:
        """仅检索，不生成。"""
        t0 = time.perf_counter()
        result = self.retriever.search(question, top_k=top_k)
        logger.info("search | %s (%dms)", question[:40], result.get("latency_ms", 0))
        return result

    def close(self) -> None:
        self.rag.close()
        logger.info("RAGService 已关闭")
