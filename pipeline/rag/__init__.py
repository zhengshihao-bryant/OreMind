"""RAG 生成管道 — 检索增强生成 (Retrieval-Augmented Generation)。

流程:
  1. HybridRetriever.search(query) → Top-3 上下文
  2. 组装 Prompt (含上下文 + 用户问题)
  3. 调用 DeepSeek API 生成回答
  4. 返回 answer + sources
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from pipeline.retriever import HybridRetriever

logger = logging.getLogger(__name__)

# ── DeepSeek 配置 ────────────────────────────────────────────

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")  # 必须通过环境变量设置
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

SYSTEM_PROMPT = """你是一个专业的矿业情报分析助手。你的知识库包含以下三类数据：

1. 新闻 (news): 全球矿业新闻，涉及公司动态、项目进展、兼收并购等。
2. 政策 (policy): 各国矿业政策、法规、战略、供应链安全等内容。
3. 价格 (price): 金属期货/现货的历史每日收盘价，含 commodity、exchange、date。

回答规则：
- 严格基于提供的上下文回答，不要编造数据。
- 如果上下文不足以回答，诚实地说"知识库中未找到相关信息"。
- 价格查询务必给出具体数字、单位和日期。
- 涉及多源信息时，明确标注信息来源（新闻/政策/价格）。
- 使用中文回答，保留关键英文术语。

=== 上下文 ===

{context}

=== 用户问题 ===

{query}

请基于以上上下文回答用户问题。如果上下文与问题无关，请忽略上下文并告知用户。"""


class RAGPipeline:
    """RAG 检索增强生成管道。"""

    def __init__(self, retriever: HybridRetriever | None = None) -> None:
        self.retriever = retriever or HybridRetriever()
        self._http_client: httpx.Client | None = None

    @property
    def http_client(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = httpx.Client(
                base_url=DEEPSEEK_BASE_URL,
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                timeout=60.0,
            )
        return self._http_client

    # ── 上下文构建 ──────────────────────────────────────────

    @staticmethod
    def _format_context(results: list[dict[str, Any]]) -> str:
        """将检索结果格式化为上下文文本。"""
        sections: list[str] = []
        for i, r in enumerate(results, 1):
            source = r.get("source", "unknown")
            meta = r.get("metadata", {})
            text = r.get("text", "")[:800]

            if source == "price":
                prefix = (
                    f"[价格] {meta.get('commodity', '')} / "
                    f"{meta.get('exchange', '')} / {meta.get('date', '')}"
                )
            elif source == "policy":
                prefix = f"[政策] {meta.get('title', '')} ({meta.get('published', '')})"
            else:
                prefix = f"[新闻] {meta.get('title', '')} ({meta.get('published', '')})"

            sections.append(f"--- 参考 {i}: {prefix} ---\n{text}")

        return "\n\n".join(sections)

    # ── DeepSeek API ────────────────────────────────────────

    def _call_llm(self, prompt: str) -> str:
        """调用 DeepSeek Chat API (OpenAI 兼容接口)。"""
        payload = {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 2048,
            "stream": False,
        }

        resp = self.http_client.post("/v1/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            logger.error("DeepSeek 响应异常: %s", data)
            return f"模型响应解析失败: {e}"

    # ── 完整 RAG 查询 ───────────────────────────────────────

    def query(self, question: str, top_k: int = 3) -> dict[str, Any]:
        """执行 RAG 查询。

        :param question: 用户问题
        :param top_k:    参考上下文条数
        :returns:        {answer, sources, intent, latency_ms}
        """
        start = time.perf_counter()

        retrieval = self.retriever.search(question, top_k=top_k)
        retrieval_ms = retrieval.get("latency_ms", 0)

        context = self._format_context(retrieval["results"])
        prompt = SYSTEM_PROMPT.format(context=context, query=question)

        llm_start = time.perf_counter()
        answer = self._call_llm(prompt)
        llm_ms = round((time.perf_counter() - llm_start) * 1000, 1)
        total_ms = round((time.perf_counter() - start) * 1000, 1)

        sources = []
        for r in retrieval["results"]:
            meta = r.get("metadata", {})
            src = r.get("source", "")
            if src == "price":
                title = f"{meta.get('commodity', '')} {meta.get('date', '')} ({meta.get('exchange', '')})"
                url = ""
            else:
                title = meta.get("title", "")
                url = meta.get("url", "")
            sources.append({
                "title": title,
                "url": url,
                "source": src,
                "score": r.get("scores", {}).get("rerank", 0),
                "scores": r.get("scores", {}),
            })

        logger.info(
            "RAG: %s | retrieval=%sms llm=%sms total=%sms",
            question[:40], retrieval_ms, llm_ms, total_ms,
        )

        return {
            "answer": answer,
            "sources": sources,
            "intent": retrieval.get("intent"),
            "latency_ms": {
                "retrieval": retrieval_ms,
                "llm": llm_ms,
                "total": total_ms,
            },
        }

    def close(self) -> None:
        if self._http_client:
            self._http_client.close()
            self._http_client = None
