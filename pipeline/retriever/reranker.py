"""交叉编码器重排序 — Cross-Encoder Reranker。

对初筛 Top-K 结果用小模型逐对打分，重排输出最精准的 Top-N。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 全局单例
_model = None
_tokenizer = None


def _load_model():
    """懒加载 Cross-Encoder 模型。"""
    global _model, _tokenizer
    if _model is not None:
        return _model, _tokenizer

    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    model_name = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    logger.info("加载 Reranker 模型: %s ...", model_name)
    _tokenizer = AutoTokenizer.from_pretrained(model_name)
    _model = AutoModelForSequenceClassification.from_pretrained(model_name)
    logger.info("Reranker 加载完成")
    return _model, _tokenizer


def rerank(
    query: str,
    candidates: list[dict[str, Any]],
    top_n: int = 3,
) -> list[dict[str, Any]]:
    """用 Cross-Encoder 对候选结果重排序。

    :param query:      原始用户查询
    :param candidates: BM25 + Vector 的合并候选项
    :param top_n:      最终返回 Top-N 条
    :returns:          按相关性降序排列，每项含 score (0-1)
    """
    import torch

    if not candidates:
        return []

    model, tokenizer = _load_model()
    texts = [c.get("text", "")[:512] for c in candidates]

    # 构造 query-doc 对
    pairs = [(query, text) for text in texts]

    inputs = tokenizer(
        pairs,
        padding=True,
        truncation=True,
        return_tensors="pt",
        max_length=512,
    )

    with torch.no_grad():
        outputs = model(**inputs)
        scores = outputs.logits.squeeze(-1).tolist()

    if not isinstance(scores, list):
        scores = [scores]

    # 归一化到 0-1 (sigmoid)
    import math
    normalized = [1.0 / (1.0 + math.exp(-s)) for s in scores]

    # 合并并排序
    for i, cand in enumerate(candidates):
        cand["rerank_score"] = round(normalized[i] if i < len(normalized) else 0, 4)

    reranked = sorted(candidates, key=lambda x: -x.get("rerank_score", 0))[:top_n]

    logger.debug(
        "Rerank: %d → %d, top1=%.4f",
        len(candidates), len(reranked),
        reranked[0]["rerank_score"] if reranked else 0,
    )
    return reranked
