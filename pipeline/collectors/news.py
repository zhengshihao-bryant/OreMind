"""新闻采集器 — 通过 RSS Feed 获取矿业新闻。

直接从 RSS 的 <content:encoded> 获取全文，不请求详情页。

用法:
  python -m pipeline.collectors.news                        # 独立测试
  python -m pipeline.collectors.run --pipeline news          # 走管道
"""

from __future__ import annotations

import logging
import re
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

from pipeline.cleaners import extract_content
from pipeline.collectors.base import BaseCollector, CollectResult
from pipeline.config.feeds import (
    ACTIVE_FEEDS,
    CONTENT_MIN_LENGTH,
    DAYS_FILTER,
    MAX_ARTICLES,
    STANDBY_FEEDS,
)

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

logger = logging.getLogger(__name__)

# 从 URL 提取可读的源名称
_FEED_SOURCE_NAMES: dict[str, str] = {
    "mining.com": "mining_com",
    "mining-journal.com": "mining_journal",
    "northernminer.com": "northern_miner",
    "theassay.com": "the_assay",
    "investingnews.com": "investing_news",
    "kitco.com": "kitco_metals",
    "mining-technology.com": "mining_technology",
    "australianmining.com.au": "australian_mining",
    "miningglobal.com": "mining_global",
    "im-mining.com": "im_mining",
    "miningmexico.com": "mining_mexico",
    "resourceworld.com": "resource_world",
    "srsroccoreport.com": "srsrocco_report",
    "feedburner.com": "junior_mining_network",
    "miningir.com": "mining_ir",
    "smallcaps.com.au": "small_caps",
}


def _source_name(url: str) -> str:
    """从 feed URL 中提取短名称，如 'mining_com'、'northern_miner'。

    使用精确域名匹配，避免 'mining.com' 误匹配 'australianmining.com.au'。
    """
    from urllib.parse import urlparse
    hostname = urlparse(url).hostname or ""
    # 尝试精确匹配（hostname 或 二级域）
    for domain, name in _FEED_SOURCE_NAMES.items():
        if hostname == domain or hostname.endswith("." + domain):
            return name
    # 兜底：取 hostname 第一段
    parts = hostname.split(".")
    if len(parts) >= 2:
        return f"{parts[-2]}_{parts[-1]}"
    return hostname.replace(".", "_")


# ── 新闻采集器 ──────────────────────────────────────────────


class NewsCollector(BaseCollector):
    """通过 RSS Feed 采集矿业新闻（全文来自 RSS 自身）。

    :param feed_urls:     RSS 地址列表，默认取 ACTIVE_FEEDS + STANDBY_FEEDS
    :param max_articles:  单次采集最多处理文章数
    :param days_filter:   只保留近 N 天的文章（None 则不过滤）
    """

    source_name = "news"

    def __init__(
        self,
        feed_urls: list[str] | None = None,
        max_articles: int = MAX_ARTICLES,
        days_filter: int | None = DAYS_FILTER,
        timeout: int = 30,
    ) -> None:
        super().__init__(timeout=timeout)
        # 合并活跃源 + 备用源
        default_feeds = list(ACTIVE_FEEDS) + list(STANDBY_FEEDS.values())
        self.feed_urls = feed_urls or default_feeds
        self.max_articles = max_articles
        self.days_filter = days_filter

    # ── 主流程 ──────────────────────────────────────────────

    def collect(self, **kwargs: Any) -> CollectResult:
        """采集流程:

        1. 并发请求所有 RSS Feed
        2. 解析文章列表，直接从 RSS 获取全文
        3. 按发布时间过滤 → 去重 → 排序 → 截取上限
        4. 写入元数据
        """
        urls: list[str] = kwargs.get("feed_urls") or self.feed_urls
        max_articles = kwargs.get("max_articles", self.max_articles)
        days_filter: int | None = kwargs.get("days_filter", self.days_filter)

        if not urls:
            return CollectResult(
                source=self.source_name, succeeded=False, error="未配置 feed_urls",
            )

        cutoff: datetime | None = None
        if days_filter is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days_filter)
            logger.info("[过滤] 只保留 %d 天内的文章（≥ %s）", days_filter, cutoff.isoformat()[:10])

        # ── 并发请求所有 Feed ──
        all_articles: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        success_feeds = 0
        failed_feeds = 0
        feed_errors: list[dict[str, str]] = []

        with ThreadPoolExecutor(max_workers=4) as pool:
            fut_map = {pool.submit(self._parse_feed, url, cutoff): url for url in urls}

            for fut in as_completed(fut_map):
                url = fut_map[fut]
                try:
                    articles = fut.result()
                    for a in articles:
                        u = a.get("url", "")
                        if u and u not in seen_urls:
                            seen_urls.add(u)
                            all_articles.append(a)
                    success_feeds += 1
                    logger.info("[RSS] %s → %d 条", url, len(articles))
                except Exception as e:
                    failed_feeds += 1
                    feed_errors.append({"url": url, "error": str(e)})
                    logger.warning("[RSS] 跳过 %s: %s", url, e)

        if not all_articles:
            return CollectResult(
                source=self.source_name, succeeded=False,
                error="; ".join(e["error"] for e in feed_errors) if feed_errors else "所有 feed 均无内容",
                metadata={"success_feeds": 0, "failed_feeds": failed_feeds, "feed_errors": feed_errors},
            )

        # 排序 → 截取
        all_articles.sort(key=lambda x: x.get("published") or "", reverse=True)
        all_articles = all_articles[:max_articles]

        # ── 质量统计 + 元数据 ──
        crawl_time = datetime.now(timezone.utc).isoformat()
        content_ok = 0
        # ── 补爬：仅摘要的文章 → 抓详情页 ──
        fetched_count = 0
        for article in all_articles:
            content = article.get("content", "")
            content_length = len(content) if content else 0
            content_status = "full" if content and content_length >= CONTENT_MIN_LENGTH else "summary"
            if content_status == "summary":
                full = self._fetch_full_content(article.get("url", ""))
                if full:
                    article["content"] = full
                    article["content_length"] = len(full)
                    article["content_status"] = "full"
                    fetched_count += 1

        content_ok = sum(1 for a in all_articles if a.get("content_status") == "full")
        content_fail = sum(1 for a in all_articles if a.get("content_status") != "full")

        for article in all_articles:
            article["crawl_time"] = crawl_time
            if "content_length" not in article:
                article["content_length"] = len(article.get("content", ""))
            if "content_status" not in article:
                article["content_status"] = "full" if article.get("content_length", 0) >= CONTENT_MIN_LENGTH else "summary"

        logger.info(
            "[汇总] %d 条，覆盖 %d/%d 个源，含全文 %d / 仅摘要 %d（补爬 %d 篇）",
            len(all_articles), success_feeds, len(urls), content_ok, content_fail, fetched_count,
        )

        return CollectResult(
            source=self.source_name,
            items=all_articles,
            metadata={
                "success_feeds": success_feeds,
                "failed_feeds": failed_feeds,
                "feed_errors": feed_errors,
                "content_ok": content_ok,
                "content_fail": content_fail,
            },
        )

    # ── RSS 解析 ────────────────────────────────────────────

    def _fetch_rss(self, url: str) -> str:
        """自控制 RSS 请求 + 智能重试。"""
        retries = 3
        last_exc: Exception | None = None
        for i in range(retries):
            try:
                resp = self._get(url, headers=self._browser_headers())
                return resp.content.decode("utf-8", errors="ignore")
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status in (400, 401, 403, 404):
                    raise
                last_exc = e
                if i < retries - 1:
                    wait = 2 ** i
                    logger.warning("[RSS] 第 %d/%d 次请求失败 (HTTP %d)，%ds 后重试", i + 1, retries, status, wait)
                    time.sleep(wait)
            except Exception as e:
                last_exc = e
                if i < retries - 1:
                    wait = 2 ** i
                    logger.warning("[RSS] 第 %d/%d 次请求失败，%ds 后重试: %s", i + 1, retries, wait, e)
                    time.sleep(wait)
        raise last_exc or RuntimeError(f"RSS 请求失败: {url}")

    @staticmethod
    def _clean_xml(text: str) -> str:
        if not text:
            return ""
        return re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", text)

    def _parse_feed(
        self,
        feed_url: str,
        cutoff: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """解析一条 RSS Feed，直接从 <content:encoded> 取全文。"""
        content = self._fetch_rss(feed_url)
        content = self._clean_xml(content)
        parsed = feedparser.parse(content)

        if parsed.bozo and not parsed.entries:
            raise ValueError(str(parsed.bozo_exception or "无法解析 RSS"))

        articles: list[dict[str, Any]] = []
        for entry in parsed.entries:
            pub_date = self._parse_published(entry)
            if cutoff is not None and pub_date is not None and pub_date < cutoff:
                continue

            # 全文：优先取 <content:encoded>，兜底 summary
            raw_content = (
                entry.get("content")[0].get("value")
                if entry.get("content")
                else entry.get("summary", "")
            )
            full_text = self._clean_html(raw_content)

            article = {
                "title": self._sanitize_text(entry.get("title") or ""),
                "url": self._resolve_link(entry, feed_url),
                "published": pub_date.isoformat() if pub_date else None,
                "summary": self._sanitize_text(self._clean_html(
                    entry.get("summary") or entry.get("description") or ""
                ))[:500],
                "content": full_text,
                "author": self._get_author(entry),
                "source": _source_name(feed_url),
                "source_feed": feed_url,
            }
            articles.append(article)

        return articles

    # ── 工具方法 ────────────────────────────────────────────

    @staticmethod
    def _resolve_link(entry: Any, base_url: str = "") -> str:
        """解析文章链接，处理相对路径。"""
        link = ""
        if hasattr(entry, "link") and entry.link:
            link = entry.link
        elif hasattr(entry, "links") and entry.links:
            for ln in entry.links:
                if ln.get("rel") == "alternate":
                    link = ln["href"]
                    break
            if not link:
                link = entry.links[0].get("href", "")

        # 处理相对链接（如 /news/article-123）
        if link and link.startswith("/") and base_url:
            from urllib.parse import urlparse
            parsed = urlparse(base_url)
            link = f"{parsed.scheme}://{parsed.netloc}{link}"

        return link

    @staticmethod
    def _get_author(entry: Any) -> str | None:
        if hasattr(entry, "author") and entry.author:
            return entry.author
        if hasattr(entry, "authors") and entry.authors:
            return entry.authors[0].get("name")
        return None

    @staticmethod
    def _parse_published(entry: Any) -> datetime | None:
        """统一解析发布时间，返回 UTC datetime。"""
        pub_tuple = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
        if pub_tuple:
            try:
                from calendar import timegm
                return datetime.fromtimestamp(timegm(pub_tuple), tz=timezone.utc)
            except (OverflowError, OSError, TypeError):
                pass
        return None

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """清洗文本中的不可见/不可打印字符。"""
        if not text:
            return ""
        text = text.replace("\xa0", " ")
        text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", text)
        return text.strip()

    @staticmethod
    def _clean_html(html: str) -> str:
        """HTML → 纯文本，并移除 RSS 正文中的常见噪音。"""
        if not html:
            return ""
        soup = BeautifulSoup(html, "lxml")

        # 移除脚本和样式
        for tag in soup(["script", "style"]):
            tag.decompose()

        # 移除版权/来源/免责声明区块
        noise_classes = re.compile(r"copyright|source|attribution|disclaimer|footer|byline", re.I)
        for tag in soup.find_all(["div", "p", "span"], class_=noise_classes):
            tag.decompose()

        # 移除包含版权符号的段落
        for tag in soup.find_all(["p", "div"], string=re.compile(r"©\s*\d{4}", re.I)):
            tag.decompose()

        return soup.get_text(separator=" ", strip=True)

    def _fetch_full_content(self, url: str) -> str | None:
        """补爬详情页获取全文（RSS 只给摘要时使用）。"""
        if not url or not url.startswith("http"):
            return None
        try:
            html = self._safe_get(url, timeout=10)
            if not html:
                return None
            from pipeline.cleaners import extract_content
            text = extract_content(html)
            return text if len(text) >= 100 else None
        except Exception:
            return None

    @staticmethod
    def _browser_headers() -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }


# ── 独立测试 ────────────────────────────────────────────────


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    collector = NewsCollector(max_articles=10)
    with collector as c:
        result = c.collect()

        print(f"\n{'='*50}")
        print(f"  结果: {'[OK]' if result.succeeded else '[FAIL]'}")
        print(f"  文章: {len(result.items)}")
        if result.metadata:
            m = result.metadata
            print(f"  Feed: {m.get('success_feeds', '?')}/{m.get('failed_feeds', '?')} (成功/失败)")
            print(f"  正文: {m.get('content_ok', '?')}/{m.get('content_fail', '?')} (成功/失败)")

        if result.items:
            a = result.items[0]
            print(f"\n  标题: {a.get('title', '')[:60]}")
            print(f"  来源: {a.get('source', '?')}")
            print(f"  全文: {a.get('content_length', 0)} 字符")
            print(f"  预览: {(a.get('content') or '')[:150]}...")
        print(f"{'='*50}\n")
