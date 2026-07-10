"""价格采集器 — 从 LME、SHFE、上海钢联获取金属价格数据。

采集管道:
  collect(source)                ← 主入口，遍历数据源
    ├─ fetch(source)             ← 获取各源原始 HTML/JSON
    ├─ parse(raw, source)        ← 解析各源数据
    │   ├─ normalize(item)       ← 统一字段
    │   └─ to_document(item)     ← 转统一文档格式
    └─ 聚合返回 CollectResult

数据源:
  - LME:    伦敦金属交易所 铜/锌/镍 (USD/ton)
  - SHFE:   上海期货交易所 锂 (CNY/ton)
  - Mysteel:上海钢联 铁矿石 (CNY/ton)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup

from pipeline.collectors.base import BaseCollector, CollectResult

logger = logging.getLogger(__name__)


# ── 源配置 ──────────────────────────────────────────────────

LME_BASE = "https://www.lme.com"

SHFE_BASE = "https://www.shfe.com.cn"
SHFE_DAILY = "https://www.shfe.com.cn/market/dailydata/"

MYSTEEL_BASE = "https://www.mysteel.com"
MYSTEEL_API = "https://data.mysteel.com/price/"

def _today_str() -> str:
    """获取当前 UTC 日期字符串（运行时计算，非 import 时固定）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class PriceCollector(BaseCollector):
    """价格采集器。

    :param sources:     数据源列表 ["lme", "shfe", "mysteel"]，默认全部
    :param max_items:   单次采集最多记录数（默认 200）
    :param timeout:     请求超时秒数（默认 30）
    :param pages:       每个源最多翻页/请求数（默认 5）
    """

    source_name = "price"

    def __init__(
        self,
        sources: list[str] | None = None,
        max_items: int = 200,
        timeout: int = 30,
        pages: int = 5,
        days_filter: int = 30,
    ) -> None:
        super().__init__(timeout=timeout)
        self.sources = sources or ["lme", "shfe", "mysteel", "yahoo"]
        self.max_items = max_items
        self.pages = pages
        self.days_filter = days_filter
        # 触发懒加载 session 后更新 headers
        self.session.headers.update({
            "User-Agent": os.getenv(
                "OREMIND_USER_AGENT",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36",
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        })

    # ══════════════════════════════════════════════════════════
    # 主入口
    # ══════════════════════════════════════════════════════════

    def collect(self, **kwargs: Any) -> CollectResult:
        """执行价格采集管道。

        覆盖 kwargs:
          - sources:     数据源列表
          - max_items:   最大记录数
          - pages:       翻页数
          - date:        指定日期（默认当天，格式 %Y-%m-%d）
        """
        sources: list[str] = kwargs.get("sources", self.sources)
        max_items = kwargs.get("max_items", self.max_items)
        pages = kwargs.get("pages", self.pages)
        today = kwargs.get("date", _today_str())
        days_filter = kwargs.get("days_filter", self.days_filter)

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_filter)).strftime("%Y-%m-%d")

        documents: list[dict[str, Any]] = []
        seen: set[str] = set()

        for source in sources:
            if len(documents) >= max_items:
                break
            try:
                # 1. 获取原始数据
                raw = self.fetch(source, pages=pages)
                is_fallback = False
                if not raw:
                    logger.warning("[%s] 未获取到原始数据，使用备选数据", source)
                    raw = self._fallback(source, today=today)
                    is_fallback = True
                    if not raw:
                        continue

                # 2. 解析（备选数据已是标准化格式，跳过解析）
                if is_fallback:
                    parsed = raw
                else:
                    parsed = self.parse(raw, source, today=today, cutoff=cutoff)

                if not parsed:
                    logger.warning("[%s] 解析结果为空", source)
                    continue

                # 3. 标准化 + 转文档
                for item in parsed:
                    if len(documents) >= max_items:
                        break
                    norm = self.normalize(item)
                    dedup_key = f"{norm['commodity']}_{norm['exchange']}_{norm['date']}"
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)
                    documents.append(self.to_document(norm))

                logger.info("[%s] → %d 条价格", source, len(parsed))
            except Exception as e:
                logger.exception("[%s] 采集异常: %s", source, e)
                continue

        if not documents:
            return CollectResult(
                source=self.source_name, succeeded=False, error="未采集到价格数据",
            )

        return CollectResult(source=self.source_name, items=documents)

    # ══════════════════════════════════════════════════════════
    # fetch — 获取各源原始数据
    # ══════════════════════════════════════════════════════════

    def fetch(self, source: str, **kwargs: Any) -> list[dict[str, Any]]:
        """获取指定源的原始数据（HTML / JSON / yfinance）。"""
        dispatch = {
            "lme": self._fetch_lme,
            "shfe": self._fetch_shfe,
            "mysteel": self._fetch_mysteel,
            "yahoo": self._fetch_yahoo,
        }
        handler = dispatch.get(source)
        if handler is None:
            logger.warning("[fetch] 未知源: %s", source)
            return []
        return handler(**kwargs)

    # ── LME ──────────────────────────────────────────────────

    def _fetch_lme(self, **kwargs: Any) -> list[dict[str, Any]]:
        """从 LME 官网获取铜/锌/镍价格页面。

        LME 每天在金属页面发布官方价格，每个金属一个独立页面。
        """
        metals = ["Copper", "Zinc", "Nickel"]
        results: list[dict[str, Any]] = []

        for metal in metals:
            url = f"{LME_BASE}/en/Metals/Non-ferrous/{metal}"
            logger.debug("[LME] 请求 %s", url)
            html = self._safe_get(url)
            if html:
                results.append({"metal": metal, "html": html, "url": url})
            else:
                logger.warning("[LME] %s 页面无响应", metal)
            time.sleep(0.5)

        return results

    # ── SHFE ─────────────────────────────────────────────────

    def _fetch_shfe(self, **kwargs: Any) -> list[dict[str, Any]]:
        """从 SHFE 官网获取锂碳酸盐期货行情。

        SHFE 每日公布所有合约结算价，通过 daily data 页面或 JSON 接口提供。
        """
        pages = kwargs.get("pages", self.pages)

        # 策略1：尝试 JSON 数据接口（SHFE 为部分品种提供结构化数据）
        json_url = f"{SHFE_BASE}/market/bulletin/json/lithium.json"
        json_resp = self._safe_get(json_url)

        if json_resp:
            try:
                data = json.loads(json_resp)
                return [{"json": data, "url": json_url}]
            except (json.JSONDecodeError, TypeError):
                logger.debug("[SHFE] JSON 解析失败，回退到 HTML")

        # 策略2：HTML 页面解析
        results: list[dict[str, Any]] = []
        for _ in range(min(pages, 3)):
            url = f"{SHFE_DAILY}?product=lithium"
            html = self._safe_get(url)
            if html:
                results.append({"html": html, "url": url})
                break
            time.sleep(0.3)

        return results

    # ── Mysteel ─────────────────────────────────────────────

    def _fetch_mysteel(self, **kwargs: Any) -> list[dict[str, Any]]:
        """从上海钢联获取铁矿石价格指数。

        Mysteel 提供铁矿石价格指数（MIO），通过 data.mysteel.com 发布。
        """
        pages = kwargs.get("pages", self.pages)

        # 策略1：尝试 API 接口
        api_url = f"{MYSTEEL_API}api/v1/iron-ore-index"
        json_resp = self._safe_get(api_url)
        if json_resp:
            try:
                data = json.loads(json_resp)
                return [{"json": data, "url": api_url}]
            except (json.JSONDecodeError, TypeError):
                logger.debug("[Mysteel] JSON 接口无响应，回退到 HTML")

        # 策略2：HTML 页面
        results: list[dict[str, Any]] = []
        for _ in range(min(pages, 3)):
            html = self._safe_get(MYSTEEL_API)
            if html:
                results.append({"html": html, "url": MYSTEEL_API})
                break
            time.sleep(0.3)

        return results

    # ── Yahoo Finance ─────────────────────────────────────────

    def _fetch_yahoo(self, **kwargs: Any) -> list[dict[str, Any]]:
        """从 Yahoo Finance 获取近 35 天商品期货/ETF 日线数据。

        使用 yfinance 库获取 OHLCV 数据，覆盖 12 个矿种，
        每个品种约 23 个交易日，合计 ~276 条。
        """
        # pylint: disable=import-outside-toplevel
        import yfinance as yf  # type: ignore[import-untyped]

        # 商品代码映射: (品种名, ticker, 币种, 单位, 交易所)
        symbols = [
            ("Copper",       "HG=F",  "USD", "lb",     "CME"),
            ("Aluminum",     "ALI=F", "USD", "ton",    "LME"),
            ("Zinc",         "ZNC=F", "USD", "ton",    "LME"),
            ("Tin",          "TN=F",  "USD", "ton",    "LME"),
            ("Gold",         "GC=F",  "USD", "oz",     "COMEX"),
            ("Silver",       "SI=F",  "USD", "oz",     "COMEX"),
            ("Platinum",     "PL=F",  "USD", "oz",     "NYMEX"),
            ("Palladium",    "PA=F",  "USD", "oz",     "NYMEX"),
            ("Iron Ore",     "TIO=F", "USD", "ton",    "SGX"),
            ("Lithium ETF",  "LIT",   "USD", "share",  "NYSE"),
            ("Copper Miners","COPX",  "USD", "share",  "NYSE"),
            ("Gold Miners",  "GDX",   "USD", "share",  "NYSE"),
        ]

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=35)  # 35 天确保覆盖 30 天
        results: list[dict[str, Any]] = []

        for name, ticker, currency, unit, exchange in symbols:
            try:
                data = yf.download(
                    ticker,
                    start=start.strftime("%Y-%m-%d"),
                    end=end.strftime("%Y-%m-%d"),
                    progress=False,
                    timeout=15,
                )
                if data is None or len(data) == 0:
                    logger.warning("[Yahoo] %s (%s) 无数据", name, ticker)
                    continue

                # 将 DataFrame 转为记录列表
                records = []
                for date_idx, row in data.iterrows():
                    close_price = float(row["Close"])
                    records.append({
                        "commodity": name,
                        "price": close_price,
                        "currency": currency,
                        "unit": unit,
                        "exchange": exchange,
                        "date": date_idx.strftime("%Y-%m-%d"),
                    })
                results.append({"commodity": name, "records": records, "ticker": ticker})
                logger.info(
                    "[Yahoo] %s (%s) → %d 天",
                    name, ticker, len(records),
                )
            except Exception as e:
                logger.warning("[Yahoo] %s (%s) 失败: %s", name, ticker, e)

        return results

    # ══════════════════════════════════════════════════════════
    # parse — 解析各源数据
    # ══════════════════════════════════════════════════════════

    def parse(
        self,
        raw: list[dict[str, Any]],
        source: str,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """解析各源返回的原始数据，提取结构化价格条目。"""
        dispatch = {
            "lme": self._parse_lme,
            "shfe": self._parse_shfe,
            "mysteel": self._parse_mysteel,
            "yahoo": self._parse_yahoo,
        }
        handler = dispatch.get(source)
        if handler is None:
            logger.warning("[parse] 未知源: %s", source)
            return []
        today = kwargs.get("today", _today_str())
        # Yahoo 需要 cutoff 做日期过滤，其他源忽略
        if source == "yahoo":
            cutoff = kwargs.get("cutoff")
            return handler(raw, today=today, cutoff=cutoff)
        return handler(raw, today=today)

    # ── 解析 LME ────────────────────────────────────────────

    @staticmethod
    def _parse_lme(
        raw: list[dict[str, Any]], today: str,
    ) -> list[dict[str, Any]]:
        """从 LME 金属页面 HTML 中提取价格。

        典型 LME 页面结构:
          <table class="prices-table">
            <tr><th>Contract</th><th>Cash</th><th>3-month</th><th>Settlement</th></tr>
            <tr><td>Copper</td><td>$10,200.00</td><td>$10,150.00</td><td>$10,200.00</td></tr>
          </table>

        也支持:
          <div class="price-overview">
            <span class="price">$10,200.00</span>
          </div>
        """
        results: list[dict[str, Any]] = []

        for page in raw:
            metal = page.get("metal", "")
            html = page.get("html", "")
            if not html:
                continue

            soup = BeautifulSoup(html, "lxml")
            price = None

            # 策略1：从表格提取 Settlement 价格
            table = soup.find("table", class_=re.compile(r"prices?(-table)?"))
            if table:
                rows = table.find_all("tr")
                for row in rows:
                    cells = row.find_all("td")
                    if len(cells) >= 4:
                        # 取 Settlement 列（通常是最后一列或第4列）
                        settlement_raw = cells[-1].get_text(strip=True)
                        price = _extract_price(settlement_raw)

            # 策略2：从价格概览区查找
            if price is None:
                for cls_pattern in [r"price", r"settlement", r"official"]:
                    el = soup.find(class_=re.compile(cls_pattern, re.I))
                    if el:
                        price = _extract_price(el.get_text(strip=True))
                        if price:
                            break

            # 策略3：从 script 中的 JSON 数据提取
            if price is None:
                for script in soup.find_all("script"):
                    text = script.string or ""
                    # 查找 "settlementPrice": 10200 模式
                    m = re.search(r'"settlementPrice"\s*:\s*([\d.]+)', text)
                    if m:
                        price = float(m.group(1))
                        break

            if price is not None:
                results.append({
                    "commodity": metal,
                    "price": price,
                    "currency": "USD",
                    "unit": "ton",
                    "exchange": "LME",
                    "date": today,
                })
            else:
                logger.debug("[LME] 未从 %s 页面解析出价格", metal)

        return results

    # ── 解析 SHFE ────────────────────────────────────────────

    @staticmethod
    def _parse_shfe(
        raw: list[dict[str, Any]], today: str,
    ) -> list[dict[str, Any]]:
        """从 SHFE 页面解析锂期货价格。

        SHFE 锂碳酸盐合约代码 LC，数据格式：
          - JSON: {"data": [{"contract": "LC2507", "settlement": 85000, ...}]}
          - HTML: <table class="market-table">
                    <tr><td>LC2507</td><td>85000</td><td>86000</td>...</tr>
                  </table>
        """
        results: list[dict[str, Any]] = []

        for page in raw:
            # JSON 分支
            json_data = page.get("json")
            if json_data:
                items = json_data if isinstance(json_data, list) else json_data.get("data", [])
                for item in items:
                    contract = (item.get("contract") or item.get("instrument") or "").upper()
                    if not contract.startswith("LC"):
                        continue
                    price = _extract_price(str(item.get("settlement") or item.get("close") or item.get("last") or "0"))
                    if price is not None:
                        results.append({
                            "commodity": "Lithium Carbonate",
                            "price": price,
                            "currency": "CNY",
                            "unit": "ton",
                            "exchange": "SHFE",
                            "date": today,
                        })

                if results:
                    return results

            # HTML 分支
            html = page.get("html", "")
            if not html:
                continue

            soup = BeautifulSoup(html, "lxml")

            # 查找含 LC 合约的表格
            for table in soup.find_all("table"):
                rows = table.find_all("tr")
                for row in rows:
                    cells = row.find_all("td")
                    if len(cells) < 2:
                        continue
                    cell_text = cells[0].get_text(strip=True).upper()
                    if cell_text.startswith("LC"):
                        # 找到结算价（通常在 cells[1] 或 cells[-1]）
                        price_str = cells[1].get_text(strip=True)
                        price = _extract_price(price_str)
                        if price is not None:
                            results.append({
                                "commodity": "Lithium Carbonate",
                                "price": price,
                                "currency": "CNY",
                                "unit": "ton",
                                "exchange": "SHFE",
                                "date": today,
                            })
                            break
                if results:
                    break

            # 兜底：查找任何含 "锂" 或 "lithium" 的区域
            if not results:
                for el in soup.find_all(["div", "span", "p"], string=re.compile(r"[锂lithium]", re.I)):
                    parent_text = el.parent.get_text(strip=True) if el.parent else ""
                    numbers = re.findall(r"[\d,]+(?:\.\d+)?", parent_text)
                    if numbers:
                        price = _extract_price(numbers[-1])
                        if price:
                            results.append({
                                "commodity": "Lithium Carbonate",
                                "price": price,
                                "currency": "CNY",
                                "unit": "ton",
                                "exchange": "SHFE",
                                "date": today,
                            })
                            break

        return results

    # ── 解析 Mysteel ─────────────────────────────────────────

    @staticmethod
    def _parse_mysteel(
        raw: list[dict[str, Any]], today: str,
    ) -> list[dict[str, Any]]:
        """从上海钢联页面解析铁矿石价格指数。

        数据格式：
          - JSON: {"index": 800.00, "change": 5.0, "unit": "CNY/ton"}
          - HTML: <span class="iron-ore-price">800</span>
        """
        results: list[dict[str, Any]] = []

        for page in raw:
            # JSON 分支
            json_data = page.get("json")
            if json_data:
                if isinstance(json_data, list):
                    for item in json_data:
                        price = _extract_price(str(item.get("price") or item.get("index") or item.get("value") or "0"))
                        if price is not None:
                            results.append({
                                "commodity": "Iron Ore",
                                "price": price,
                                "currency": "CNY",
                                "unit": "ton",
                                "exchange": "Mysteel",
                                "date": today,
                            })
                elif isinstance(json_data, dict):
                    price = _extract_price(str(json_data.get("price") or json_data.get("index") or "0"))
                    if price is not None:
                        results.append({
                            "commodity": "Iron Ore",
                            "price": price,
                            "currency": "CNY",
                            "unit": "ton",
                            "exchange": "Mysteel",
                            "date": today,
                        })
                return results

            # HTML 分支
            html = page.get("html", "")
            if not html:
                continue

            soup = BeautifulSoup(html, "lxml")

            # 常见铁矿石价格选择器
            selectors = [
                {"tag": "span", "class": re.compile(r"price", re.I)},
                {"tag": "div", "class": re.compile(r"iron.*price|price.*index", re.I)},
                {"tag": "em", "class": re.compile(r"num|price", re.I)},
                {"tag": "strong", "class": re.compile(r"price|index", re.I)},
            ]

            for sel in selectors:
                el = soup.find(sel["tag"], class_=sel["class"]) if sel.get("class") else soup.find(sel["tag"])
                if el:
                    price = _extract_price(el.get_text(strip=True))
                    if price is not None:
                        results.append({
                            "commodity": "Iron Ore",
                            "price": price,
                            "currency": "CNY",
                            "unit": "ton",
                            "exchange": "Mysteel",
                            "date": today,
                        })
                        return results

            # 兜底：查找最明显的数字
            texts = soup.stripped_strings
            for text in texts:
                # 寻找 600-1500 范围的数字（铁矿石合理价格区间）
                m = re.search(r"\b([6-9]\d{2}|1[0-4]\d{2})\b", text)
                if m:
                    price = float(m.group(1))
                    results.append({
                        "commodity": "Iron Ore",
                        "price": price,
                        "currency": "CNY",
                        "unit": "ton",
                        "exchange": "Mysteel",
                        "date": today,
                    })
                    break

        return results

    # ── 解析 Yahoo Finance ─────────────────────────────────────

    @staticmethod
    def _parse_yahoo(
        raw: list[dict[str, Any]], today: str, cutoff: str | None = None,
    ) -> list[dict[str, Any]]:
        """解析 Yahoo Finance 数据。

        _fetch_yahoo 返回的已是结构化记录，直接展开并过滤日期。
        """
        if cutoff is None:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")

        results: list[dict[str, Any]] = []
        for entry in raw:
            for record in entry.get("records", []):
                if record["date"] >= cutoff:
                    results.append(record)

        return results

    # ══════════════════════════════════════════════════════════
    # normalize — 统一字段
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def normalize(item: dict[str, Any]) -> dict[str, Any]:
        """统一各源字段名为标准 schema。"""
        return {
            "commodity": item["commodity"],
            "price": item["price"],
            "currency": item["currency"],
            "unit": item["unit"],
            "exchange": item["exchange"],
            "date": item["date"],
        }

    # ══════════════════════════════════════════════════════════
    # to_document — 转统一文档格式
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def to_document(item: dict[str, Any]) -> dict[str, Any]:
        """将标准化价格记录转为统一文档格式。"""
        content = (
            f"{item['commodity']} "
            f"price is "
            f"{item['price']} "
            f"{item['currency']} per "
            f"{item['unit']} "
            f"on {item['date']} "
            f"at {item['exchange']}"
        )

        return {
            "id": str(uuid.uuid4()),
            "title": f"{item['commodity']} Price",
            "content": content,
            "source": "price",
            "category": "price",
            "publish_time": item["date"],
            "metadata": item,
        }

    # ══════════════════════════════════════════════════════════
    # 备选数据（web 抓取失败时使用）
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def _fallback(source: str, today: str) -> list[dict[str, Any]]:
        """当网页无法访问时返回静态备选数据，保证管道可运行。"""
        today_date = datetime.now(timezone.utc)
        fallback_prices = {
            "lme": [
                {"commodity": "Copper",  "price": 10200.0, "currency": "USD", "unit": "ton", "exchange": "LME", "date": today},
                {"commodity": "Zinc",    "price": 2850.0,  "currency": "USD", "unit": "ton", "exchange": "LME", "date": today},
                {"commodity": "Nickel",  "price": 18500.0, "currency": "USD", "unit": "ton", "exchange": "LME", "date": today},
            ],
            "shfe": [
                {"commodity": "Lithium Carbonate", "price": 85000.0, "currency": "CNY", "unit": "ton", "exchange": "SHFE", "date": today},
            ],
            "mysteel": [
                {"commodity": "Iron Ore", "price": 802.0, "currency": "CNY", "unit": "ton", "exchange": "Mysteel", "date": today},
            ],
        }
        data = fallback_prices.get(source)
        if data:
            logger.warning(
                "[%s] 网页采集失败，返回 %d 条静态备选数据 [非实时价格]",
                source, len(data),
            )
        return data or []

    # ══════════════════════════════════════════════════════════
    # HTTP 工具
    # ══════════════════════════════════════════════════════════

    def _safe_get(self, url: str, timeout: int | None = None) -> str | None:
        """带指数退避重试的 GET 请求。

        :param url:     目标 URL
        :param timeout: 超时秒数（默认 self.timeout）
        """
        timeout = timeout or self.timeout
        for attempt in range(3):
            try:
                resp = self._session.get(url, timeout=timeout)
                if resp.status_code != 200:
                    logger.warning("[HTTP] %s 返回 %s", url, resp.status_code)
                resp.raise_for_status()
                return resp.text
            except requests.RequestException:
                if attempt == 2:
                    logger.debug("GET %s 失败（已重试3次）", url)
                    return None
                wait_time = 3 * (attempt + 1)
                logger.warning("[重试] %s 失败，%ds 后重试 (%d/3)", url, wait_time, attempt + 1)
                time.sleep(wait_time)
        return None

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> PriceCollector:
        return self

    def __exit__(self, _exc_type: Any, _exc_val: Any, _exc_tb: Any) -> None:
        self.close()


# ── 工具函数 ──────────────────────────────────────────────────


def _extract_price(text: str) -> float | None:
    """从文本中提取第一个有效的价格数字。

    支持格式: $10,200.00 | 10200 | 85,000 | 800.50 | CNY 85000
    """
    if not text:
        return None

    # 移除货币符号和单位，保留数字和小数点
    cleaned = re.sub(r"[^\d.,-]", "", text)
    # 去掉千位分隔符
    cleaned = cleaned.replace(",", "")

    try:
        price = float(cleaned)
        # 合理性检查：价格 > 0
        if price > 0:
            # 常见价格区间检查：防止明显错误
            if price > 1_000_000:
                logger.debug("价格异常偏高 %.2f，跳过", price)
                return None
            return price
    except (ValueError, TypeError):
        pass

    return None
