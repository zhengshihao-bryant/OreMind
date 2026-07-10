"""混合检索器 — Hybrid Search & Reranking 引擎。

检索管道:
  1. Query Transform  — 查询扩展 (同义词/中英互补)
  2. Intent Router    — 意图识别 + 元数据过滤
  3. Hybrid Search    — BM25 (稀疏) + Vector (密集) → RRF 融合
  4. Reranker         — Cross-Encoder 重排序 → Top-3

用法:
  from pipeline.retriever import HybridRetriever
  retriever = HybridRetriever()
  results = retriever.search("最近的铜价走势")
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from pipeline.embedding import embed
from pipeline.retriever.bm25 import BM25Index
from pipeline.retriever.intent import (
    build_filter, detect_intent, route_collections,
)
from pipeline.retriever.query_transform import expand_query
from pipeline.retriever.reranker import rerank
from pipeline.vectordb import VectorStore

logger = logging.getLogger(__name__)


# ── RRF 融合 ──────────────────────────────────────────────────


def _rrf_fuse(
    bm25_results: list[dict[str, Any]],
    vector_results: list[dict[str, Any]],
    k: int = 60,
) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion — 融合 BM25 和向量检索结果。"""
    score_map: dict[str, dict[str, Any]] = {}

    for rank, item in enumerate(bm25_results):
        key = item.get("text", "")[:100]
        score_map[key] = {
            "text": item["text"],
            "metadata": item.get("metadata", {}),
            "source": item.get("source", ""),
            "rrf_score": 1.0 / (k + rank + 1),
            "bm25_score": item.get("score", 0),
            "vector_score": 0.0,
        }

    for rank, item in enumerate(vector_results):
        key = item.get("text", "")[:100]
        if key in score_map:
            score_map[key]["rrf_score"] += 1.0 / (k + rank + 1)
            score_map[key]["vector_score"] = item.get("distance", 0)
        else:
            score_map[key] = {
                "text": item["text"],
                "metadata": item.get("metadata", {}),
                "source": item.get("source", ""),
                "rrf_score": 1.0 / (k + rank + 1),
                "bm25_score": 0.0,
                "vector_score": item.get("distance", 0),
            }

    fused = sorted(score_map.values(), key=lambda x: -x["rrf_score"])
    return fused


# ══════════════════════════════════════════════════════════════
# 主检索器
# ══════════════════════════════════════════════════════════════


class HybridRetriever:
    """混合检索器。

    :param top_k_hybrid: 初筛阶段每路召回的条数（默认 20）
    :param top_n_final:  重排序后最终返回条数（默认 3）
    """

    def __init__(
        self,
        top_k_hybrid: int = 20,
        top_n_final: int = 3,
    ) -> None:
        self.top_k_hybrid = top_k_hybrid
        self.top_n_final = top_n_final
        self._store = VectorStore()
        self._bm25 = BM25Index(self._store)

    # ── 核心检索 ──────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int | None = None,
        filter_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行完整检索管道。

        :param query:           用户查询
        :param top_k:           返回条数（默认 self.top_n_final）
        :param filter_override: 强制覆盖过滤条件
        :returns:               {query, intent, filter, expanded_queries,
                                 latency_ms, results: [{text, metadata, scores}]}
        """
        start = time.perf_counter()

        # 1. 查询扩展（生成中英互补的多条子查询）
        expanded = expand_query(query)
        primary_query = expanded[0]

        # 实体+意图交叉修正
        from pipeline.retriever.query_transform import extract_entities
        entities = extract_entities(primary_query)
        intent = detect_intent(primary_query)
        # 有 commodity + 不是明确的新闻/政策/含 "mining" → 提升为价格意图
        if ("commodity" in entities
            and intent not in ("news", "policy")
            and "mining" not in primary_query.lower()
            and "矿" not in primary_query
            and "project" not in primary_query.lower()
            and "勘探" not in primary_query
            and "acquisition" not in primary_query.lower()
        ):
            intent = "price"

        metadata_filter = filter_override or build_filter(intent, primary_query)
        collections = route_collections(intent)

        logger.info(
            "Query: %s | Intent: %s | Filter: %s | Colls: %s | Expanded: %s",
            primary_query, intent, metadata_filter, collections, expanded,
        )

        # 3. 混合检索（用所有扩展子查询分别检索后合并）
        all_candidates: list[dict[str, Any]] = []
        seen_texts: set[str] = set()

        for search_query in expanded:
            for coll in collections:
                # BM25 检索（用每个扩展子查询分别搜索）
                bm25_results = self._bm25.search(
                    search_query, coll, top_k=self.top_k_hybrid,
                )
                for r in bm25_results:
                    dedup_key = r.get("text", "")[:100]
                    if dedup_key not in seen_texts:
                        seen_texts.add(dedup_key)
                        all_candidates.append(r)

                # 向量检索（用原始查询进行语义搜索）
                try:
                    query_vec = embed([search_query])[0]
                    vector_results = self._store.search(
                        collection=coll,
                        query_embedding=query_vec,
                        top_k=self.top_k_hybrid,
                    )
                    for r in vector_results:
                        item = {
                            "text": r["document"],
                            "metadata": r["metadata"],
                            "source": coll,
                            "method": "vector",
                            "distance": r["distance"],
                        }
                        dedup_key = item.get("text", "")[:100]
                        if dedup_key not in seen_texts:
                            seen_texts.add(dedup_key)
                            all_candidates.append(item)
                except Exception as e:
                    logger.warning("向量检索 %s 失败: %s", coll, e)

        # 4. RRF 融合
        fused = _rrf_fuse(
            [c for c in all_candidates if c.get("method") == "bm25"],
            [c for c in all_candidates if c.get("method") == "vector"],
        )

        if not fused:
            elapsed = (time.perf_counter() - start) * 1000
            return {
                "query": query, "intent": intent,
                "filter": metadata_filter,
                "expanded_queries": expanded,
                "latency_ms": round(elapsed, 1),
                "results": [],
            }

        # 5. Cross-Encoder 重排序（中文查询跳过，RRF 分数已够）
        top_n = top_k or self.top_n_final
        has_chinese = bool(re.search(r"[一-鿿]", primary_query))
        if has_chinese:
            # 中文查询：直接用 RRF 排序
            final = sorted(fused, key=lambda x: -x["rrf_score"])[:top_n]
            for r in final:
                r["rerank_score"] = r["rrf_score"]
        else:
            final = rerank(primary_query, fused, top_n=min(top_n * 2, len(fused)))
            final = final[:top_n]

        # 6. 置信度：BM25 分数或向量相似度的绝对值
        for r in final:
            bm25_raw = r.get("bm25_score", 0)
            vec_dist = r.get("vector_score", 1)  # 默认 1（完全不相似）
            vec_sim = 1 - vec_dist
            if bm25_raw > 0:
                # BM25 原始分 → 0~1
                r["confidence"] = round(min(bm25_raw / 10, 1), 4)
            else:
                # 向量余弦距离 → 相似度
                r["confidence"] = round(max(vec_sim, 0), 4)

        elapsed = (time.perf_counter() - start) * 1000
        logger.info("检索完成: %d 候选 → %d 最终 (%d ms)", len(fused), len(final), round(elapsed))

        return {
            "query": query,
            "intent": intent,
            "filter": metadata_filter,
            "expanded_queries": expanded,
            "latency_ms": round(elapsed, 1),
            "results": [
                {
                    "text": r["text"],
                    "metadata": r.get("metadata", {}),
                    "source": r.get("source", ""),
                    "scores": {
                        "rrf": r.get("rrf_score", 0),
                        "rerank": r.get("rerank_score", 0),
                        "confidence": r.get("confidence", 0),
                    },
                }
                for r in final
            ],
        }

    # ── 性能评估 ──────────────────────────────────────────────

    def evaluate(
        self, test_set: list[dict[str, Any]],
    ) -> dict[str, float]:
        """在标注测试集上计算 Precision@K / Recall / MRR。

        :param test_set: [{query, relevant_texts: [text, ...], relevant_collections: [coll, ...]}]
        """
        precision_list: list[float] = []
        recall_list: list[float] = []
        mrr_list: list[float] = []

        for case in test_set:
            query = case["query"]
            relevant = set(case.get("relevant_texts", []))
            result = self.search(query, top_k=5)
            retrieved = [r["text"] for r in result["results"]]

            if not retrieved:
                precision_list.append(0.0)
                recall_list.append(0.0)
                mrr_list.append(0.0)
                continue

            hits = sum(1 for r in retrieved if r in relevant)
            precision_list.append(hits / len(retrieved))

            if relevant:
                recall_list.append(hits / len(relevant))
            else:
                recall_list.append(0.0)

            # MRR: 第一个相关结果的位置
            for rank, r in enumerate(retrieved):
                if r in relevant:
                    mrr_list.append(1.0 / (rank + 1))
                    break
            else:
                mrr_list.append(0.0)

        n = len(test_set)
        return {
            "test_size": n,
            "precision@5": round(sum(precision_list) / n, 4) if n else 0,
            "recall@5": round(sum(recall_list) / n, 4) if n else 0,
            "mrr@5": round(sum(mrr_list) / n, 4) if n else 0,
        }
