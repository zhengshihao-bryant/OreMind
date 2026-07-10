"""查询转换 — 口语化查询 → 多维度检索表达式。

对用户 query 进行同义词扩展和技术指标补充，提升召回率。
"""

from __future__ import annotations

import re

# ── 辅助函数 ──────────────────────────────────────────────────


def _match_alias(alias: str, text: str) -> bool:
    """智能别名匹配：英文用词边界，中文直接子串。"""
    if re.match(r"^[a-z]", alias):
        # 英文别名：要求词边界（避免 "al" 命中 "critical"）
        return bool(re.search(rf"\b{re.escape(alias)}\b", text))
    # 中文/数字：直接子串匹配
    return alias in text


def _any_alias(aliases: list[str], text: str) -> bool:
    """检查 text 中是否包含 aliases 中的任意一个。"""
    return any(_match_alias(a, text) for a in aliases)


# ── 领域词典 ──────────────────────────────────────────────────

# 国家/地区别名
REGION_ALIASES: dict[str, list[str]] = {
    "australia": ["australia", "australian", "澳洲", "澳大利亚"],
    "china": ["china", "chinese", "中国", "中国的"],
    "us": ["united states", "usa", "us", "american", "美国"],
}

# commodity 同义词映射
COMMODITY_ALIASES: dict[str, list[str]] = {
    "copper": ["copper", "cu", "铜", "电解铜", "精铜"],
    "aluminum": ["aluminum", "aluminium", "al", "铝", "电解铝"],
    "zinc": ["zinc", "zn", "锌"],
    "nickel": ["nickel", "ni", "镍"],
    "tin": ["tin", "sn", "锡"],
    "lead": ["lead", "pb", "铅"],
    "gold": ["gold", "au", "黄金", "金"],
    "silver": ["silver", "ag", "白银", "银"],
    "platinum": ["platinum", "pt", "铂金", "铂"],
    "palladium": ["palladium", "pd", "钯金", "钯"],
    "iron ore": ["iron ore", "iron", "fe", "铁矿石", "铁矿"],
    "lithium": ["lithium", "li", "锂", "碳酸锂", "锂电"],
    "rare earth": ["rare earth", "re", "稀土"],
}

EXCHANGE_ALIASES: dict[str, list[str]] = {
    "lme": ["lme", "london metal exchange", "伦敦金属交易所", "伦"],
    "shfe": ["shfe", "shanghai futures exchange", "上期所", "上海期货交易所"],
    "cme": ["cme", "comex", "chicago mercantile"],
    "nysex": ["nysex", "nymex"],
    "sgx": ["sgx", "singapore exchange", "新交所"],
}

# 价格指标关键词
PRICE_INDICATORS = [
    "price", "价", "价格", "行情", "报价", "settlement", "close",
    "spot", "现货", "期货", "futures",
]

# 政策/新闻关键词
POLICY_INDICATORS = [
    "policy", "政策", "regulation", "法规",
    "strategy", "战略", "plan", "规划",
    "supply", "供应", "demand", "需求",
    "market", "市场", "industry", "行业",
    "trade", "贸易", "export", "出口", "import", "进口",
]

TIME_INDICATORS = [
    "recent", "最近", "latest", "最新", "today", "今天",
    "this week", "本周", "this month", "本月",
]


def expand_query(query: str) -> list[str]:
    """将用户查询扩展为多个检索子查询。

    :returns: [原查询, 英文扩展, 中文扩展, ...]
    """
    q = query.lower().strip()
    queries = [q]

    # 按空格拆词
    words = set(re.findall(r"[a-z]+|[一-鿿]+", q))

    # commodity 扩展
    for commodity, aliases in COMMODITY_ALIASES.items():
        if _any_alias(aliases, q):
            en_aliases = [a for a in aliases if re.match(r"^[a-z]", a)]
            cn_aliases = [a for a in aliases if re.match(r"[一-鿿]", a)]
            if en_aliases and cn_aliases:
                queries.append(f"{en_aliases[0]} price")
                queries.append(f"{cn_aliases[0]} 价格")
            break

    # exchange 扩展
    for exchange, aliases in EXCHANGE_ALIASES.items():
        if _any_alias(aliases, q):
            queries.append(f"{exchange.upper()} {q}")
            break

    # region 扩展（中英互补）
    for region, aliases in REGION_ALIASES.items():
        if _any_alias(aliases, q):
            en_alias = next((a for a in aliases if re.match(r"^[a-z]", a)), region)
            cn_alias = next((a for a in aliases if re.match(r"[一-鿿]", a)), region)
            if en_alias != cn_alias:
                queries.append(f"{en_alias} {q}")
                queries.append(f"{cn_alias} {q}")
            break

    # 价格指标 → commodity 单位补充
    if _any_alias(PRICE_INDICATORS, q):
        for commodity, aliases in COMMODITY_ALIASES.items():
            if _any_alias(aliases, q):
                queries.append(f"{commodity} price usd ton")
                break

    # 去重，保留最多 5 条
    seen: set[str] = set()
    unique: list[str] = []
    for qs in queries:
        if qs not in seen:
            seen.add(qs)
            unique.append(qs)
            if len(unique) >= 5:
                break

    return unique


def extract_entities(query: str) -> dict[str, str]:
    """从查询中提取 commodity、exchange 等实体。"""
    q = query.lower()
    result: dict[str, str] = {}

    for commodity, aliases in COMMODITY_ALIASES.items():
        if _any_alias(aliases, q):
            result["commodity"] = commodity.capitalize()
            break

    for exchange, aliases in EXCHANGE_ALIASES.items():
        if _any_alias(aliases, q):
            result["exchange"] = exchange.upper()
            break

    for region, aliases in REGION_ALIASES.items():
        if _any_alias(aliases, q):
            result["region"] = region.capitalize()
            break

    # 时间判断
    for ind in TIME_INDICATORS:
        if ind in q:
            result["time"] = "recent"
            break

    return result
