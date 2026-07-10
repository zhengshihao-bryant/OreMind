"""Normalizer — 字段标准化。

职责: 将采集的原始字段统一为标准格式，不做内容提取。
   collectors → raw data
     ↓
   cleaners  → HTML 剥离、去噪声
     ↓
   normalizer → 字段标准化 (日期/币种/单位)
     ↓
   dedup → chunk → embed → store

标准化规则:
  - date: 统一为 YYYY-MM-DD 格式
  - currency: 统一为大写 USD / CNY
  - unit: 统一为小写 ton / oz / lb / share
  - title: 首尾空白 trim
  - None → 空字符串
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


def normalize(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """批量标准化。"""
    return [normalize_item(item) for item in items]


def normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    """标准化单条记录。"""
    result = dict(item)

    # title
    if isinstance(result.get("title"), str):
        result["title"] = result["title"].strip()
    elif result.get("title") is None:
        result["title"] = ""

    # content
    if result.get("content") is None:
        result["content"] = ""

    # published → ISO8601
    pub = result.get("published")
    if pub and isinstance(pub, str):
        result["published"] = _normalize_date(pub)

    # metadata 内部字段 (price data)
    meta = result.get("metadata")
    if isinstance(meta, dict):
        result["metadata"] = _normalize_metadata(meta)

    return result


def _normalize_date(date_str: str) -> str:
    """将各种日期格式转为 YYYY-MM-DD。"""
    # 已经是 ISO8601 格式
    if re.match(r"^\d{4}-\d{2}-\d{2}", date_str):
        return date_str[:10]

    # RFC 2822: "Tue, 09 Dec 2025 12:00:00 +0000"
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        if dt:
            return dt.strftime("%Y-%m-%d")
    except Exception:
        pass

    # 中文: "2026年7月9日"
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", date_str)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"

    # 回退: 截取前10字符
    return date_str[:10]


def _normalize_currency(currency: str) -> str:
    """币种统一大写。"""
    c = currency.upper().strip()
    return c if c in ("USD", "CNY", "EUR", "GBP", "JPY") else currency


def _normalize_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """标准化 metadata 中的价格字段。"""
    m = dict(meta)

    if isinstance(m.get("currency"), str):
        m["currency"] = _normalize_currency(m["currency"])

    if isinstance(m.get("unit"), str):
        m["unit"] = m["unit"].lower().strip()

    if isinstance(m.get("date"), str):
        m["date"] = _normalize_date(m["date"])

    return {k: v for k, v in m.items() if v is not None}
