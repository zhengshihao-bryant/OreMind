"""意图路由与元数据过滤 — 识别查询意图，注入精确过滤条件。

路由策略（按优先级）：
  1. 价格查询 → filter 注入 commodity/exchange/date
  2. 政策查询 → filter 注入时间范围
  3. 新闻查询 → 不加 filter，走全量检索
"""

from __future__ import annotations

import re
from typing import Any

from pipeline.retriever.query_transform import (
    COMMODITY_ALIASES, EXCHANGE_ALIASES, _any_alias, _match_alias,
)

# ── 意图类型 ──────────────────────────────────────────────────

INTENT_PRICE = "price"
INTENT_POLICY = "policy"
INTENT_NEWS = "news"
INTENT_GENERAL = "general"

# 价格触发词
_PRICE_TRIGGERS = [
    "price", "价", "价格", "行情", "报价", "多少钱", "how much",
    "cost", "settlement", "close price", "spot", "现货",
    "期货价格", "futures price", "铜价", "金价", "银价",
    "锂价", "铁矿石价格",
]

# 政策触发词
_POLICY_TRIGGERS = [
    "policy", "政策", "regulation", "法规", "strategy", "战略",
    "plan", "规划", "supply", "供应", "demand", "需求",
    "trade", "贸易", "tariff", "关税", "sanction", "制裁",
    "供应链", "supply chain", "critical mineral", "关键矿产",
    "security", "安全", "energy transition", "能源转型",
]


def detect_intent(query: str) -> str:
    """识别查询意图。

    :returns: "price" | "policy" | "news" | "general"
    """
    q = query.lower().strip()

    # 1. 价格意图：含 commodity + 价格词
    has_commodity = any(
        _match_alias(alias, q) for aliases in COMMODITY_ALIASES.values() for alias in aliases
    )
    has_price_word = any(trigger in q for trigger in _PRICE_TRIGGERS)

    if has_commodity and has_price_word:
        return INTENT_PRICE

    # 2. 新闻意图
    if ("新闻" in q or q == "news" or "最新" in q
        or "mining" in q or "矿" in q
        or "project" in q or "公司" in q
        or "acquisition" in q or "勘探" in q):
        return INTENT_NEWS

    # 3. 纯价格词 + 数字
    if has_price_word and re.search(r"\d{4}", q):
        return INTENT_PRICE

    # 4. 政策意图
    if any(trigger in q for trigger in _POLICY_TRIGGERS):
        return INTENT_POLICY

    # 5. 默认
    return INTENT_GENERAL


def build_filter(
    intent: str, query: str, max_age_days: int = 30,
) -> dict[str, Any] | None:
    """根据意图和查询构造 ChromaDB metadata filter。

    支持:
      - commodity / exchange 精确匹配
      - category 限定
      - 时间范围过滤 (policy 场景)

    :param intent:      意图类型
    :param query:       原始查询
    :param max_age_days: 政策时效天数
    :returns:           ChromaDB where 过滤条件，None = 不过滤
    """
    q = query.lower()

    # ----- price -----
    if intent == INTENT_PRICE:
        conditions: dict[str, Any] = {}

        for commodity, aliases in COMMODITY_ALIASES.items():
            if _any_alias(aliases, q):
                conditions["commodity"] = commodity.capitalize()
                break

        for exchange, aliases in EXCHANGE_ALIASES.items():
            if _any_alias(aliases, q):
                conditions["exchange"] = exchange.upper()
                break

        # 含时间词 → 注入 date 范围
        if "近" in q or "最近" in q or "current" in q or "today" in q or "最新" in q:
            from datetime import datetime, timezone, timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
            conditions["date"] = {"$gte": cutoff}

        conditions["category"] = "price"
        return conditions if conditions else {"category": "price"}

    # ----- policy -----
    if intent == INTENT_POLICY:
        from datetime import datetime, timezone, timedelta
        base_filter: dict[str, Any] = {"category": "policy"}
        # 如有时间词，注入 published 范围
        if "近" in q or "最近" in q or "latest" in q or "current" in q:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
            base_filter["published"] = {"$gte": cutoff}
        return base_filter

    # ----- news -----
    if intent == INTENT_NEWS:
        return {"category": "news"}

    return None


def route_collections(intent: str) -> list[str]:
    """路由到匹配的集合。

    :returns: 集合名称列表
    """
    if intent == INTENT_PRICE:
        return ["price"]
    if intent == INTENT_POLICY:
        return ["policy", "news"]
    if intent == INTENT_NEWS:
        return ["news"]
    return ["news", "policy", "price"]
