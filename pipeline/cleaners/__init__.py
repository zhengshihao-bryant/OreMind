"""清洗模块 — HTML 清理、正文提取、标题/正文过滤。

用法:
  from pipeline.cleaners import extract_content, is_policy_relevant
"""

from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# 正文提取
# ══════════════════════════════════════════════════════════════


def extract_content(html: str | None) -> str:
    """从 HTML 提取正文（去标签、去噪声）。

    :param html: 原始 HTML
    :returns:    纯文本正文（段落用换行拼接）
    """
    if not html:
        return ""

    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    paragraphs: list[str] = []
    for p in soup.find_all("p"):
        text = p.get_text(strip=True)
        if len(text) > 10:  # 过滤短段落
            paragraphs.append(text)

    return "\n".join(paragraphs)


def extract_title(html: str | None) -> str:
    """从 HTML 提取标题（<h1> 或 <title>）。"""
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)
    title_tag = soup.find("title")
    return title_tag.get_text(strip=True) if title_tag else ""


# ══════════════════════════════════════════════════════════════
# 标题级过滤
# ══════════════════════════════════════════════════════════════

# 中文政策关键词
TITLE_POLICY_ZH = [
    "政策", "规划", "战略", "通知", "方案", "指南",
    "意见", "办法", "条例", "规定", "细则", "公告",
    "纲要", "行动", "部署", "改革", "调控", "监管",
    "法规", "标准", "规范", "批复", "调整",
    "管理", "准入", "许可", "备案", "报告",
    "十四五", "十五五", "专项", "体系",
]

# 中文矿业/资源关键词
TITLE_MINING_ZH = [
    "稀土", "矿产资源", "矿产", "资源", "能源",
    "采掘", "选矿", "冶炼", "加工",
    "战略储备", "出口", "关税", "配额",
    "供应链", "产业链", "价值链",
    "储备", "收储", "调控", "价格",
    "矿业", "矿山", "矿产品",
]

# 英文关键词
TITLE_KEYWORDS_EN = [
    "strategy", "policy", "framework", "plan", "regulation",
    "guideline", "roadmap", "initiative", "program",
    "reform", "standard", "code", "directive", "rule",
    "legislation", "bill", "act", "order", "executive",
    "critical mineral", "rare earth", "critical",
    "lithium", "copper", "nickel", "cobalt", "graphite",
    "uranium", "zinc", "lead",
    "supply chain", "supply", "trade", "export", "import",
    "sanction", "tariff", "quota", "stockpile",
    "mining", "mine", "mineral", "resource", "reserve",
    "metal", "processing", "refining", "smelting",
    "exploration", "development", "production",
    "investment", "revenue", "royalty", "tax", "incentive",
    "security", "geopolitic", "national security",
    "energy", "transition", "decarbon",
]


def is_policy_relevant(title: str) -> bool:
    """标题级过滤：标题是否含政策/矿业关键词。"""
    t = title.lower()
    for kw in TITLE_KEYWORDS_EN:
        if kw in t:
            return True
    for kw in TITLE_POLICY_ZH:
        if kw in t:
            return True
    for kw in TITLE_MINING_ZH:
        if kw in t:
            return True
    return False


# ══════════════════════════════════════════════════════════════
# 正文级打分过滤
# ══════════════════════════════════════════════════════════════

WEIGHTED_KEYWORDS: dict[str, int] = {
    "稀土": 3, "战略储备": 3, "出口管制": 3, "关键矿产": 3, "供应链": 3,
    "critical mineral": 3, "rare earth": 3, "national security": 3,
    "export control": 3, "supply chain": 3, "strategic reserve": 3,
    "矿产资源": 2, "产业链": 2, "国家安全": 2, "矿业政策": 2,
    "资源规划": 2, "战略资源": 2, "储备基地": 2,
    "收储": 2, "价格调控": 2, "能源资源": 2,
    "采矿业": 2, "选矿": 2, "冶炼": 2, "加工": 2,
    "关税": 2, "配额": 2, "许可证": 2, "监管": 2,
    "stockpile": 2, "tariff": 2, "quota": 2, "sanction": 2,
    "mining policy": 2, "resource strategy": 2,
    "processing": 2, "refining": 2, "smelting": 2,
    "矿业": 1, "资源": 1, "矿产": 1, "政策": 1,
    "strategy": 1, "policy": 1, "regulation": 1,
    "mining": 1, "resource": 1, "investment": 1,
}


def score_content(content: str, min_score: int = 2) -> bool:
    """正文加权打分，≥ min_score 通过。"""
    if not content:
        return False
    text = content.lower()
    score = 0
    for kw, weight in WEIGHTED_KEYWORDS.items():
        if kw in text:
            score += weight
            if score >= min_score:
                return True
    return False
