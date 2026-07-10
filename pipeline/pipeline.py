"""统一调度管道 — collect → clean → normalize → dedup → chunk → embed → index。

用法:
  python -m pipeline.pipeline                     # 全量运行
  python -m pipeline.pipeline --skip-embed        # 仅采集 → 切块
  python -m pipeline.pipeline --rebuild           # 强制重建向量库
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.chunkers import chunk
from pipeline.collectors.run import DEFAULT_PIPELINE, PipelineRunner
from pipeline.dedup import URLDeduplicator, PriceDeduplicator
from pipeline.embedding import embed, embed_dimension
from pipeline.vectordb import VectorStore

logger = logging.getLogger(__name__)

# 各采集器对应的集合名
COLLECTION_MAP = {"news": "news", "policy": "policy", "price": "price"}


class Pipeline:
    """统一数据管道：采集 → 清洗 → 去重 → 切块 → 嵌入 → 入库。

    用法:
      p = Pipeline()
      p.run()   # 全流程
    """

    def __init__(self, days: int = 30, max_items: int = 200, pages: int = 20) -> None:
        self.days = days
        self.max_items = max_items
        self.pages = pages
        self._stats: dict[str, Any] = {}

    # ── 阶段 1: 采集 ──────────────────────────────────────

    def collect(self, sources: list[str] | None = None) -> dict[str, list[dict[str, Any]]]:
        """遍历各采集器，返回 {source: raw_items}。"""
        runner = PipelineRunner(days=self.days, verbose=True)
        results = runner.run(
            pipeline=sources or DEFAULT_PIPELINE,
            max_items=self.max_items,
            pages=self.pages,
        )
        raw: dict[str, list[dict[str, Any]]] = {}
        for name, result in results.items():
            if result.succeeded and result.items:
                raw[name] = result.items
                logger.info("[collect] %s: %d 条", name, len(result.items))
            else:
                logger.warning("[collect] %s: 未采集到数据", name)
        self._stats["collect"] = {k: len(v) for k, v in raw.items()}
        return raw

        # ── 阶段 2: 清洗 ──────────────────────────────────────

    def clean(self, raw: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
        """HTML 剥离 + 去噪声标签 + 短文本过滤。"""
        from pipeline.cleaners import extract_content

        result: dict[str, list[dict[str, Any]]] = {}
        for source, items in raw.items():
            kept: list[dict[str, Any]] = []
            for item in items:
                content = item.get("content", "")
                if content and "<" in content and ">" in content:
                    item = dict(item)
                    item["content"] = extract_content(content)
                if item.get("content") or item.get("title"):
                    kept.append(item)
            result[source] = kept
        self._stats["clean"] = {k: len(v) for k, v in result.items()}
        return result

    # ── 阶段 3: 标准化 ────────────────────────────────────

    def normalize(self, cleaned: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
        """字段标准化: 日期/币种/单位统一格式。"""
        from pipeline.cleaners.normalize import normalize as norm_fn

        result: dict[str, list[dict[str, Any]]] = {}
        for source, items in cleaned.items():
            result[source] = norm_fn(items)
        self._stats["normalize"] = {k: len(v) for k, v in result.items()}
        return result

    # ── 阶段 4: 去重 ──────────────────────────────────────

    def dedup(self, raw: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
        """调用 dedup 模块跨源去重。"""
        url_dedup = URLDeduplicator()
        price_dedup = PriceDeduplicator()
        result: dict[str, list[dict[str, Any]]] = {}

        for source, items in raw.items():
            keep: list[dict[str, Any]] = []
            for item in items:
                if not url_dedup.add(item):
                    continue  # URL 重复
                # price 额外按 commodity+exchange+date 去重
                meta = item.get("metadata", {})
                if source == "price":
                    if not price_dedup.add(
                        meta.get("commodity", ""),
                        meta.get("exchange", ""),
                        meta.get("date", ""),
                    ):
                        continue
                keep.append(item)
            result[source] = keep

        self._stats["dedup"] = {k: len(v) for k, v in result.items()}
        removed = sum(len(raw.get(k, [])) for k in raw) - sum(len(v) for v in result.values())
        if removed:
            logger.info("[dedup] 去除 %d 条重复", removed)
        return result

    # ── 阶段 4: 切块 ──────────────────────────────────────

    def chunk(self, deduped: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
        """按类型切块。"""
        result: dict[str, list[dict[str, Any]]] = {}
        for source, items in deduped.items():
            result[source] = chunk(items, source)
        self._stats["chunk"] = {k: len(v) for k, v in result.items()}
        return result

    # ── 阶段 5: 嵌入 + 入库 ───────────────────────────────

    def embed_and_store(
        self,
        chunks: dict[str, list[dict[str, Any]]],
        rebuild: bool = False,
    ) -> VectorStore:
        """BGE 嵌入 → ChromaDB 写入。"""
        store = VectorStore()
        dim = embed_dimension()

        for source, items in chunks.items():
            coll_name = COLLECTION_MAP.get(source, source)
            if rebuild:
                store.delete_collection(coll_name)
            if not items:
                continue

            texts = [c["document"] for c in items]
            vectors = embed(texts)
            store.add(
                collection=coll_name,
                ids=[c["id"] for c in items],
                embeddings=vectors,
                metadatas=[c["metadata"] for c in items],
                documents=[c["document"] for c in items],
            )
            logger.info(
                "[store] %s: %d vectors (dim=%d)", coll_name, len(items), dim,
            )

        self._stats["store"] = {c: store.count(c) for c in COLLECTION_MAP}
        return store

    # ── 全流程 ───────────────────────────────────────────

    def run(
        self,
        sources: list[str] | None = None,
        rebuild: bool = False,
        skip_embed: bool = False,
    ) -> dict[str, Any]:
        """执行完整管道。

        :param sources:   采集器列表 (默认 news/policy/price)
        :param rebuild:   强制重建向量库
        :param skip_embed: 跳过嵌入 + 入库，仅输出 JSON
        :returns:         {阶段名: {源: 数量}}
        """
        start = time.perf_counter()

        logger.info("=" * 50)
        logger.info("  管道启动")
        logger.info("=" * 50)

        # 1. 采集
        timings: dict[str, float] = {}
        t0 = time.perf_counter()
        raw = self.collect(sources)
        timings["collect"] = round(time.perf_counter() - t0, 2)

        if not raw:
            logger.warning("没有数据，管道终止")
            return self._stats

        # 2. 清洗
        t0 = time.perf_counter()
        cleaned = self.clean(raw)
        timings["clean"] = round(time.perf_counter() - t0, 2)

        # 3. 标准化 (日期/币种/单位)
        t0 = time.perf_counter()
        normalized = self.normalize(cleaned)
        timings["normalize"] = round(time.perf_counter() - t0, 2)

        # 4. 去重
        t0 = time.perf_counter()
        deduped = self.dedup(normalized)
        timings["dedup"] = round(time.perf_counter() - t0, 2)

        # 5. 切块
        t0 = time.perf_counter()
        chunks_data = self.chunk(deduped)
        timings["chunk"] = round(time.perf_counter() - t0, 2)

        if skip_embed:
            logger.info("跳过嵌入+入库 (--skip-embed)")
        else:
            t0 = time.perf_counter()
            self.embed_and_store(chunks_data, rebuild=rebuild)
            timings["embed_store"] = round(time.perf_counter() - t0, 2)

        total = round(time.perf_counter() - start, 2)

        # 保存监控数据
        import json
        from datetime import datetime
        monitor = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_seconds": total,
            "stage_timings": timings,
            "stage_counts": self._stats,
        }
        monitor_path = settings.PROJECT_ROOT / "data" / "logs" / "pipeline_monitor.json"
        monitor_path.parent.mkdir(parents=True, exist_ok=True)
        monitor_path.write_text(json.dumps(monitor, ensure_ascii=False, indent=2), encoding="utf-8")

        # 摘要输出
        sep = "=" * 50
        print(f"\n{sep}")
        print("  管道执行摘要")
        print(sep)
        for stage, counts in self._stats.items():
            parts = [f"{k}:{v}" for k, v in counts.items()]
            total_n = sum(counts.values())
            t = timings.get(stage, 0)
            print(f"  {stage:<12s}  {', '.join(parts):40s}  ∑{total_n:>4d}  {t:>6.1f}s")
        print(f"  {'total':<12s}  {'':40s}  {'':>4s}  {total:>6.1f}s")
        print(sep)
        print(f"  监控数据 → {monitor_path}")
        print()

        self._stats["_timings"] = timings
        self._stats["_total_seconds"] = total
        return self._stats


# ── CLI ──────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OreMind 统一数据管道")
    parser.add_argument("--sources", "-s", nargs="+", default=None,
                        help="采集器 (默认 all)")
    parser.add_argument("--rebuild", "-r", action="store_true",
                        help="强制重建向量库")
    parser.add_argument("--skip-embed", action="store_true",
                        help="跳过嵌入入库，仅输出 JSON")
    parser.add_argument("--days", type=int, default=30,
                        help="时间窗口 (天)")
    parser.add_argument("--max-items", type=int, default=200,
                        help="单源最大条数")
    return parser.parse_args(argv)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging()

    pipeline = Pipeline(days=args.days, max_items=args.max_items)
    pipeline.run(sources=args.sources, rebuild=args.rebuild, skip_embed=args.skip_embed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
