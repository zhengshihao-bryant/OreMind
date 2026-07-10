"""政策采集器 — 从政府/企业官网抓取矿业相关政策、战略与法规。

采集管道:
  fetch_list()          ← 政策网站列表页
      │
      ▼
  extract_links()       ← 获取所有链接
      │
      ▼
  is_policy()           ← 标题关键词初筛（中/英）
      │
      ▼
  fetch_detail()        ← 抓详情页
      │
      ▼
  content_policy_filter()  ← 正文关键词打分复核（≥2分才算）
      │
      ▼
  return raw 数据

数据源:
  - regcc.cn             中国稀土集团官网（多栏目分页）
  - industry.gov.au      澳洲 DISR Critical Minerals Office
  - mnr.gov.cn           中国自然资源部
  - miit.gov.cn          中国工信部
  - mining_rss           Mining.com 政策/关键矿产 RSS 保底源
"""

from __future__ import annotations

import email.utils
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from pipeline.collectors.base import BaseCollector, CollectResult

logger = logging.getLogger(__name__)


# ── 源配置 ──────────────────────────────────────────────────

REGCC_BASE = "https://www.regcc.cn"
REGCC_CATEGORIES = [
    "jtnew",    # 集团新闻
    "gsgg",     # 公司公告
    "gzdt",     # 国资动态
    "cydt",     # 行业动态
    "qydt",     # 基层动态
    "llxxu",    # 理论学习
    "cxdt",     # 科创动态
    "xxgkzn",   # 信息公开指南
    "xxgkgd",   # 信息公开规定
    "zdgk",     # 主动公开内容
]

DISR_BASE = "https://www.industry.gov.au"
DISR_LIST_URLS = [
    "https://www.industry.gov.au/publications?publisher=9&items_per_page=50&order=field_date&sort=desc",
    "https://www.industry.gov.au/news?topic=critical-minerals&items_per_page=50",
]

# 中国政府矿业政策来源
CN_GOV_SOURCES = [
    {
        "name": "自然资源部",
        "url": "https://www.mnr.gov.cn/",
        "list_selector": "a",
    },
    {
        "name": "工信部原材料司",
        "url": "https://www.miit.gov.cn/",
        "list_selector": "a",
    },
]


# ── 标题级过滤关键词 ────────────────────────────────────────

# 中文政策/战略关键词
TITLE_POLICY_ZH = [
    "政策", "规划", "战略", "通知", "方案", "指南",
    "意见", "办法", "条例", "规定", "细则", "公告",
    "纲要", "行动", "部署", "改革", "调控", "监管",
    "法规", "标准", "规范", "批复", "调整",
    "管理", "准入", "许可", "备案", "报告",
    "十四五", "十五五", "专项", "体系",
]

# 中文矿业/资源关键词（标题含这些即视为政策相关）
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
    # 政策类
    "strategy", "policy", "framework", "plan", "regulation",
    "guideline", "roadmap", "initiative", "program",
    "reform", "standard", "code", "directive", "rule",
    "legislation", "bill", "act", "order", "executive",
    # 关键矿产
    "critical mineral", "rare earth", "critical",
    "lithium", "copper", "nickel", "cobalt", "graphite",
    "uranium", "zinc", "lead",
    # 供应链/贸易
    "supply chain", "supply", "trade", "export", "import",
    "sanction", "tariff", "quota", "stockpile",
    # 矿业/资源
    "mining", "mine", "mineral", "resource", "reserve",
    "metal", "processing", "refining", "smelting",
    "exploration", "development", "production",
    # 投资/经济
    "investment", "revenue", "royalty", "tax", "incentive",
    # 地缘/安全
    "security", "geopolitic", "national security",
    "energy", "transition", "decarbon",
]

# ── 正文级过滤（带权打分，≥ content_min_score 通过） ──────

# 核心关键词权重：越高说明与矿业政策相关度越大
WEIGHTED_CONTENT_KEYWORDS: dict[str, int] = {
    # 中文核心（权重 3）
    "稀土": 3, "战略储备": 3, "出口管制": 3, "关键矿产": 3, "供应链": 3,
    # 英文核心（权重 3）
    "critical mineral": 3, "rare earth": 3, "national security": 3,
    "export control": 3, "supply chain": 3, "strategic reserve": 3,
    # 中文重要（权重 2）
    "矿产资源": 2, "产业链": 2, "国家安全": 2, "矿业政策": 2,
    "资源规划": 2, "战略资源": 2, "储备基地": 2,
    "收储": 2, "价格调控": 2, "能源资源": 2,
    "采矿业": 2, "选矿": 2, "冶炼": 2, "加工": 2,
    "关税": 2, "配额": 2, "许可证": 2, "监管": 2,
    # 英文重要（权重 2）
    "stockpile": 2, "tariff": 2, "quota": 2, "sanction": 2,
    "mining policy": 2, "resource strategy": 2,
    "processing": 2, "refining": 2, "smelting": 2,
    # 通用（权重 1）
    "矿业": 1, "资源": 1, "矿产": 1, "政策": 1,
    "strategy": 1, "policy": 1, "regulation": 1,
    "mining": 1, "resource": 1, "investment": 1,
}


class PolicyCollector(BaseCollector):
    """政策采集器。

    :param sources:       数据源列表 ["regcc", "disr", "cn_gov"]
    :param max_items:     单次采集最多政策数（默认 300）
    :param days_filter:   只保留近 N 天的文章（默认 30）
    :param pages:         每个分类最多翻页数（默认 10）
    :param content_min_score: 正文过滤最低得分（默认 2）
    """

    source_name = "policy"

    def __init__(
        self,
        sources: list[str] | None = None,
        max_items: int = 300,
        days_filter: int | None = 30,
        pages: int = 10,
        content_min_score: int = 2,
    ) -> None:
        super().__init__()
        self.sources = sources or ["regcc", "disr", "mining_rss"]
        self.max_items = max_items
        self.days_filter = days_filter
        self.pages = pages
        self.content_min_score = content_min_score
        self._session = requests.Session()
        # 完整的浏览器伪装（对 DISR 等海外站点尤为重要）
        self._session.headers.update({
            "User-Agent": os.getenv(
                "OREMIND_USER_AGENT",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36",
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Connection": "keep-alive",
        })

    # ── 主流程 ──────────────────────────────────────────────

    def collect(self, **kwargs: Any) -> CollectResult:
        """执行政策采集管道。"""
        sources: list[str] = kwargs.get("sources", self.sources)
        max_items = kwargs.get("max_items", self.max_items)
        days_filter = kwargs.get("days_filter", self.days_filter)
        pages = kwargs.get("pages", self.pages)
        min_score = kwargs.get("content_min_score", self.content_min_score)

        cutoff: datetime | None = None
        if days_filter is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days_filter)

        all_policies: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        for source in sources:
            if len(all_policies) >= max_items:
                break

            if source == "regcc":
                items = self._collect_regcc(cutoff, pages)
            elif source == "disr":
                items = self._collect_disr(cutoff)
            elif source == "cn_gov":
                items = self._collect_cn_gov(cutoff)
            elif source == "mining_rss":
                items = self._collect_mining_policy_rss(cutoff)
            else:
                logger.warning("[跳过] 未知源: %s", source)
                continue

            for item in items:
                u = item.get("url", "")
                if u and u not in seen_urls:
                    seen_urls.add(u)
                    all_policies.append(item)

            logger.info("[%s] → %d 条政策", source, len(items))

        if not all_policies:
            return CollectResult(
                source=self.source_name, succeeded=False, error="未采集到政策内容",
            )

        all_policies.sort(key=lambda x: x.get("published") or "", reverse=True)
        all_policies = all_policies[:max_items]

        return CollectResult(source=self.source_name, items=all_policies)

    # ══════════════════════════════════════════════════════════
    # 中国稀土集团 (regcc.cn)
    # ══════════════════════════════════════════════════════════

    def _collect_regcc(
        self, cutoff: datetime | None, pages: int,
    ) -> list[dict[str, Any]]:
        """遍历 regcc.cn 各栏目分页。"""
        results: list[dict[str, Any]] = []

        for category in REGCC_CATEGORIES:
            if len(results) >= self.max_items:
                break
            items = self._scrape_regcc_category(category, cutoff, pages)
            results.extend(items)

        return results

    def _scrape_regcc_category(
        self, category: str, cutoff: datetime | None, pages: int,
    ) -> list[dict[str, Any]]:
        """抓取 regcc 单个栏目（支持分页）。

        使用 _parse_regcc_list 从列表页精确提取日期和链接，
        避免通用 extract_links 丢失结构化信息，并提前过滤
        不符合日期条件的文章以减少详情页请求。

        列表页统一结构:
          <ul class="aclist">
            <li>
              <a href="..." title="标题">
                <div class="times">
                  <span class="year">2026-07</span>
                  <span class="day">07</span>
                </div>
                <div class="txt"><h6>标题</h6><p>摘要</p></div>
              </a>
            </li>
          </ul>
        """
        policies: list[dict[str, Any]] = []
        empty_pages = 0

        for page in range(1, pages + 1):
            if len(policies) >= self.max_items:
                break

            list_url = self._regcc_list_url(category, page)
            items = self._parse_regcc_list(list_url, cutoff)
            if not items:
                empty_pages += 1
                if empty_pages >= 2:
                    break
                continue
            empty_pages = 0

            for item in items:
                if len(policies) >= self.max_items:
                    break

                url = item.get("url", "")
                # 跳过微信等站外链接
                if "regcc.cn" not in url.lower():
                    continue

                # 标题过滤
                if not self.is_policy(item):
                    continue

                # 列表页日期已精确到日，传入 known_date 避免详情页重复解析
                detail = self._fetch_regcc_detail(item, known_date=item.get("list_date"))
                if detail is None:
                    continue

                # 正文过滤（regcc 内容质量可靠，标题+日期即足）
                # 设置为 0 可跳过此步仅用标题过滤，设为 ≥1 则做关键词复核
                detail["source"] = "regcc"
                policies.append(detail)

            time.sleep(0.3)

        return policies

    def _parse_regcc_list(
        self, list_url: str, cutoff: datetime | None,
    ) -> list[dict[str, Any]]:
        """专门解析 regcc.cn 列表页，返回已通过 cutoff 日期过滤的文章列表。

        HTML 结构（所有栏目统一）:
          <ul class="aclist">
            <li>
              <a href="/path/..." title="文章标题">
                <div class="times">
                  <span class="year">2026-07</span>
                  <span class="day">07</span>
                </div>
                <div class="txt">
                  <h6>文章标题</h6>
                  <p>摘要</p>
                </div>
              </a>
            </li>
          </ul>

        :returns: [{title, url, list_date}] — 列表页日期已被精确解析到日
        """
        html = self._safe_get(list_url)
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")
        li_tags = soup.select("ul.aclist > li")
        if not li_tags:
            logger.warning("[regcc] 未在 %s 找到文章列表 (ul.aclist > li)", list_url)
            return []

        results: list[dict[str, Any]] = []
        for li in li_tags:
            # ── 提取日期（结构化：span.year + span.day） ──
            year_el = li.select_one("span.year")
            day_el = li.select_one("span.day")
            if not year_el or not day_el:
                continue

            try:
                year_month = year_el.get_text(strip=True)  # "2026-07"
                day_text = day_el.get_text(strip=True)      # "07"
                pub_date = datetime.strptime(
                    f"{year_month}-{day_text}", "%Y-%m-%d"
                ).replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue

            # 日期预筛：早于 cutoff 的直接跳过，不浪费详情页请求
            if cutoff is not None and pub_date < cutoff:
                continue

            # ── 提取链接和标题 ──
            a_tag = li.select_one("a[href]")
            if not a_tag:
                continue

            href = a_tag.get("href", "")
            if not href:
                continue

            article_url = urljoin(REGCC_BASE, href)

            # 优先取 <a title="...">，否则取 <h6> 文本
            title = (a_tag.get("title") or "").strip()
            if not title:
                h6 = a_tag.select_one("h6")
                title = h6.get_text(strip=True) if h6 else ""
            if not title:
                continue

            results.append({
                "title": title,
                "url": article_url,
                "list_date": pub_date,
            })

        return results

    def _fetch_regcc_detail(
        self, link: dict[str, Any], known_date: datetime | None = None,
    ) -> dict[str, Any] | None:
        """获取 regcc 详情页，优先使用已知日期。"""
        url = link["url"]
        if not url:
            return None
        # 拒绝站外链接（微信等）
        if "regcc.cn" not in url.lower() and not url.startswith("/"):
            return None

        try:
            html = self._safe_get(url)
        except requests.RequestException:
            return None

        content = self.parse_content(html)
        # 优先使用列表页已知日期，避免重新解析
        published = known_date or self._parse_page_date(html, url)

        return {
            "title": link["title"],
            "url": url,
            "content": content,
            "published": published.isoformat() if published else None,
            "category": "policy",
        }

    # ══════════════════════════════════════════════════════════
    # 澳洲 DISR
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def _regcc_list_url(category: str, page: int) -> str:
        if page == 1:
            return f"{REGCC_BASE}/zgxtjt/{category}/list.shtml"
        return f"{REGCC_BASE}/zgxtjt/{category}/list_{page}.shtml"

    @staticmethod
    def _regcc_date_from_url(url: str) -> datetime | None:
        """从 URL 路径中提取月份（精度到月），保留用作兜底/调试工具。

        匹配模式: /202607/ → 2026年7月
        """
        m = re.search(r"/(\d{4})(\d{2})/", url)
        if m:
            try:
                return datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=timezone.utc)
            except (ValueError, OverflowError):
                pass
        return None

    def _collect_disr(
        self, cutoff: datetime | None,
    ) -> list[dict[str, Any]]:
        """DISR 专用采集：CSS 定位列表项 + 列表页日期预筛。

        避免通用 extract_links 找不到链接的问题。
        """
        results: list[dict[str, Any]] = []

        for list_url in DISR_LIST_URLS:
            if len(results) >= self.max_items:
                break

            html = self._safe_get(list_url, timeout=10)
            if not html:
                logger.warning("[DISR] 无法访问 %s（超时或连接失败）", list_url)
                continue

            soup = BeautifulSoup(html, "lxml")

            # 多组 CSS 选择器兜底
            cards = (
                soup.select("div.view-content article")
                or soup.select("ul.cards li")
                or soup.select("div.listing article")
                or soup.select("[data-component='news-teaser']")
            )

            if not cards:
                # 兜底：如果 CSS 选择器都没命中，回退到 extract_links
                logger.warning("[DISR] CSS 选择器未命中 %s，回退到 extract_links", list_url)
                links = self.extract_links(html, base_url=DISR_BASE)
                disr_links = [
                    ln for ln in links
                    if "industry.gov.au" in ln.get("url", "")
                ]
                for link in disr_links:
                    if len(results) >= self.max_items:
                        break
                    if not self.is_policy(link):
                        continue
                    detail = self._fetch_disr_detail(link)
                    if detail is None:
                        continue
                    if cutoff is not None and not self._check_date(detail, cutoff):
                        continue
                    if not self.content_policy_filter(detail.get("content", "")):
                        continue
                    detail["source"] = "disr"
                    results.append(detail)
                    time.sleep(0.3)
                continue

            for card in cards:
                if len(results) >= self.max_items:
                    break

                link_tag = card.select_one("a")
                if not link_tag:
                    continue

                href = link_tag.get("href", "")
                if href and not href.startswith("http"):
                    href = urljoin(DISR_BASE, href)
                title = link_tag.get_text(strip=True)
                if not title or len(title) < 5:
                    continue

                link_item = {"title": title, "url": href}

                # 标题级过滤
                if not self.is_policy(link_item):
                    continue

                # 列表页日期预筛（避免不必要的详情页请求）
                date_tag = card.select_one("time, span.date, span.datetime")
                if date_tag:
                    pub_date = self._parse_card_date(date_tag)
                    if cutoff is not None and pub_date is not None and pub_date < cutoff:
                        continue

                detail = self._fetch_disr_detail(link_item)
                if detail is None:
                    continue

                # 详情页日期复核
                if cutoff is not None and not self._check_date(detail, cutoff):
                    continue

                if not self.content_policy_filter(detail.get("content", "")):
                    continue

                detail["source"] = "disr"
                results.append(detail)
                time.sleep(0.3)

        return results

    @staticmethod
    def _parse_card_date(date_tag: Any) -> datetime | None:
        """从列表页卡片提取日期。"""
        # <time datetime="...">
        dt_str = date_tag.get("datetime") or date_tag.get("content")
        if dt_str:
            try:
                return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        # 纯文本日期
        text = date_tag.get_text(strip=True)
        if text:
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
            try:
                return datetime.strptime(text, "%d %B %Y").replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass
        return None

    def _check_date(self, detail: dict[str, Any], cutoff: datetime) -> bool:
        """检查文章日期是否在 cutoff 之后。"""
        pub_str = detail.get("published")
        if not pub_str:
            logger.debug("[disr] 跳过（无发布日期）: %s", detail.get("url", ""))
            return False
        try:
            dt = datetime.fromisoformat(pub_str)
            return dt >= cutoff
        except (ValueError, TypeError):
            logger.debug("[disr] 跳过（日期格式无法解析）: %s", detail.get("url", ""))
            return False

    def _fetch_disr_detail(
        self, link: dict[str, Any],
    ) -> dict[str, Any] | None:
        url = link["url"]
        try:
            html = self._safe_get(url)
        except requests.RequestException:
            return None

        content = self.parse_content(html)
        published = self._parse_page_date(html, url)

        return {
            "title": link["title"],
            "url": url,
            "content": content,
            "published": published.isoformat() if published else None,
            "category": "policy",
        }

    # ══════════════════════════════════════════════════════════
    # 中国政府政策源（cn_gov）
    # ══════════════════════════════════════════════════════════

    def _collect_cn_gov(
        self, cutoff: datetime | None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        for source in CN_GOV_SOURCES:
            if len(results) >= self.max_items:
                break

            try:
                html = self._safe_get(source["url"])
            except requests.RequestException as e:
                logger.warning("[cn_gov] 无法访问 %s: %s", source["name"], e)
                continue

            if not html:
                logger.warning("[cn_gov] %s 无响应", source["name"])
                continue

            links = self.extract_links(html, base_url=source["url"])

            for link in links:
                if len(results) >= self.max_items:
                    break
                if not self.is_policy(link):
                    continue
                # 只保留站内链接
                url = link.get("url", "")
                if not url.startswith(("https://", "http://")):
                    continue

                detail = self._fetch_generic_detail(link)
                if detail is None:
                    continue

                # 时间过滤：无法确定日期则拒绝
                if cutoff is not None:
                    pub_str = detail.get("published")
                    if not pub_str:
                        logger.debug("[cn_gov] 跳过（无发布日期）: %s", link.get("url", ""))
                        continue
                    try:
                        dt = datetime.fromisoformat(pub_str)
                        if dt < cutoff:
                            continue
                    except (ValueError, TypeError):
                        logger.debug("[cn_gov] 跳过（日期格式无法解析）: %s", link.get("url", ""))
                        continue

                if not self.content_policy_filter(detail.get("content", "")):
                    continue

                detail["source"] = source["name"]
                results.append(detail)
                time.sleep(0.3)

        return results

    def _fetch_generic_detail(
        self, link: dict[str, Any],
    ) -> dict[str, Any] | None:
        url = link["url"]
        try:
            html = self._safe_get(url)
        except requests.RequestException:
            return None

        content = self.parse_content(html)
        published = self._parse_page_date(html, url)

        return {
            "title": link["title"],
            "url": url,
            "content": content,
            "published": published.isoformat() if published else None,
            "category": "policy",
        }

    # ══════════════════════════════════════════════════════════
    # Mining.com RSS 保底源
    # ══════════════════════════════════════════════════════════

    def _collect_mining_policy_rss(
        self, cutoff: datetime | None,
    ) -> list[dict[str, Any]]:
        """从多个矿业新闻 RSS 源采集政策相关数据，作为直采源的保底。

        RSS 源包含完整标题、链接、日期和摘要，无需二次抓取详情页，
        数据量大且更新快，适合快速堆积数量。

        支持 WordPress ?paged=N 翻页以获取 30 天窗口内的更多文章。
        """
        rss_urls = [
            # Mining.com 主站 + 细分频道
            "https://www.mining.com/feed/",
            "https://www.mining.com/feed/?paged=2",  # 翻页：更多 30 天内的文章
            "https://www.mining.com/feed/?paged=3",
            "https://www.mining.com/category/news/feed/",
            "https://www.mining.com/tag/critical-minerals/feed/",
            "https://www.mining.com/tag/rare-earth/feed/",
            "https://www.mining.com/tag/lithium/feed/",
            # 其他矿媒
            "https://www.mining-journal.com/feed/rss",
            "https://www.northernminer.com/feed/",
            "https://www.theassay.com/feed/",
            "https://investingnews.com/feed/",
            "https://im-mining.com/rss/",
            "https://www.australianmining.com.au/feed/",
            "https://www.miningreview.com.au/feed/",
            # 二级源
            "https://www.srsroccoreport.com/feed/",
            "https://feeds.feedburner.com/juniorminingnetwork",
            "https://feeds.feedburner.com/infomine",
            "https://resourceworld.com/feed/",
            "https://www.miningir.com/feed/",
            "https://smallcaps.com.au/feed/",
            # 政策/行业协会源
            "https://euromines.org/feed",
        ]
        results: list[dict[str, Any]] = []

        for rss_url in rss_urls:
            if len(results) >= self.max_items:
                break

            html = self._safe_get(rss_url)
            if not html:
                logger.warning("[mining_rss] 无法访问 %s", rss_url)
                continue

            try:
                soup = BeautifulSoup(html, "lxml-xml")
            except Exception:
                logger.warning("[mining_rss] XML 解析失败: %s", rss_url)
                continue

            items = soup.find_all("item")
            if not items:
                continue

            for item in items:
                    if len(results) >= self.max_items:
                        break

                    title_tag = item.find("title")
                    link_tag = item.find("link")
                    if not title_tag or not link_tag:
                        continue

                    title = title_tag.get_text(strip=True)
                    url = link_tag.get_text(strip=True)
                    if not title or not url:
                        continue

                    # 标题级过滤
                    if not self.is_policy({"title": title}):
                        continue

                    # 解析日期（RFC 2822）
                    pub_date = None
                    pub_date_tag = item.find("pubDate")
                    if pub_date_tag:
                        try:
                            pub_date = email.utils.parsedate_to_datetime(
                                pub_date_tag.get_text(strip=True)
                            )
                            if pub_date and pub_date.tzinfo is None:
                                pub_date = pub_date.replace(tzinfo=timezone.utc)
                        except Exception:
                            pass

                    # 日期过滤
                    if cutoff is not None and pub_date is not None and pub_date < cutoff:
                        continue

                    # 摘要有则取，无则留空
                    desc_tag = item.find("description")
                    content = desc_tag.get_text(strip=True) if desc_tag else ""

                    # NOTE: RSS 摘要短，不进行正文过滤（标题 is_policy 已筛）

                    # 提取源域名作为 source 标识
                    from urllib.parse import urlparse
                    source_name = urlparse(url).netloc.replace("www.", "")

                    results.append({
                        "title": title,
                        "url": url,
                        "content": content,
                        "published": pub_date.isoformat() if pub_date else None,
                        "source": source_name,
                        "category": "policy",
                    })

            feed_name = rss_url.rstrip("/").split("/")[-1].replace("feed", "")
            logger.info(
                "[mining_rss] %s → %d 条",
                feed_name or rss_url.split("/")[-2],
                len(results),
            )

        return results

    # ══════════════════════════════════════════════════════════
    # 通用工具
    # ══════════════════════════════════════════════════════════

    def _safe_get(self, url: str, timeout: int = 20) -> str | None:
        """带指数退避重试的 GET 请求。

        :param timeout: 单次请求超时秒数（默认 20s）
        :param url:     目标 URL
        """
        for attempt in range(3):
            try:
                resp = self._session.get(url, timeout=timeout)
                if resp.status_code != 200:
                    logger.warning("[HTTP] %s 返回 %s", url, resp.status_code)
                resp.raise_for_status()
                return resp.text
            except requests.RequestException:
                if attempt == 2:
                    return None
                # 指数退避：3s, 6s
                wait_time = 3 * (attempt + 1)
                logger.warning("[重试] %s 失败，%ds 后重试 (%d/3)", url, wait_time, attempt + 1)
                time.sleep(wait_time)
        return None

    @staticmethod
    def extract_links(html: str, base_url: str = "") -> list[dict[str, Any]]:
        """从 HTML 提取所有链接。"""
        soup = BeautifulSoup(html, "lxml")
        links: list[dict[str, Any]] = []
        seen: set[str] = set()

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            title = a.get_text(strip=True)

            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue
            if not title or len(title) < 4:
                continue

            if base_url and not href.startswith(("http://", "https://")):
                href = urljoin(base_url, href)

            if href not in seen:
                seen.add(href)
                links.append({"title": title, "url": href})

        return links

    def is_policy(self, item: dict[str, Any]) -> bool:
        """标题级过滤：英文优先（DISR 等英文章快速命中）。"""
        title = item.get("title", "").lower()

        # 英文关键词（英文站点匹配更快）
        for kw in TITLE_KEYWORDS_EN:
            if kw in title:
                return True

        # 中文政策关键词
        for kw in TITLE_POLICY_ZH:
            if kw in title:
                return True

        # 中文矿业/资源关键词
        for kw in TITLE_MINING_ZH:
            if kw in title:
                return True

        return False

    def content_policy_filter(self, content: str) -> bool:
        """正文过滤：加权关键词打分，≥ content_min_score 通过。"""
        if not content:
            return False

        content_lower = content.lower()
        score = 0

        for kw, weight in WEIGHTED_CONTENT_KEYWORDS.items():
            if kw in content_lower:
                score += weight
                if score >= self.content_min_score:
                    return True

        return False

    @staticmethod
    def parse_content(html: str | None) -> str:
        """从详情页提取正文。"""
        if not html:
            return ""
        soup = BeautifulSoup(html, "lxml")

        for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        paragraphs: list[str] = []
        for p in soup.find_all("p"):
            text = p.get_text(strip=True)
            if len(text) > 10:
                paragraphs.append(text)

        return "\n".join(paragraphs)

    @staticmethod
    def _parse_page_date(html: str, fallback_url: str) -> datetime | None:
        """从 HTML 或 URL 提取发布时间。"""
        soup = BeautifulSoup(html, "lxml")

        # <time> 标签
        time_tag = soup.find("time")
        if time_tag and time_tag.get("datetime"):
            try:
                return datetime.fromisoformat(time_tag["datetime"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        # Open Graph 协议日期（常用作文章发布时间）
        og_tag = soup.find("meta", property="article:published_time")
        if og_tag and og_tag.get("content"):
            try:
                return datetime.fromisoformat(og_tag["content"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        # meta
        for meta in soup.find_all("meta"):
            name = (meta.get("name") or "").lower()
            if name in ("dcterms.date", "dc.date", "date", "pubdate"):
                content = meta.get("content", "")
                try:
                    return datetime.fromisoformat(content.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass

        # 中文日期：2026年07月09日 / 2026-07-09 / 2026/07/09
        text = soup.get_text()
        for pattern in [
            r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
            r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})",
        ]:
            m = re.search(pattern, text)
            if m:
                try:
                    return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
                except (ValueError, OverflowError):
                    pass
                break  # 匹配到但解析失败，不再试同级别模式

        # 中文日期（只精确到月，如 "2025年04月"）
        m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", text)
        if m:
            try:
                dt = datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=timezone.utc)
                return dt
            except (ValueError, OverflowError):
                pass

        # 英文日期
        m = re.search(r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})", text)
        if m:
            try:
                return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %B %Y").replace(tzinfo=timezone.utc)
            except (ValueError, OverflowError):
                pass

        # URL 日期
        m = re.search(r"/(\d{4})(\d{2})/", fallback_url)
        if m:
            try:
                return datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=timezone.utc)
            except (ValueError, OverflowError):
                pass

        return None

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> PolicyCollector:
        return self

    def __exit__(self, _exc_type: Any, _exc_val: Any, _exc_tb: Any) -> None:
        self.close()
