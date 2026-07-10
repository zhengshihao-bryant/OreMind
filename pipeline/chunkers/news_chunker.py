"""News Chunker — 按段落切块。

策略: 将新闻正文按自然段落拆分。
  1. 以双换行 \n\n 为分隔符拆为段落
  2. 过短段落 (< 50 字符) 合并到上一段
  3. 段落逐条作为独立 chunk

为什么不用 RecursiveCharacterTextSplitter:
  新闻的语义边界是段落，不是 token 数。
  段落级切块保证每个 chunk 内容完整、无截断，
  避免 "标题在 chunk-1、正文在 chunk-2" 的断裂。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from pipeline.config import settings

logger = logging.getLogger(__name__)


def chunk(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按段落切分新闻。

    :param items:  新闻 item 列表 (含 title / content / url / published / source)
    :returns:      [{id, document, metadata}, ...]
    """
    chunks: list[dict[str, Any]] = []
    for item in items:
        title = item.get("title", "")
        content = item.get("content", "")
        text = f"{title}\n\n{content}" if title and content else (title or content)
        if not text.strip():
            continue

        # 1. 按双换行拆分段落
        raw_paragraphs = [
            p.strip() for p in text.split("\n\n") if p.strip()
        ]

        # 2. 短段落合并到上一段
        merged: list[str] = []
        for p in raw_paragraphs:
            if merged and len(p) < 50:
                merged[-1] += "\n" + p
            else:
                merged.append(p)

        # 3. 每段作为一个 chunk（不超过 max_chars 时保持完整）
        for idx, para in enumerate(merged):
            if len(para) < 20:
                continue

            # 超长段落内部按句号切分
            if len(para) > settings.CHUNK_SIZE * 2:
                sentences = [s.strip() for s in para.replace("。", "。\n").split("\n") if s.strip()]
                for sidx, sent in enumerate(sentences):
                    if len(sent) < 20:
                        continue
                    cid = hashlib.md5(
                        f"news:{item.get('url', item.get('title', ''))}:p{idx}:s{sidx}".encode()
                    ).hexdigest()[:16]
                    chunks.append({
                        "id": cid,
                        "document": sent,
                        "metadata": {
                            "title": title,
                            "url": item.get("url", ""),
                            "published": item.get("published", ""),
                            "source": item.get("source", "news"),
                            "category": "news",
                        },
                    })
            else:
                cid = hashlib.md5(
                    f"news:{item.get('url', item.get('title', ''))}:p{idx}".encode()
                ).hexdigest()[:16]
                chunks.append({
                    "id": cid,
                    "document": para,
                    "metadata": {
                        "title": title,
                        "url": item.get("url", ""),
                        "published": item.get("published", ""),
                        "source": item.get("source", "news"),
                        "category": "news",
                    },
                })

    logger.info("[chunk-news] %d items → %d chunks", len(items), len(chunks))
    return chunks
