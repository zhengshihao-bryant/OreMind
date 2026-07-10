"""索引管道 — 将采集数据切块 → 嵌入 → 写入 ChromaDB。

用法:
  python -m pipeline.embedding.index                         # 全部重建
  python -m pipeline.embedding.index --rebuild                # 强重新建
  python -m pipeline.embedding.index --collection news price  # 只建指定集合

工作流程:
  1. 读取 data/raw/ 中最新的 JSON 文件
  2. 新闻/政策 → RecursiveCharacterTextSplitter 切块
  3. 价格     → 单条即一块
  4. BAAI/bge-small-en-v1.5 嵌入
  5. 写入 ChromaDB (data/vectordb/)
"""

"""索引管道 — 从 data/raw/ 加载 JSON → 切块 → 嵌入 → ChromaDB。

向后兼容入口，新代码请使用 pipeline/pipeline.py 统一调度。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from pipeline.chunkers import chunk as chunk_func
from pipeline.embedding import embed, embed_dimension
from pipeline.vectordb import VectorStore

logger = logging.getLogger(__name__)

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"

COLLECTION_MAP = {"news": "news", "policy": "policy", "price": "price"}


def _latest_file(prefix: str) -> Path | None:
    files = sorted(RAW_DIR.glob(f"{prefix}_*.json"), reverse=True)
    return files[0] if files else None


def load_raw(source: str) -> list[dict[str, Any]]:
    path = _latest_file(source)
    if not path:
        logger.warning("[%s] 未找到数据文件", source)
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items", [])
    logger.info("[%s] 加载 %s → %d 条", source, path.name, len(items))
    return items


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════


def build_index(
    collections: list[str] | None = None,
    rebuild: bool = False,
) -> None:
    """构建索引的主流程。

    :param collections: 要构建的集合列表，默认全部
    :param rebuild:     强制重建（删除已有集合）
    """
    if collections is None:
        collections = ["news", "policy", "price"]

    store = VectorStore()
    dim = embed_dimension()

    for src in collections:
        coll_name = COLLECTION_MAP.get(src)
        if not coll_name:
            logger.warning("未知集合: %s", src)
            continue

        # 可选重建
        if rebuild:
            store.delete_collection(coll_name)

        # 加载原始数据
        items = load_raw(src)
        if not items:
            continue

        # 切块（委托 chunkers 模块）
        chunks = chunk_func(items, src)

        if not chunks:
            logger.warning("[%s] 切块结果为空，跳过", src)
            continue

        # 嵌入
        texts = [c["document"] for c in chunks]
        vectors = embed(texts)

        # 写入 ChromaDB
        store.add(
            collection=coll_name,
            ids=[c["id"] for c in chunks],
            embeddings=vectors,
            metadatas=[c["metadata"] for c in chunks],
            documents=[c["document"] for c in chunks],
        )

        logger.info(
            "[%s] 写入完成: %d 个向量 (dim=%d)",
            coll_name, len(chunks), dim,
        )

    # 打印摘要
    logger.info("=" * 50)
    logger.info("索引摘要:")
    for c in collections:
        cn = COLLECTION_MAP.get(c, c)
        count = store.count(cn)
        logger.info("  %-10s %5d 条", cn, count)
    logger.info("=" * 50)


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="构建向量索引：采集数据 → 切块 → 嵌入 → ChromaDB",
    )
    parser.add_argument(
        "--collection", "-c",
        nargs="+",
        choices=list(COLLECTION_MAP),
        default=list(COLLECTION_MAP),
        help="要构建的集合（默认全部）",
    )
    parser.add_argument(
        "--rebuild", "-r",
        action="store_true",
        help="强制重建（删除已有集合再写入）",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="详细日志",
    )
    return parser.parse_args(argv)


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    ))
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(verbose=args.verbose)

    logger.info("构建向量索引 ...")
    logger.info("  集合:    %s", args.collection)
    logger.info("  重建:    %s", args.rebuild)
    logger.info("  数据源:  %s", RAW_DIR)
    logger.info("  chunk_size=%d, chunk_overlap=%d", CHUNK_SIZE, CHUNK_OVERLAP)

    try:
        build_index(collections=args.collection, rebuild=args.rebuild)
        logger.info("索引构建完成")
        return 0
    except Exception as e:
        logger.exception("索引构建失败: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
