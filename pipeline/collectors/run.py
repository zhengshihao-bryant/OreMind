"""统一运行入口 — 串行执行所有采集器并保存结果。

管道流程:
  NewsCollector       ← mining.com RSS 新闻
       ↓
  PolicyCollector     ← 政府/企业官网政策
       ↓
  PriceCollector      ← LME / SHFE / 上海钢联价格
       ↓
  保存到 data/raw/    ← JSON 文件（每采集器独立文件）

用法:
  python -m pipeline.collectors.run                          # 全部默认
  python -m pipeline.collectors.run --pipeline news price    # 只运行指定采集器
  python -m pipeline.collectors.run --days 7                 # 近7天
  python -m pipeline.collectors.run --output ./my_data       # 自定义输出目录
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.collectors.news import NewsCollector
from pipeline.collectors.policy import PolicyCollector
from pipeline.collectors.price import PriceCollector
from pipeline.collectors.base import CollectResult

logger = logging.getLogger(__name__)

# ── 默认输出目录 ────────────────────────────────────────────

DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "data" / "raw"
# pipeline/collectors/run.py → 向上到 OreMind/ → OreMind/data/raw


# ── 采集器注册表 ────────────────────────────────────────────

# name -> (class, default_kwargs)
COLLECTORS: dict[str, tuple[type, dict[str, Any]]] = {
    "news": (
        NewsCollector,
        {
            "max_articles": 300,
            "days_filter": 30,
        },
    ),
    "policy": (
        PolicyCollector,
        {
            "sources": ["regcc", "disr", "cn_gov", "mining_rss"],
            "max_items": 200,
            "days_filter": 30,
            "pages": 20,
            "content_min_score": 1,
        },
    ),
    "price": (
        PriceCollector,
        {
            "sources": ["lme", "shfe", "mysteel", "yahoo"],
            "max_items": 200,
            "days_filter": 30,
        },
    ),
}

# 默认执行顺序
DEFAULT_PIPELINE = ["news", "policy", "price"]


# ══════════════════════════════════════════════════════════════
# 管道运行器
# ══════════════════════════════════════════════════════════════


class PipelineRunner:
    """串行执行采集管道，将结果保存到 output_dir。

    :param output_dir:  保存目录（默认 data/raw/）
    :param days:        全局时间过滤天数（覆盖各采集器的 days_filter）
    :param max_items:   全局最大条数（覆盖各采集器的 max_items）
    :param verbose:     是否输出详细日志
    """

    def __init__(
        self,
        output_dir: str | Path | None = None,
        days: int | None = None,
        max_items: int | None = None,
        verbose: bool = False,
    ) -> None:
        self.output_dir = Path(output_dir or DEFAULT_OUTPUT)
        self.days = days
        self.max_items = max_items
        self.verbose = verbose
        self.results: dict[str, CollectResult] = {}

    def run(
        self,
        pipeline: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, CollectResult]:
        """按顺序执行采集器。

        :param pipeline: 采集器名称列表，默认 ["news", "policy", "price"]
        :returns:         {采集器名: CollectResult} 字典
        """
        pipeline = pipeline or DEFAULT_PIPELINE
        unknown = set(pipeline) - set(COLLECTORS)
        if unknown:
            logger.error("未知采集器: %s", unknown)
            return {}

        total_start = time.time()

        for name in pipeline:
            collector_cls, default_kwargs = COLLECTORS[name]
            # 合并参数：默认参数 → 全局覆盖 → run 级覆盖
            run_kwargs = {**default_kwargs}
            if self.days is not None:
                # 各采集器的 days 参数名不同
                days_param = {
                    "news": "days_filter",
                    "policy": "days_filter",
                    "price": None,  # price 无时间过滤
                }.get(name)
                if days_param:
                    run_kwargs[days_param] = self.days
            if self.max_items is not None:
                items_param = {
                    "news": "max_articles",
                    "policy": "max_items",
                    "price": "max_items",
                }.get(name, "max_items")
                run_kwargs[items_param] = self.max_items

            # 合并 kwargs 中同名的覆盖
            for k in list(run_kwargs):
                if k in kwargs:
                    run_kwargs[k] = kwargs[k]

            try:
                collector = collector_cls(**run_kwargs)
                logger.info("=" * 50)
                logger.info("[管道] 开始执行 %s 采集器", name)
                logger.info("=" * 50)

                with collector as c:
                    result = c.run()

                self.results[name] = result

                if result.succeeded:
                    count = len(result.items)
                    logger.info(
                        "[管道] %s 采集完成：%d 条，耗时 %.1fs",
                        name, count, result.elapsed_seconds,
                    )
                else:
                    logger.warning(
                        "[管道] %s 采集失败：%s", name, result.error,
                    )

            except Exception as e:
                logger.exception("[管道] %s 异常: %s", name, e)
                self.results[name] = CollectResult(
                    source=name, succeeded=False, error=str(e),
                )

        total_elapsed = time.time() - total_start
        succeeded = sum(1 for r in self.results.values() if r.succeeded)
        total = len(pipeline)
        logger.info(
            "=" * 50,
        )
        logger.info(
            "[管道] 全部完成：%d/%d 成功，总计 %.1fs",
            succeeded, total, total_elapsed,
        )

        return self.results

    def save(self) -> dict[str, Path]:
        """将采集结果保存到 output_dir。

        每个采集器输出一个 JSON 文件：
          data/raw/{name}_{timestamp}.json

        :returns: {采集器名: 保存路径} 字典
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        saved: dict[str, Path] = {}

        for name, result in self.results.items():
            if not result.succeeded:
                logger.warning("[保存] %s 采集失败，跳过保存", name)
                continue
            if not result.items:
                logger.warning("[保存] %s 结果为空，跳过保存", name)
                continue

            file_path = self.output_dir / f"{name}_{timestamp}.json"

            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
                saved[name] = file_path
                logger.info(
                    "[保存] %s → %s （%d 条）",
                    name, file_path, len(result.items),
                )
            except OSError as e:
                logger.error("[保存] %s 写入失败: %s", file_path, e)

        return saved


# ── 摘要报告 ────────────────────────────────────────────────


def print_summary(
    results: dict[str, CollectResult],
    saved: dict[str, Path] | None = None,
) -> None:
    """在终端打印管道执行摘要（含总条数统计）。"""
    sep = "=" * 50
    print(f"\n{sep}")
    print("  管道执行摘要")
    print(sep)

    total_items = 0
    for name, result in results.items():
        icon = "[OK]" if result.succeeded else "[!!]"
        count = len(result.items) if result.succeeded and result.items else 0
        total_items += count
        elapsed = f"{result.elapsed_seconds:.1f}s" if result.succeeded else "-"
        status = result.error or "成功"
        path = ""
        if saved and name in saved:
            path = f"  -> {saved[name]}"
        print(f"  {icon}  {name:<8s}  {count:>5d} 条  {elapsed:>8s}  {status}{path}")

    totals_success = sum(1 for r in results.values() if r.succeeded)
    print(sep)
    print(f"  成功：{totals_success}/{len(results)}  合计：{total_items:>5d} 条")
    print()


# ── CLI ──────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OreMind 数据采集管道 — 串行执行新闻/政策/价格采集并保存",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python -m pipeline.collectors.run                        # 全部默认\n"
            "  python -m pipeline.collectors.run --pipeline news price  # 仅新闻+价格\n"
            "  python -m pipeline.collectors.run --days 7               # 近7天\n"
            "  python -m pipeline.collectors.run --verbose              # 详细日志\n"
            "  python -m pipeline.collectors.run --dry-run              # 仅打印配置\n"
        ),
    )
    parser.add_argument(
        "--pipeline", "-p",
        nargs="+",
        default=DEFAULT_PIPELINE,
        choices=list(COLLECTORS),
        help=f"采集器执行顺序（默认: {' '.join(DEFAULT_PIPELINE)}）",
    )
    parser.add_argument(
        "--days", "-d",
        type=int,
        default=None,
        help="全局时间过滤天数（覆盖各采集器默认的 days_filter）",
    )
    parser.add_argument(
        "--max-items", "-m",
        type=int,
        default=None,
        help="全局最大采集条数",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="输出目录（默认 data/raw/）",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="详细日志输出",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="仅打印配置，不执行采集",
    )
    return parser.parse_args(argv)


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)
    # 清除已有 handler，避免重复
    root.handlers.clear()
    root.addHandler(handler)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    args = parse_args(argv)
    setup_logging(verbose=args.verbose)

    logger.info("OreMind 数据采集管道启动")
    logger.info("  采集器: %s", " → ".join(args.pipeline))
    logger.info("  输出目录: %s", args.output or DEFAULT_OUTPUT)
    if args.days:
        logger.info("  全局时间过滤: %d 天", args.days)
    if args.max_items:
        logger.info("  全局条数上限: %d", args.max_items)

    if args.dry_run:
        logger.info("Dry-run 模式，跳过执行")
        return 0

    runner = PipelineRunner(
        output_dir=args.output,
        days=args.days,
        max_items=args.max_items,
        verbose=args.verbose,
    )

    results = runner.run(pipeline=args.pipeline)
    saved = runner.save()

    print_summary(results, saved)

    # 返回码：全部成功 → 0，部分失败 → 1
    all_succeeded = all(r.succeeded for r in results.values()) and len(results) > 0
    return 0 if all_succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
