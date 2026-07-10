"""去重模块 — URL 去重 + 复合键去重。

用法:
  from pipeline.dedup import URLDeduplicator, PriceDeduplicator
"""

from __future__ import annotations

from typing import Any


class URLDeduplicator:
    """基于 URL 的去重器 — 通用型。"""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def add(self, url: str) -> bool:
        """添加 URL，返回是否为新内容。"""
        if not url or url in self._seen:
            return False
        self._seen.add(url)
        return True

    def is_duplicate(self, url: str) -> bool:
        return url in self._seen

    def __len__(self) -> int:
        return len(self._seen)

    def reset(self) -> None:
        self._seen.clear()


class PriceDeduplicator:
    """基于 commodity+exchange+date 的价格去重器。"""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def add(self, commodity: str, exchange: str, date: str) -> bool:
        key = f"{commodity}_{exchange}_{date}"
        if key in self._seen:
            return False
        self._seen.add(key)
        return True

    def is_duplicate(self, commodity: str, exchange: str, date: str) -> bool:
        key = f"{commodity}_{exchange}_{date}"
        return key in self._seen

    def __len__(self) -> int:
        return len(self._seen)

    def reset(self) -> None:
        self._seen.clear()


class ItemDeduplicator:
    """通用 item 去重器，支持自定义 key 函数。

    :param key_func: 从 item 提取去重键的函数
    """

    def __init__(self, key_func: Any = None) -> None:
        self._seen: set[str] = set()
        self._key_func = key_func or (lambda item: item.get("url", ""))

    def add(self, item: dict[str, Any]) -> bool:
        key = self._key_func(item)
        if not key or key in self._seen:
            return False
        self._seen.add(key)
        return True

    def filter(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """批量过滤去重，保留首次出现的。"""
        result: list[dict[str, Any]] = []
        for item in items:
            if self.add(item):
                result.append(item)
        return result

    def __len__(self) -> int:
        return len(self._seen)

    def reset(self) -> None:
        self._seen.clear()
