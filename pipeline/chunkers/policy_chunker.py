"""Policy Chunker — 按标题感知切块。

策略: 保留文档的标题层级结构。
  1. 识别 "##", "**", "第X条" 等标题标记
  2. 每个标题及其下级内容作为一个独立 chunk
  3. 无标题的段落则按自然段分组

为什么:
  政策文档的语义边界是「节/条款」，不是 token 数。
  按标题切块确保: 一条政策引用的上下文不会被拦腰截断。
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# 标题行模式: ## heading / **heading** / 第X条 / Article X / 一、 等
_HEADING_PATTERN = re.compile(
    r"^(#{2,4}\s+|第[一二三四五六七八九十百千\d]+[条章节条款]|"
    r"\d+\.\d+\s+[A-Z]|【[^】]+】|[一二三四五六七八九十]+[,、.．]\s+|"
    r"\*\*.+?\*\*)$",
    re.MULTILINE,
)


def chunk(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按标题感知切分政策文档。

    :param items:  政策 item 列表
    :returns:      [{id, document, metadata}, ...]
    """
    chunks: list[dict[str, Any]] = []
    for item in items:
        title = item.get("title", "")
        content = item.get("content", "")
        text = f"{title}\n\n{content}" if title and content else (title or content)
        if not text.strip():
            continue

        sections = _split_by_heading(text)

        for idx, sec in enumerate(sections):
            if len(sec) < 20:
                continue

            # 如果标题本身是单独的短行，合并到内容
            lines = sec.split("\n", 1)
            sec_title = lines[0].strip() if len(lines) > 1 else ""
            sec_body = lines[1].strip() if len(lines) > 1 else sec

            # 超长 section 内部按段落二次拆分
            if len(sec_body) > 1400:
                paragraphs = [p.strip() for p in sec_body.split("\n\n") if p.strip()]
                for pidx, para in enumerate(paragraphs):
                    if len(para) < 20:
                        continue
                    cid = hashlib.md5(
                        f"policy:{item.get('url', item.get('title', ''))}:s{idx}:p{pidx}".encode()
                    ).hexdigest()[:16]
                    chunks.append({
                        "id": cid,
                        "document": f"{sec_title}\n{para}" if sec_title else para,
                        "metadata": {
                            "title": title,
                            "url": item.get("url", ""),
                            "published": item.get("published", ""),
                            "source": item.get("source", "policy"),
                            "category": "policy",
                        },
                    })
            else:
                cid = hashlib.md5(
                    f"policy:{item.get('url', item.get('title', ''))}:s{idx}".encode()
                ).hexdigest()[:16]
                chunks.append({
                    "id": cid,
                    "document": sec,
                    "metadata": {
                        "title": title,
                        "url": item.get("url", ""),
                        "published": item.get("published", ""),
                        "source": item.get("source", "policy"),
                        "category": "policy",
                    },
                })

    logger.info("[chunk-policy] %d items → %d sections", len(items), len(chunks))
    return chunks


def _split_by_heading(text: str) -> list[str]:
    """按标题行拆分文本。"""
    lines = text.split("\n")
    sections: list[str] = []
    current: list[str] = []

    for line in lines:
        if _HEADING_PATTERN.match(line.strip()):
            if current:
                sections.append("\n".join(current))
            current = [line]
        else:
            current.append(line)

    if current:
        sections.append("\n".join(current))
    return sections or [text]
