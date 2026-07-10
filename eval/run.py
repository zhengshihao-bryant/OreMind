"""Eval — 检索器自动化评测。

指标:
  - recall@5:  前 5 条结果中命中 ground truth 的比例
  - precision@5: 前 5 条结果中相关的比例
  - intent_accuracy: 意图识别正确的比例
  - latency_p50: 检索延迟中位数

用法:
  python -m eval.run                          # 全量评测
  python -m eval.run --verbose                 # 详细输出
  python -m eval.run --skip-rag                # 仅测检索，不调 LLM
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from pipeline.rag import RAGPipeline
from pipeline.retriever import HybridRetriever
from pipeline.retriever.intent import detect_intent

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
GROUND_TRUTH_PATH = ROOT / "ground_truth.json"


# ══════════════════════════════════════════════════════════════
# 1. 加载 Ground Truth
# ══════════════════════════════════════════════════════════════


def load_ground_truth() -> list[dict[str, Any]]:
    with open(GROUND_TRUTH_PATH, encoding="utf-8") as f:
        return json.load(f)


# ══════════════════════════════════════════════════════════════
# 2. 评估指标
# ══════════════════════════════════════════════════════════════


def eval_retrieval(
    retriever: HybridRetriever,
    test_cases: list[dict[str, Any]],
    top_k: int = 5,
) -> dict[str, Any]:
    """检索器评测：recall@5 + precision@5 + intent_accuracy。

    对每条测试用例，检查返回结果中是否包含 ground truth 中的
    关键词（answer_terms）或相关 commodity。
    """
    recall_list: list[float] = []
    precision_list: list[float] = []
    intent_correct = 0
    latencies: list[float] = []
    details: list[dict[str, Any]] = []

    for case in test_cases:
        q = case["question"]
        terms = [t.lower() for t in case.get("answer_terms", [])]
        expected_intent = case.get("intent", "")

        start = time.perf_counter()
        result = retriever.search(q, top_k=top_k)
        elapsed_ms = result.get("latency_ms", (time.perf_counter() - start) * 1000)
        latencies.append(elapsed_ms)

        retrieved = result["results"]
        intent = result.get("intent", "")
        if intent == expected_intent:
            intent_correct += 1

        # recall@5: 返回结果中至少有一条包含任一 answer_term
        hits = 0
        for r in retrieved:
            text = (r.get("text", "") + " " + json.dumps(r.get("metadata", {}))).lower()
            if any(t in text for t in terms):
                hits += 1

        p = hits / max(len(retrieved), 1)
        r_val = 1.0 if hits > 0 else 0.0

        precision_list.append(p)
        recall_list.append(r_val)

        details.append({
            "id": case["id"],
            "question": q,
            "intent": intent,
            "expected_intent": expected_intent,
            "recall": r_val,
            "precision": p,
            "hits": hits,
            "n_results": len(retrieved),
        })

    n = len(test_cases)
    latencies.sort()
    p50 = latencies[len(latencies) // 2] if latencies else 0

    return {
        "test_size": n,
        f"recall@{top_k}": round(sum(recall_list) / n, 4),
        f"precision@{top_k}": round(sum(precision_list) / n, 4),
        "intent_accuracy": round(intent_correct / n, 4),
        "latency_p50_ms": round(p50, 1),
        "summary": f"{sum(recall_list):.0f}/{n} queries had a relevant result in top-{top_k}",
        "details": details,
    }


def eval_rag(
    rag: RAGPipeline,
    test_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    """RAG 生成评测：answer_faithfulness。

    检查生成的回答中是否包含 ground truth 中的 answer_terms。
    """
    faithful = 0
    total_terms = 0
    details: list[dict[str, Any]] = []

    for case in test_cases:
        q = case["question"]
        terms = [t.lower() for t in case.get("answer_terms", [])]

        result = rag.query(q, top_k=3)
        answer = (result.get("answer") or "").lower()

        matched = sum(1 for t in terms if t in answer)
        total_terms += len(terms)

        if matched >= max(1, len(terms) // 2):
            faithful += 1

        details.append({
            "id": case["id"],
            "question": q,
            "terms_matched": f"{matched}/{len(terms)}",
            "faithful": matched >= max(1, len(terms) // 2),
        })

    n = len(test_cases)
    return {
        "test_size": n,
        "answer_faithfulness": round(faithful / n, 4),
        "summary": f"{faithful}/{n} answers contain >=50% of ground-truth terms",
        "details": details,
    }


# ══════════════════════════════════════════════════════════════
# 3. 主入口
# ══════════════════════════════════════════════════════════════


def print_report(retrieval: dict, rag_result: dict | None = None) -> None:
    sep = "=" * 50
    print(f"\n{sep}")
    print("  OreMind 评测报告")
    print(sep)
    print(f"  测试集:  {retrieval['test_size']} 条")
    print(f"  recall@5:       {retrieval['recall@5']:.2%}")
    print(f"  precision@5:    {retrieval['precision@5']:.2%}")
    print(f"  intent_acc:     {retrieval['intent_accuracy']:.2%}")
    print(f"  latency_p50:    {retrieval['latency_p50_ms']:.0f} ms")
    print(f"  摘要:            {retrieval['summary']}")

    if rag_result:
        print(f"\n  answer_faithfulness:  {rag_result['answer_faithfulness']:.2%}")
        print(f"  RAG 摘要:             {rag_result['summary']}")

    print(sep)

    # 打印每条细节
    print(f"\n{'ID':>3} {'Intent':>8} {'R/P':>5} {'细节':<50}")
    print("-" * 70)
    for d in retrieval["details"]:
        intent_ok = "[OK]" if d["intent"] == d["expected_intent"] else "[!!]"
        r_text = f"{d['recall']:.0f}"
        p_text = f"{d['precision']:.0f}"
        print(f" {d['id']:>2}  {d['intent']:>8} {r_text}/{p_text}  {intent_ok} {d['question'][:45]}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OreMind 检索器评测")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")
    parser.add_argument("--skip-rag", action="store_true", help="跳过 RAG 生成评测")
    parser.add_argument("--top-k", type=int, default=5, help="Top-K (默认 5)")
    return parser.parse_args(argv)


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(message)s")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)

    test_cases = load_ground_truth()
    logger.info("加载 %d 条 ground truth", len(test_cases))
    logger.info("初始化检索器 ...")

    retriever = HybridRetriever()

    # 检索评测
    logger.info("运行检索评测 ...")
    retrieval_result = eval_retrieval(retriever, test_cases, top_k=args.top_k)

    # RAG 评测
    rag_result = None
    if not args.skip_rag:
        logger.info("运行 RAG 评测 (可能较慢) ...")
        rag = RAGPipeline(retriever=retriever)
        rag_result = eval_rag(rag, test_cases)
        rag.close()
    else:
        logger.info("跳过 RAG 评测")

    print_report(retrieval_result, rag_result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
