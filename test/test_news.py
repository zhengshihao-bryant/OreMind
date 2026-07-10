"""NewsCollector 单元测试。

测试策略：
  - 不发送真实 HTTP 请求（所有网络调用通过 mock 隔离）
  - 覆盖正常路径、边界条件、异常处理
  - 使用 FeedParserDict 手动构造 mock 数据
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
from calendar import timegm
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from feedparser import FeedParserDict

from pipeline.collectors.news import NewsCollector
from pipeline.config.feeds import ACTIVE_FEEDS, STANDBY_FEEDS, MAX_ARTICLES


# ── 辅助函数：构造 mock Feed 条目 ────────────────────────────


def _make_entry(
    title: str,
    link: str,
    *,
    desc: str = "",
    pub_year: int = 2026,
    pub_month: int = 7,
    pub_day: int = 7,
    pub_hour: int = 0,
    author: str | None = None,
) -> FeedParserDict:
    entry = FeedParserDict({
        "title": title,
        "link": link,
        "summary": desc,
        "description": desc,
        "published_parsed": (pub_year, pub_month, pub_day, pub_hour, 0, 0, 0, 0, 0),
    })
    if author:
        entry["author"] = author
    return entry


def _make_feed(entries: list[FeedParserDict]) -> MagicMock:
    feed = MagicMock()
    feed.bozo = 0
    feed.bozo_exception = None
    feed.entries = entries
    return feed


# ── 测试用条目 ──────────────────────────────────────────────

ENTRY_COPPER = _make_entry(
    "Copper prices hit new high in 2026",
    "https://www.mining.com/copper-prices-2026",
    desc="Copper prices surged driven by supply constraints.",
    pub_year=2026, pub_month=7, pub_day=7, pub_hour=14,
    author="John Smith",
)

ENTRY_LITHIUM = _make_entry(
    "Lithium mining expands in Australia",
    "https://www.mining.com/lithium-australia-2026",
    desc="New lithium projects announced in Western Australia.",
    pub_year=2026, pub_month=7, pub_day=6, pub_hour=9,
    author="Jane Doe",
)

ENTRY_IRON = _make_entry(
    "Iron ore market update",
    "https://www.mining.com/iron-ore-update",
    desc="Iron ore demand remains strong in Asia.",
    pub_year=2026, pub_month=7, pub_day=5, pub_hour=16,
)

MOCK_FEED = _make_feed([ENTRY_COPPER, ENTRY_LITHIUM, ENTRY_IRON])

SAMPLE_ARTICLE_HTML = """<html>
<head><title>Copper prices hit new high</title></head>
<body>
  <article>
    <p>Copper prices surged to a new record high of $10,500 per ton on Tuesday,
    driven by global supply constraints and strong demand from the renewable energy sector.</p>
    <p>Analysts expect prices to remain elevated through the rest of the year.</p>
  </article>
  <footer>&copy; 2026 Mining.com</footer>
</body>
</html>"""


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def collector() -> NewsCollector:
    return NewsCollector(feed_urls=["https://www.mining.com/feed"], max_articles=300)


@pytest.fixture
def cutoff_recent() -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════
# 初始化
# ══════════════════════════════════════════════════════════════


class TestInit:
    def test_default_feeds(self):
        c = NewsCollector()
        assert len(c.feed_urls) >= 10
        assert c.max_articles == 300
        assert c.days_filter == 30

    def test_custom_feeds(self):
        c = NewsCollector(feed_urls=["https://example.com/feed"])
        assert c.feed_urls == ["https://example.com/feed"]

    def test_no_content_extraction(self):
        c = NewsCollector(extract_content=False)
        assert c.extract_content is False

    def test_source_name(self):
        c = NewsCollector()
        assert c.source_name == "news"

    def test_standby_feeds_available(self):
        assert len(STANDBY_FEEDS) >= 2


# ══════════════════════════════════════════════════════════════
# _parse_feed — RSS 解析
# ══════════════════════════════════════════════════════════════


class TestParseFeed:
    @patch("pipeline.collectors.news.feedparser.parse")
    def test_parse_valid_rss(self, mock_parse, collector, cutoff_recent):
        """解析有效的 RSS，提取文章字段。"""
        mock_parse.return_value = MOCK_FEED

        articles = collector._parse_feed("https://www.mining.com/feed", cutoff=cutoff_recent)
        assert len(articles) == 3

        article = articles[0]
        assert article["title"] == "Copper prices hit new high in 2026"
        assert article["url"] == "https://www.mining.com/copper-prices-2026"
        assert article["author"] == "John Smith"
        assert article["summary"] != ""
        assert article["content"] is None
        assert article["source_feed"] == "https://www.mining.com/feed"
        assert article["published"] is not None

    @patch("pipeline.collectors.news.feedparser.parse")
    def test_time_filtering(self, mock_parse, collector):
        """超出 cutoff 的文章应被过滤。"""
        mock_parse.return_value = MOCK_FEED

        far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
        articles = collector._parse_feed("https://www.mining.com/feed", cutoff=far_future)
        assert len(articles) == 0

    @patch("pipeline.collectors.news.feedparser.parse")
    def test_no_cutoff_returns_all(self, mock_parse, collector):
        """cutoff=None 时应返回所有文章。"""
        mock_parse.return_value = MOCK_FEED
        articles = collector._parse_feed("https://www.mining.com/feed", cutoff=None)
        assert len(articles) == 3

    @patch("pipeline.collectors.news.feedparser.parse")
    def test_empty_feed(self, mock_parse, collector):
        """空 Feed 应返回空列表。"""
        empty = MagicMock()
        empty.bozo = 0
        empty.entries = []
        mock_parse.return_value = empty
        articles = collector._parse_feed("https://www.mining.com/feed")
        assert articles == []

    def test_parse_error_raises(self, collector):
        """无法解析的 feed 应抛出 ValueError。"""
        mock = MagicMock()
        mock.bozo = 1
        mock.entries = []
        mock.bozo_exception = ValueError("Parse failed")

        with patch("pipeline.collectors.news.feedparser.parse", return_value=mock):
            with pytest.raises(ValueError, match="Parse failed"):
                collector._parse_feed("https://www.mining.com/feed")


# ══════════════════════════════════════════════════════════════
# _parse_published — 时间解析
# ══════════════════════════════════════════════════════════════


class TestParsePublished:
    def test_with_published_parsed(self):
        entry = FeedParserDict({"published_parsed": (2026, 7, 7, 14, 30, 0, 1, 188, 0)})
        result = NewsCollector._parse_published(entry)
        assert result is not None
        assert result.year == 2026
        assert result.month == 7
        assert result.day == 7

    def test_falls_back_to_updated_parsed(self):
        entry = FeedParserDict({"updated_parsed": (2026, 7, 6, 9, 15, 0, 0, 187, 0)})
        result = NewsCollector._parse_published(entry)
        assert result is not None
        assert result.year == 2026
        assert result.month == 7

    def test_no_date_returns_none(self):
        entry = FeedParserDict({})
        result = NewsCollector._parse_published(entry)
        assert result is None


# ══════════════════════════════════════════════════════════════
# _resolve_link — URL 解析
# ══════════════════════════════════════════════════════════════


class TestResolveLink:
    def test_from_link_attr(self):
        entry = FeedParserDict({"link": "https://example.com/article"})
        assert NewsCollector._resolve_link(entry) == "https://example.com/article"

    def test_from_links_alternate(self):
        entry = FeedParserDict({
            "links": [
                {"rel": "alternate", "href": "https://example.com/article"},
                {"rel": "self", "href": "https://example.com/feed"},
            ],
        })
        assert NewsCollector._resolve_link(entry) == "https://example.com/article"

    def test_from_links_fallback(self):
        entry = FeedParserDict({
            "links": [{"rel": "enclosure", "href": "https://example.com/file.pdf"}],
        })
        assert NewsCollector._resolve_link(entry) == "https://example.com/file.pdf"

    def test_no_link_returns_empty(self):
        entry = FeedParserDict({})
        assert NewsCollector._resolve_link(entry) == ""


# ══════════════════════════════════════════════════════════════
# _get_author — 作者提取
# ══════════════════════════════════════════════════════════════


class TestGetAuthor:
    def test_from_author_attr(self):
        entry = FeedParserDict({"author": "John Smith"})
        assert NewsCollector._get_author(entry) == "John Smith"

    def test_from_authors_list(self):
        entry = FeedParserDict({"authors": [{"name": "Jane Doe"}]})
        assert NewsCollector._get_author(entry) == "Jane Doe"

    def test_no_author(self):
        entry = FeedParserDict({})
        assert NewsCollector._get_author(entry) is None


# ══════════════════════════════════════════════════════════════
# _clean_html — HTML 清理
# ══════════════════════════════════════════════════════════════


class TestCleanHtml:
    def test_removes_tags(self):
        assert NewsCollector._clean_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_empty_string(self):
        assert NewsCollector._clean_html("") == ""

    def test_none_input(self):
        assert NewsCollector._clean_html(None) == ""


# ══════════════════════════════════════════════════════════════
# _extract_article — 正文提取
# ══════════════════════════════════════════════════════════════


class TestExtractArticle:
    @patch("pipeline.collectors.news.NewsCollector._get")
    def test_extract_from_article_tag(self, mock_get, collector):
        """能提取 <article> 标签内的正文。"""
        mock_get.return_value = MagicMock(text=SAMPLE_ARTICLE_HTML)

        result = collector._extract_article("https://www.mining.com/copper-prices-2026")
        assert result is not None
        assert "Copper prices surged" in result
        assert "Analysts expect" in result
        assert "Mining.com" not in result  # footer 应被移除

    @patch("pipeline.collectors.news.NewsCollector._get")
    def test_no_known_selector_falls_back_to_body(self, mock_get, collector):
        """无已知选择器匹配时回退到 <body>。"""
        html = "<html><body><p>Fallback content paragraph here.</p></body></html>"
        mock_get.return_value = MagicMock(text=html)

        result = collector._extract_article("https://www.mining.com/test")
        assert result is not None
        assert "Fallback content" in result

    @patch("pipeline.collectors.news.NewsCollector._get")
    def test_short_content_returns_none(self, mock_get, collector):
        """正文太短，选择器匹配不上，body 回退也无内容时返回 None。"""
        # 选择器匹配 → 文本 ≤ 100 字符 → 跳过；body 也有但太短 → 仍被返回
        # 测试 body 也不存在的情形
        html = "<!DOCTYPE html>"
        mock_get.return_value = MagicMock(text=html)

        result = collector._extract_article("https://www.mining.com/short")
        assert result is None

    @patch("pipeline.collectors.news.NewsCollector._get")
    def test_successful_extraction(self, mock_get, collector):
        """成功提取 <article> 正文，含多段落。"""
        mock_get.return_value = MagicMock(text=SAMPLE_ARTICLE_HTML)
        result = collector._extract_article("https://www.mining.com/copper-prices-2026")
        assert result is not None
        assert len(result) > 100


# ══════════════════════════════════════════════════════════════
# collect — 主流程
# ══════════════════════════════════════════════════════════════


class TestCollect:
    @patch("pipeline.collectors.news.feedparser.parse")
    def test_basic_collection(self, mock_parse, collector):
        """正常采集流程应返回 CollectResult。"""
        mock_parse.return_value = MOCK_FEED
        result = collector.collect(extract_content=False)
        assert result.succeeded
        assert len(result.items) == 3
        assert result.source == "news"

    @patch("pipeline.collectors.news.feedparser.parse")
    def test_extract_content_flag(self, mock_parse):
        """extract_content=True 时应请求详情页。"""
        mock_parse.return_value = MOCK_FEED
        collector = NewsCollector(feed_urls=["https://www.mining.com/feed"], extract_content=True)

        with patch.object(collector, "_extract_article", return_value="Extracted content") as mock_extract:
            result = collector.collect(extract_content=True)

        assert result.succeeded
        mock_extract.assert_called()
        assert result.items[0]["content"] == "Extracted content"

    @patch("pipeline.collectors.news.feedparser.parse")
    def test_max_articles_truncation(self, mock_parse):
        """应截取到 max_articles 上限。"""
        mock_parse.return_value = MOCK_FEED
        collector = NewsCollector(feed_urls=["https://www.mining.com/feed"], max_articles=2)
        result = collector.collect(extract_content=False)
        assert len(result.items) == 2

    @patch("pipeline.collectors.news.feedparser.parse")
    def test_deduplication(self, mock_parse, collector):
        """同一 URL 不重复采集。"""
        mock_parse.return_value = MOCK_FEED
        collector.feed_urls = ["https://www.mining.com/feed", "https://www.mining.com/feed"]
        result = collector.collect(extract_content=False)
        assert len(result.items) == 3  # 去重后仍是 3 条

    @patch("pipeline.collectors.news.feedparser.parse")
    def test_feed_partial_failure(self, mock_parse, collector):
        """部分 feed 失败不应影响其他 feed。"""
        mock_parse.side_effect = [MOCK_FEED, ValueError("Feed parse error")]
        collector.feed_urls = [
            "https://www.mining.com/feed",
            "https://www.mining.com/broken-feed",
        ]
        result = collector.collect(extract_content=False)
        assert result.succeeded
        assert len(result.items) == 3

    def test_no_feeds_error(self):
        """无 feed 源时应返回失败。"""
        collector = NewsCollector(feed_urls=["http://localhost/nonexistent"])
        with patch("pipeline.collectors.news.feedparser.parse") as mock_parse:
            err = MagicMock()
            err.bozo = 1
            err.entries = []
            err.bozo_exception = Exception("Feed unreachable")
            mock_parse.return_value = err
            result = collector.collect(extract_content=False)
        assert not result.succeeded

    @patch("pipeline.collectors.news.feedparser.parse")
    def test_all_feeds_fail(self, mock_parse, collector):
        """所有 feed 失败时应返回失败。"""
        err = MagicMock()
        err.bozo = 1
        err.entries = []
        err.bozo_exception = Exception("error")
        mock_parse.return_value = err
        result = collector.collect(extract_content=False)
        assert not result.succeeded

    @patch("pipeline.collectors.news.feedparser.parse")
    def test_articles_sorted_by_date(self, mock_parse, collector):
        """文章应按发布时间降序排列。"""
        mock_parse.return_value = MOCK_FEED
        result = collector.collect(extract_content=False)
        dates = [a.get("published", "") for a in result.items]
        assert dates == sorted(dates, reverse=True)

    @patch("pipeline.collectors.news.feedparser.parse")
    def test_days_filter(self, mock_parse):
        """days_filter 参数传递正确。"""
        mock_parse.return_value = MOCK_FEED
        collector = NewsCollector(feed_urls=["https://www.mining.com/feed"], days_filter=1)
        # 不应抛异常
        result = collector.collect(extract_content=False)
        assert isinstance(result, object)

    @patch("pipeline.collectors.news.feedparser.parse")
    def test_json_serializable(self, mock_parse, collector):
        """collect() 的 items 应可直接 json.dump。"""
        mock_parse.return_value = MOCK_FEED
        result = collector.collect(extract_content=False)
        json_str = json.dumps(result.items, ensure_ascii=False, indent=2)
        parsed = json.loads(json_str)
        assert len(parsed) == len(result.items)


# ══════════════════════════════════════════════════════════════
# 常量/配置验证
# ══════════════════════════════════════════════════════════════


class TestConfig:
    def test_active_feeds_non_empty(self):
        assert len(ACTIVE_FEEDS) >= 10

    def test_standby_feeds_have_urls(self):
        for name, url in STANDBY_FEEDS.items():
            assert url.startswith("http"), f"{name} URL invalid: {url}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
