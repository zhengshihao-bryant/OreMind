"""嵌入模型封装 — BAAI/bge-small-en-v1.5"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# 全局单例，避免重复加载模型
_model = None


from pipeline.config import settings


def get_model(model_name: str | None = None) -> Any:
    if model_name is None:
        model_name = settings.EMBED_MODEL
    """懒加载 BGE 嵌入模型。"""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        logger.info("加载嵌入模型: %s ...", model_name)
        _model = SentenceTransformer(model_name)
        logger.info("嵌入模型加载完成 (dimension=%d)", _model.get_embedding_dimension())
    return _model


def embed(texts: list[str], batch_size: int = 64) -> list[list[float]]:
    """将文本列表转为嵌入向量。

    :param texts:      文本列表
    :param batch_size: 批处理大小
    :returns:          [[dim_0, dim_1, ...], ...] 归一化后的向量
    """
    model = get_model()
    # BGE 模型建议在 query 时加前缀，段落检索不用加
    logger.debug("嵌入 %d 段文本 (batch=%d) ...", len(texts), batch_size)
    embeddings = model.encode(texts, batch_size=batch_size, show_progress_bar=False)
    # 归一化（余弦相似度要求）
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / np.where(norms == 0, 1, norms)
    return embeddings.tolist()


def embed_dimension() -> int:
    """返回嵌入向量维度。"""
    return get_model().get_embedding_dimension()
