"""采集器基类 — 所有数据采集器的抽象接口与通用工具。"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


# ── 公共数据类型 ──────────────────────────────────────────────

@dataclass
class CollectResult:
    """一次采集任务的标准化结果。"""
    source: str                              # 数据来源标识，如 "news_api"
    items: list[dict[str, Any]] = field(default_factory=list)
    collected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    succeeded: bool = True
    error: Optional[str] = None
    elapsed_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "items": self.items,
            "collected_at": self.collected_at.isoformat(),
            "succeeded": self.succeeded,
            "error": self.error,
            "elapsed": self.elapsed_seconds,
            "metadata": self.metadata,
        }


# ── 采集器基类 ────────────────────────────────────────────────

class BaseCollector(ABC):
    """所有采集器必须继承此类并实现 :meth:`collect`。"""

    # 子类覆盖
    source_name: str = "unknown"

    def __init__(
        self,
        base_url: str = "",
        timeout: int = 30,
        max_retries: int = 3,
        concurrency: int = 1,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.concurrency = concurrency

        self._session: Optional[requests.Session] = None

    # ── 子类必须实现 ──────────────────────────────────────────

    @abstractmethod
    def collect(self, **kwargs: Any) -> CollectResult:
        """执行一次采集，返回标准化结果。"""
        ...

    # ── 流水线分段（子类可选择性重写） ──────────────────────

    def clean(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """清洗阶段：HTML 剥离、正文提取、短文本过滤。

        默认实现调用 cleaners 模块，子类可覆盖。
        """
        from pipeline.cleaners import extract_content

        result: list[dict[str, Any]] = []
        for item in items:
            html = item.pop("_raw_html", None) or item.get("content", "")
            if html:
                item["content"] = extract_content(html)
            # 过滤无内容项
            if item.get("content") or item.get("title"):
                result.append(item)
        return result

    def dedup(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """去重阶段：URL / 复合键去重。

        默认实现调用 dedup 模块，子类可覆盖。
        """
        from pipeline.dedup import URLDeduplicator, PriceDeduplicator

        url_dedup = URLDeduplicator()
        price_dedup = PriceDeduplicator()
        result: list[dict[str, Any]] = []

        for item in items:
            url = item.get("url", "")
            if url and not url_dedup.add(url):
                continue
            # price 额外按 commodity+exchange+date 去重
            source = item.get("source") or item.get("metadata", {}).get("exchange", "")
            meta = item.get("metadata", {})
            if source == "price" or "price" in str(source):
                if not price_dedup.add(
                    meta.get("commodity", ""),
                    meta.get("exchange", ""),
                    meta.get("date", ""),
                ):
                    continue
            result.append(item)
        return result

    def run(self, **kwargs: Any) -> CollectResult:
        """管道入口：collect → clean → dedup → 打包。

        子类只需实现 collect()，父类自动串联后续阶段。
        """
        start = time.time()
        logger.info("[%s] 管道启动", self.source_name)
        try:
            # 1. 采集
            collect_result = self.collect(**kwargs)
            if not collect_result.succeeded or not collect_result.items:
                logger.warning("[%s] 采集阶段返回空", self.source_name)
                return collect_result

            raw_items = collect_result.items

            # 2. 清洗
            cleaned = self.clean(raw_items)

            # 3. 去重
            deduped = self.dedup(cleaned)

            logger.info(
                "[%s] 管道完成: raw=%d → clean=%d → dedup=%d, %.1fs",
                self.source_name,
                len(raw_items), len(cleaned), len(deduped),
                time.time() - start,
            )
            return CollectResult(
                source=self.source_name,
                items=deduped,
                metadata={
                    "stage_counts": {
                        "raw": len(raw_items),
                        "clean": len(cleaned),
                        "dedup": len(deduped),
                    }
                },
            )
        except Exception as e:
            logger.exception("[%s] 管道异常: %s", self.source_name, e)
            return CollectResult(
                source=self.source_name, succeeded=False, error=str(e),
            )

    # ── HTTP 工具 ─────────────────────────────────────────────

    @property
    def session(self) -> requests.Session:
        """带重试机制的复用 HTTP session（懒加载）。"""
        if self._session is None:
            self._session = self._build_session()
        return self._session

    def _build_session(self) -> requests.Session:
        sess = requests.Session()
        retry = Retry(
            total=self.max_retries,
            backoff_factor=0.5,
            status_forcelist={429, 500, 502, 503, 504},
            allowed_methods={"GET", "POST"},
        )
        adapter = HTTPAdapter(max_retries=retry)
        sess.mount("http://", adapter)
        sess.mount("https://", adapter)
        return sess

    def _get(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> requests.Response:
        """带超时与日志的 GET 请求。"""
        url = f"{self.base_url}{path}"
        logger.debug("GET %s  params=%s", url, params)
        resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp

    def _post(
        self,
        path: str,
        json: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> requests.Response:
        """带超时与日志的 POST 请求。"""
        url = f"{self.base_url}{path}"
        logger.debug("POST %s", url)
        resp = self.session.post(url, json=json, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp

    # ── 通用工具 ─────────────────────────────────────────────

    @staticmethod
    def now() -> datetime:
        """当前 UTC 时间（采集时间戳用）。"""
        return datetime.now(timezone.utc)

    @staticmethod
    def elapsed(start: float) -> float:
        """计算耗时（秒）。"""
        return round(time.time() - start, 3)

    # ── 流水线分段 ──────────────────────────────────────

    def close(self) -> None:
        """释放 HTTP session。"""
        if self._session:
            self._session.close()
            self._session = None

    def __enter__(self) -> BaseCollector:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
