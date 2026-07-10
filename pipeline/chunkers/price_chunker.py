"""Price Chunker — 记录级切块，不分割。

策略: 每条价格记录 = 1 个 chunk，不切分。

为什么:
  价格数据是高度结构化的键值对 (commodity + date + price)。
  每一条记录本身就是完整信息单元，切割会破坏语义。
  记录级切块保证检索时能精确命中 "Copper 2026-07-09 10200 USD/ton"。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


def chunk(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """不切块，每条记录作为一个独立 chunk。

    :param items:  价格 item 列表 (含 title / content / metadata)
    :returns:      [{id, document, metadata}, ...]
    """
    chunks: list[dict[str, Any]] = []
    for item in items:
        title = item.get("title", "")
        content = item.get("content", "")
        text = f"{title}. {content}" if title and content else (title or content)
        if not text.strip():
            continue

        raw_meta = item.get("metadata", {})
        dedup_key = f"price:{raw_meta.get('commodity', '')}:{raw_meta.get('date', '')}"
        chunk_id = hashlib.md5(dedup_key.encode()).hexdigest()[:16]

        chunks.append({
            "id": chunk_id,
            "document": text,
            "metadata": {**raw_meta, "source": "price", "category": "price"},
        })

    logger.info("[chunk-price] %d items → %d chunks", len(items), len(chunks))
    return chunks
