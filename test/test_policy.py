"""PolicyCollector 单元测试。

测试策略：
  - 不发送真实 HTTP 请求（所有网络调用通过 mock 隔离）
  - 覆盖标题过滤、正文打分、日期解析、链接提取等全部逻辑
  - 使用真实 HTML 片段验证解析器
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pipeline.collectors.policy import (
    PolicyCollector,
    REGCC_BASE,
    REGCC_CATEGORIES,
    DISR_BASE,
    DISR_LIST_URLS,
    CN_GOV_SOURCES,
    TITLE_POLICY_ZH,
    TITLE_MINING_ZH,
    TITLE_KEYWORDS_EN,
    WEIGHTED_CONTENT_KEYWORDS,
)


# ── 测试用 HTML 片段 ─────────────────────────────────────────

# regcc.cn 列表页实际 HTML 结构（基于真实页面提取）
REGCC_LIST_HTML = """<html><body>
<ul class="aclist">
  <li>
    <a href="/zgxtjt/jtnew/202607/07/abc123.shtml" title="稀土产业政策通知文件">
      <div class="times"><span class="year">2026-07</span><span class="day">07</span></div>
      <div class="txt"><h6>稀土产业政策通知文件</h6><p>摘要内容</p></div>
    </a>
  </li>
  <li>
    <a href="/zgxtjt/gsgg/202606/15/def456.shtml" title="公司战略规划部署公告">
      <div class="times"><span class="year">2026-06</span><span class="day">15</span></div>
      <div class="txt"><h6>公司战略规划部署公告</h6><p>摘要内容</p></div>
    </a>
  </li>
  <li>
    <a href="https://www.wechat.com/some-post" title="微信平台外部文章链接">
      <div class="times"><span class="year">2026-07</span><span class="day">01</span></div>
      <div class="txt"><h6>微信平台外部文章链接</h6><p>摘要</p></div>
    </a>
  </li>
  <li>
    <a href="/zgxtjt/qydt/202505/01/old789.shtml" title="旧文章（超 cutoff）">
      <div class="times"><span class="year">2025-05</span><span class="day">01</span></div>
      <div class="txt"><h6>旧文章内容</h6><p>摘要</p></div>
    </a>
  </li>
</ul>
</body></html>"""

LIST_HTML = """<html><body>
  <a href="/zgxtjt/jtnew/202607/01/list.shtml">稀土产业政策通知文件</a>
  <a href="/zgxtjt/gsgg/202606/15/list.shtml">公司战略规划部署公告</a>
  <a href="https://www.wechat.com/some-post">微信平台外部文章链接</a>
  <a href="/zgxtjt/qydt/202506/01/list.shtml">旧文章内容页面链接</a>
</body></html>"""

DETAIL_HTML = """<html>
<head><meta name="dcterms.date" content="2026-07-08T10:00:00Z"/></head>
<body>
  <h1>稀土产业政策调整通知文件</h1>
  <p>为保障国家稀土供应链安全，加强战略资源储备管理，
  现就稀土产业政策调整有关事项通知如下。</p>
  <p>一、加强稀土资源规划管理，建立战略储备基地。</p>
  <p>二、完善出口管制措施，优化配额分配机制。</p>
</body>
</html>"""

DETAIL_NO_DATE = """<html><body>
  <p>稀土产业扶持政策出台通知，加强供应链安全管理工作。</p>
</body></html>"""

DETAIL_CN_DATE = """<html><body>
  <p>发布时间：2026年7月8日</p>
  <p>供应链安全与矿产资源规划相关通知文件。</p>
</body></html>"""

DETAIL_EN_DATE = """<html><body>
  <p>Published: 8 July 2026</p>
  <p>Critical minerals strategy and supply chain resilience framework.</p>
</body></html>"""

DISR_LIST = """<html><body>
  <a href="https://www.industry.gov.au/publications/critical-minerals-strategy-2026">Critical Minerals Strategy 2026</a>
  <a href="https://www.industry.gov.au/news/rare-earth-investment">Rare Earth Investment Program</a>
  <a href="https://www.industry.gov.au/news/other-topic">General industry news article</a>
</body></html>"""

CN_GOV_LIST = """<html><body>
  <a href="https://www.mnr.gov.cn/policy/2026/notice.html">矿产资源规划调整通知文件</a>
  <a href="https://www.mnr.gov.cn/news/2026/seminar.html">矿产资源学术研讨会议通知</a>
</body></html>"""


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def collector() -> PolicyCollector:
    return PolicyCollector(sources=["regcc", "disr", "cn_gov"], max_items=300)


@pytest.fixture
def cutoff() -> datetime:
    return datetime(2026, 6, 1, tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════
# 初始化
# ══════════════════════════════════════════════════════════════


class TestInit:
    def test_default_sources(self):
        c = PolicyCollector()
        assert c.sources == ["regcc", "disr", "mining_rss"]
        assert c.max_items == 300
        assert c.days_filter == 30

    def test_custom_sources(self):
        c = PolicyCollector(sources=["regcc"])
        assert c.sources == ["regcc"]

    def test_custom_max_items(self):
        c = PolicyCollector(max_items=100)
        assert c.max_items == 100

    def test_no_time_filter(self):
        c = PolicyCollector(days_filter=None)
        assert c.days_filter is None

    def test_source_name(self):
        c = PolicyCollector()
        assert c.source_name == "policy"

    def test_session_headers(self):
        c = PolicyCollector()
        ua = c._session.headers.get("User-Agent", "")
        assert "Mozilla" in ua

    def test_all_categories_covered(self):
        assert len(REGCC_CATEGORIES) >= 5  # 至少 5 个核心栏目


# ══════════════════════════════════════════════════════════════
# is_policy — 标题级过滤
# ══════════════════════════════════════════════════════════════


class TestIsPolicy:
    def setup_method(self):
        self.collector = PolicyCollector()

    @pytest.mark.parametrize("title", [
        "稀土产业政策通知",
        "矿产资源规划纲要部署",
        "战略储备管理办法规定",
        "出口管制条例修订通知",
        "供应链安全管理办法通知",
        "十五五矿产资源总体规划",
        "Critical minerals strategy 2026",
        "Rare earth supply chain framework",
        "Mining policy reform announced",
    ])
    def test_should_match(self, title):
        assert self.collector.is_policy({"title": title}), f"should match: {title}"

    @pytest.mark.parametrize("title", [
        "今日天气预报情况分析",
        "体育新闻赛事最新报道",
        "Company picnic announcement",
        "",
        "今日菜谱美食推荐分享",
    ])
    def test_should_not_match(self, title):
        assert not self.collector.is_policy({"title": title}), f"should not match: {title}"

    def test_case_insensitive(self):
        assert self.collector.is_policy({"title": "CRITICAL MINERAL STRATEGY"})

    def test_title_substring_match(self):
        assert self.collector.is_policy({"title": "关于矿产资源开发的通知文件"})

    def test_no_title_returns_false(self):
        assert not self.collector.is_policy({"url": "https://example.com"})


# ══════════════════════════════════════════════════════════════
# content_policy_filter — 正文关键词打分
# ══════════════════════════════════════════════════════════════


class TestContentPolicyFilter:
    def setup_method(self):
        self.collector = PolicyCollector(content_min_score=2)

    def test_two_keywords_pass(self):
        assert self.collector.content_policy_filter("稀土 供应链 重点通知") is True

    def test_one_keyword_fails(self):
        # "矿业" 权重 1，一个关键词不足 min_score=2
        assert self.collector.content_policy_filter("矿业贸易常规动态") is False

    def test_empty_content(self):
        assert self.collector.content_policy_filter("") is False
        assert self.collector.content_policy_filter(None) is False

    def test_no_match(self):
        assert self.collector.content_policy_filter("今天天气不错适合出门散步") is False

    def test_case_insensitive(self):
        assert self.collector.content_policy_filter("RARE EARTH and SUPPLY CHAIN")

    def test_custom_min_score(self):
        c = PolicyCollector(content_min_score=1)
        assert c.content_policy_filter("稀土") is True
        assert c.content_policy_filter("常规内容测试") is False

    @pytest.mark.parametrize("content", [
        "稀土供应链国家安全",
        "出口管制与战略储备安排",
        "critical mineral rare earth",
        "mining policy and export control",
        "出口管制 供应链 价格调控 监管",
    ])
    def test_various_matches(self, content):
        c = PolicyCollector(content_min_score=1)
        assert c.content_policy_filter(content)


# ══════════════════════════════════════════════════════════════
# extract_links — HTML 链接提取
# ══════════════════════════════════════════════════════════════


class TestExtractLinks:
    def test_basic_extraction(self):
        html = '<html><body><a href="https://example.com/page1">链接标题文本内容</a><a href="/relative/path">相对路径链接文本</a></body></html>'
        links = PolicyCollector.extract_links(html, base_url="https://example.com")
        assert len(links) == 2

    def test_relative_url_resolved(self):
        html = '<html><body><a href="/page/info">页面详情文本内容</a></body></html>'
        links = PolicyCollector.extract_links(html, base_url="https://example.com")
        assert links[0]["url"] == "https://example.com/page/info"

    def test_skip_short_title(self):
        html = '<html><body><a href="https://example.com/page">ab</a></body></html>'
        links = PolicyCollector.extract_links(html)
        assert len(links) == 0

    def test_skip_javascript(self):
        html = '<html><body><a href="javascript:void(0)">点击链接功能</a></body></html>'
        links = PolicyCollector.extract_links(html)
        assert len(links) == 0

    def test_skip_mailto(self):
        html = '<html><body><a href="mailto:test@example.com">联系邮箱地址</a></body></html>'
        links = PolicyCollector.extract_links(html)
        assert len(links) == 0

    def test_skip_anchor(self):
        html = '<html><body><a href="#section">页面内部跳转</a></body></html>'
        links = PolicyCollector.extract_links(html)
        assert len(links) == 0

    def test_deduplication(self):
        html = '<html><body><a href="https://example.com/page">重复链接内容文本</a><a href="https://example.com/page">重复链接内容文本</a></body></html>'
        links = PolicyCollector.extract_links(html)
        assert len(links) == 1

    def test_empty_html(self):
        assert PolicyCollector.extract_links("") == []


# ══════════════════════════════════════════════════════════════
# _regcc_list_url — URL 生成
# ══════════════════════════════════════════════════════════════


class TestRegccListUrl:
    def test_page_one(self):
        url = PolicyCollector._regcc_list_url("jtnew", 1)
        assert url == f"{REGCC_BASE}/zgxtjt/jtnew/list.shtml"

    def test_page_two(self):
        url = PolicyCollector._regcc_list_url("jtnew", 2)
        assert url == f"{REGCC_BASE}/zgxtjt/jtnew/list_2.shtml"

    def test_all_categories(self):
        for cat in REGCC_CATEGORIES:
            url = PolicyCollector._regcc_list_url(cat, 1)
            assert cat in url


# ══════════════════════════════════════════════════════════════
# _regcc_date_from_url — URL 日期提取
# ══════════════════════════════════════════════════════════════


class TestRegccDateFromUrl:
    def test_valid_date(self):
        url = "https://www.regcc.cn/zgxtjt/jtnew/202607/01/list.shtml"
        dt = PolicyCollector._regcc_date_from_url(url)
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 7

    def test_no_date(self):
        url = "https://www.regcc.cn/zgxtjt/jtnew/list.shtml"
        assert PolicyCollector._regcc_date_from_url(url) is None

    def test_invalid_month(self):
        url = "https://www.regcc.cn/zgxtjt/jtnew/202613/01/list.shtml"
        assert PolicyCollector._regcc_date_from_url(url) is None


# ══════════════════════════════════════════════════════════════
# _parse_regcc_list — regcc 列表页专用解析
# ══════════════════════════════════════════════════════════════


class TestParseRegccList:
    def test_parse_all_items(self, collector):
        """能正确解析所有列表项的结构化数据。"""
        with patch.object(collector, "_safe_get", return_value=REGCC_LIST_HTML):
            items = collector._parse_regcc_list(
                "https://www.regcc.cn/zgxtjt/jtnew/list.shtml", cutoff=None,
            )
        # 不传 cutoff 时，4 项全部返回（不过滤）
        assert len(items) == 4

    def test_parse_with_cutoff(self, collector, cutoff):
        """旧文章（2025-05-01）被 cutoff 过滤掉。"""
        with patch.object(collector, "_safe_get", return_value=REGCC_LIST_HTML):
            items = collector._parse_regcc_list(
                "https://www.regcc.cn/zgxtjt/jtnew/list.shtml", cutoff=cutoff,
            )
        assert len(items) == 3  # 第 4 项 2025-05-01 < 2026-06-01，被过滤
        urls = [i["url"] for i in items]
        assert all("old789" not in u for u in urls)

    def test_parse_extracts_title_and_url(self, collector):
        """正确提取标题和 URL。"""
        with patch.object(collector, "_safe_get", return_value=REGCC_LIST_HTML):
            items = collector._parse_regcc_list(
                "https://www.regcc.cn/zgxtjt/jtnew/list.shtml", cutoff=None,
            )
        assert len(items) == 4
        # 第一条
        assert items[0]["title"] == "稀土产业政策通知文件"
        assert "regcc.cn" in items[0]["url"]
        assert "/jtnew/" in items[0]["url"]
        # 日期精度到日
        assert items[0]["list_date"].year == 2026
        assert items[0]["list_date"].month == 7
        assert items[0]["list_date"].day == 7

    def test_parse_external_url(self, collector):
        """微信等站外链接也会返回，由调用方过滤。"""
        with patch.object(collector, "_safe_get", return_value=REGCC_LIST_HTML):
            items = collector._parse_regcc_list(
                "https://www.regcc.cn/zgxtjt/jtnew/list.shtml", cutoff=None,
            )
        urls = [i["url"] for i in items]
        assert any("wechat.com" in u for u in urls)

    def test_empty_page_returns_empty(self, collector):
        """无文章的空列表页返回 [][。"""
        html_empty = """<html><body><ul class="aclist"></ul></body></html>"""
        with patch.object(collector, "_safe_get", return_value=html_empty):
            items = collector._parse_regcc_list(
                "https://www.regcc.cn/zgxtjt/jtnew/list.shtml", cutoff=None,
            )
        assert items == []

    def test_no_aclist_returns_empty(self, collector):
        """页面没有 aclist 结构时返回 []。"""
        with patch.object(collector, "_safe_get", return_value="<html><body><p>no list</p></body></html>"):
            items = collector._parse_regcc_list(
                "https://www.regcc.cn/zgxtjt/jtnew/list.shtml", cutoff=None,
            )
        assert items == []

    def test_http_failure_returns_empty(self, collector):
        """HTTP 请求失败返回 []。"""
        with patch.object(collector, "_safe_get", return_value=None):
            items = collector._parse_regcc_list(
                "https://www.regcc.cn/zgxtjt/jtnew/list.shtml", cutoff=None,
            )
        assert items == []


# ══════════════════════════════════════════════════════════════
# _parse_page_date — HTML 日期提取
# ══════════════════════════════════════════════════════════════


class TestParsePageDate:
    def test_meta_date(self):
        dt = PolicyCollector._parse_page_date(DETAIL_HTML, "")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 7
        assert dt.day == 8

    def test_chinese_date(self):
        dt = PolicyCollector._parse_page_date(DETAIL_CN_DATE, "")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 7
        assert dt.day == 8

    def test_english_date(self):
        dt = PolicyCollector._parse_page_date(DETAIL_EN_DATE, "")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 7
        assert dt.day == 8

    def test_no_date(self):
        assert PolicyCollector._parse_page_date(DETAIL_NO_DATE, "") is None

    def test_url_fallback(self):
        url = "https://www.regcc.cn/zgxtjt/jtnew/202607/01/list.shtml"
        dt = PolicyCollector._parse_page_date("<html></html>", url)
        assert dt is not None
        assert dt.month == 7

    def test_time_tag(self):
        html = '<html><body><time datetime="2026-07-08T10:00:00Z">July 8</time></body></html>'
        dt = PolicyCollector._parse_page_date(html, "")
        assert dt is not None
        assert dt.year == 2026


# ══════════════════════════════════════════════════════════════
# parse_content — 正文提取
# ══════════════════════════════════════════════════════════════


class TestParseContent:
    def test_extract_paragraphs(self):
        html = "<html><body><p>段落一内容正文内容展示</p><p>段落二内容信息较长展示</p></body></html>"
        content = PolicyCollector.parse_content(html)
        assert "段落一内容正文内容展示" in content
        assert "段落二内容信息较长展示" in content

    def test_remove_unwanted_tags(self):
        html = """<html>
          <body>
            <p>正文内容段落展示信息内容呈现</p>
            <script>alert('remove me')</script>
            <nav>导航菜单链接区域</nav>
            <footer>页脚版权信息区域</footer>
          </body>
        </html>"""
        content = PolicyCollector.parse_content(html)
        assert "正文内容段落展示信息内容呈现" in content
        assert "alert" not in content
        assert "导航" not in content
        assert "页脚" not in content

    def test_short_paragraph_skipped(self):
        html = "<html><body><p>短</p><p>符合长度要求的正文段落信息内容展示</p></body></html>"
        content = PolicyCollector.parse_content(html)
        assert "符合长度要求的正文段落信息内容展示" in content
        assert "短" not in content

    def test_empty_html(self):
        assert PolicyCollector.parse_content("") == ""


# ══════════════════════════════════════════════════════════════
# _safe_get — HTTP 重试
# ══════════════════════════════════════════════════════════════


class TestSafeGet:
    @patch("pipeline.collectors.policy.requests.Session.get")
    def test_retry_on_failure(self, mock_get, collector):
        mock_get.side_effect = [
            __import__("requests").RequestException("err"),
            __import__("requests").RequestException("err"),
            __import__("requests").RequestException("err"),
        ]
        result = collector._safe_get("https://example.com")
        assert result is None
        assert mock_get.call_count == 3

    @patch("pipeline.collectors.policy.requests.Session.get")
    def test_success(self, mock_get, collector):
        mock_get.return_value = MagicMock(text="ok", status_code=200)
        mock_get.return_value.raise_for_status.return_value = None
        result = collector._safe_get("https://example.com")
        assert result == "ok"
        assert mock_get.call_count == 1


# ══════════════════════════════════════════════════════════════
# _collect_regcc — regcc 采集流程
# ══════════════════════════════════════════════════════════════


class TestCollectRegcc:
    def _make_detail_mock(self) -> dict:
        return {
            "title": "稀土产业政策通知",
            "url": "https://www.regcc.cn/page",
            "content": "稀土供应链 战略储备 国家安全 政策规划",
            "published": "2026-07-08T00:00:00+00:00",
            "category": "policy",
        }

    @patch.object(PolicyCollector, "_safe_get")
    def test_happy_path(self, mock_get, collector, cutoff):
        """正常流程：_parse_regcc_list 解析列表 → 过滤 → 取详情。"""
        mock_get.return_value = REGCC_LIST_HTML
        with patch.object(collector, "_fetch_regcc_detail", return_value=self._make_detail_mock()):
            items = collector._collect_regcc(cutoff, pages=1)
        # REGCC_LIST_HTML: 4 items → 1 旧(2025-05 < cutoff 2026-06) → 1 站外(微信) → 2 有效
        assert len(items) >= 1

    @patch.object(PolicyCollector, "_safe_get")
    def test_empty_list_page(self, mock_get, collector):
        """空列表页 → 空结果。"""
        mock_get.return_value = "<html><body><ul class='aclist'></ul></body></html>"
        assert collector._collect_regcc(None, pages=1) == []

    @patch.object(PolicyCollector, "_safe_get")
    def test_no_response(self, mock_get, collector):
        """HTTP 无响应 → 空结果。"""
        mock_get.return_value = None
        assert collector._collect_regcc(None, pages=1) == []


# ══════════════════════════════════════════════════════════════
# _collect_disr — DISR 采集流程
# ══════════════════════════════════════════════════════════════


class TestCollectDisr:
    def _make_detail_mock(self) -> dict:
        return {
            "title": "Critical Minerals Strategy",
            "url": "https://www.industry.gov.au/strategy",
            "content": "Australia critical minerals strategy and supply chain framework for rare earth elements.",
            "published": "2026-07-08T00:00:00+00:00",
            "category": "policy",
        }

    @patch.object(PolicyCollector, "_safe_get")
    def test_happy_path(self, mock_get, collector, cutoff):
        mock_get.return_value = DISR_LIST
        with patch.object(collector, "_fetch_disr_detail", return_value=self._make_detail_mock()):
            items = collector._collect_disr(cutoff)
        assert len(items) >= 1

    @patch.object(PolicyCollector, "_safe_get")
    def test_unreachable(self, mock_get, collector):
        mock_get.return_value = None
        assert collector._collect_disr(None) == []


# ══════════════════════════════════════════════════════════════
# _collect_cn_gov — 中国政府源采集
# ══════════════════════════════════════════════════════════════


class TestCollectCnGov:
    def _make_detail_mock(self) -> dict:
        return {
            "title": "矿产资源规划调整通知",
            "url": "https://www.mnr.gov.cn/notice",
            "content": "矿产资源规划调整通知 供应链安全管理 战略储备",
            "published": "2026-07-08T00:00:00+00:00",
            "category": "policy",
        }

    @patch.object(PolicyCollector, "_safe_get")
    def test_happy_path(self, mock_get, collector, cutoff):
        mock_get.return_value = CN_GOV_LIST
        with patch.object(collector, "_fetch_generic_detail", return_value=self._make_detail_mock()):
            items = collector._collect_cn_gov(cutoff)
        assert len(items) >= 1

    @patch.object(PolicyCollector, "_safe_get")
    def test_unreachable(self, mock_get, collector):
        mock_get.side_effect = __import__("requests").RequestException("timeout")
        assert collector._collect_cn_gov(None) == []

    @patch.object(PolicyCollector, "_safe_get")
    def test_no_policy_links(self, mock_get, collector):
        mock_get.return_value = '<html><body><a href="https://www.mnr.gov.cn">首页链接导航</a></body></html>'
        assert collector._collect_cn_gov(None) == []


# ══════════════════════════════════════════════════════════════
# _fetch_regcc_detail — regcc 详情页
# ══════════════════════════════════════════════════════════════


class TestFetchRegccDetail:
    @patch.object(PolicyCollector, "_safe_get")
    def test_successful_fetch(self, mock_get, collector):
        mock_get.return_value = DETAIL_HTML
        result = collector._fetch_regcc_detail({
            "title": "稀土产业政策通知",
            "url": "https://www.regcc.cn/zgxtjt/jtnew/202607/01/list.shtml",
        })
        assert result is not None
        assert result["title"] == "稀土产业政策通知"
        assert "供应链" in result["content"]
        assert result["published"] is not None

    def test_empty_url(self, collector):
        assert collector._fetch_regcc_detail({"title": "test", "url": ""}) is None

    def test_external_url_skipped(self, collector):
        assert collector._fetch_regcc_detail({
            "title": "test", "url": "https://external.com/page",
        }) is None

    @patch.object(PolicyCollector, "_safe_get")
    def test_http_failure(self, mock_get, collector):
        mock_get.side_effect = __import__("requests").RequestException("err")
        result = collector._fetch_regcc_detail({
            "title": "test",
            "url": "https://www.regcc.cn/page",
        })
        assert result is None


# ══════════════════════════════════════════════════════════════
# collect — 主流程
# ══════════════════════════════════════════════════════════════


class TestCollect:
    @patch.object(PolicyCollector, "_safe_get")
    def test_all_sources_fallback(self, mock_get, collector):
        """所有 HTTP 请求失败 → 无功而返。"""
        mock_get.return_value = None
        result = collector.collect()
        assert not result.succeeded

    @patch.object(PolicyCollector, "_safe_get")
    def test_happy_path(self, mock_get, collector, cutoff):
        """模拟一个源产生数据。"""
        detail = {
            "title": "稀土产业政策通知",
            "url": "https://www.regcc.cn/page",
            "content": "稀土供应链 战略储备 国家安全 政策规划",
            "published": "2026-07-08T00:00:00+00:00",
            "category": "policy",
        }
        mock_get.return_value = REGCC_LIST_HTML  # 使用真实结构
        with patch.object(collector, "_fetch_regcc_detail", return_value=detail):
            result = collector.collect(sources=["regcc"])
        assert result.succeeded
        assert len(result.items) > 0

    def test_max_items_truncation(self):
        collector = PolicyCollector(sources=["regcc"], max_items=1)
        with patch.object(collector, "_scrape_regcc_category") as mock_scrape:
            mock_scrape.return_value = [
                {"url": "https://www.regcc.cn/1", "title": "政策一", "published": "2026-07-08T00:00:00"},
                {"url": "https://www.regcc.cn/2", "title": "政策二", "published": "2026-07-08T00:00:00"},
            ]
            result = collector.collect(pages=0)
        assert len(result.items) <= 1

    def test_unknown_source_skipped(self):
        collector = PolicyCollector(sources=["unknown"])
        result = collector.collect()
        assert not result.succeeded

    @patch.object(PolicyCollector, "_scrape_regcc_category")
    def test_deduplication(self, mock_scrape, collector):
        mock_scrape.return_value = [
            {"url": "https://www.regcc.cn/dup", "title": "政策A", "published": "2026-07-08T00:00:00"},
            {"url": "https://www.regcc.cn/dup", "title": "政策B", "published": "2026-07-08T00:00:00"},
            {"url": "https://www.regcc.cn/unique", "title": "政策C", "published": "2026-07-08T00:00:00"},
        ]
        result = collector.collect(sources=["regcc"], pages=0)
        assert len(result.items) == 2  # dedup: 去掉了重复 URL 的一条

    def test_json_serializable(self, collector):
        with patch.object(collector, "_scrape_regcc_category") as mock_scrape:
            mock_scrape.return_value = [
                {
                    "url": "https://www.regcc.cn/a",
                    "title": "政策A",
                    "published": "2026-07-08T00:00:00",
                    "content": "供应链 稀土 政策",
                    "source": "regcc",
                    "category": "policy",
                },
            ]
            result = collector.collect(sources=["regcc"], pages=0)

        json_str = json.dumps(result.items, ensure_ascii=False, indent=2)
        parsed = json.loads(json_str)
        assert len(parsed) == len(result.items)


# ══════════════════════════════════════════════════════════════
# 常量/配置验证
# ══════════════════════════════════════════════════════════════


class TestConfig:
    def test_regcc_categories(self):
        assert len(REGCC_CATEGORIES) == 10

    def test_disr_list_urls(self):
        assert len(DISR_LIST_URLS) == 2
        for url in DISR_LIST_URLS:
            assert "industry.gov.au" in url

    def test_cn_gov_sources(self):
        assert len(CN_GOV_SOURCES) == 2

    def test_title_keywords_non_empty(self):
        assert len(TITLE_POLICY_ZH) > 10
        assert len(TITLE_MINING_ZH) > 10
        assert len(TITLE_KEYWORDS_EN) > 10

    def test_content_keywords_non_empty(self):
        assert len(WEIGHTED_CONTENT_KEYWORDS) >= 20


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
