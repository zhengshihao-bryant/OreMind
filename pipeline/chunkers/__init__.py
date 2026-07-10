"""切块模块 — 按数据类型选择策略。

分派:
  - news   → news_chunker    段落级切块
  - policy → policy_chunker  标题感知切块
  - price  → price_chunker   记录级 (不切)
"""

from __future__ import annotations

from typing import Any

from pipeline.chunkers import news_chunker, policy_chunker, price_chunker


def chunk(items: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """按数据类型分派到专属 chunker。"""
    if source == "news":
        return news_chunker.chunk(items)
    if source == "policy":
        return policy_chunker.chunk(items)
    if source == "price":
        return price_chunker.chunk(items)
    raise ValueError(f"未知数据类型: {source}")

