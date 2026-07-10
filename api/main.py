"""OreMind RAG API — FastAPI 服务。

启动:
  uvicorn api.main:app --reload --port 8000

或直接运行:
  python -m api.main
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import asyncio
import json
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from pipeline.rag import RAGPipeline

logger = logging.getLogger(__name__)


# ── 全局 RAG 实例 ────────────────────────────────────────────

rag: RAGPipeline | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global rag
    logger.info("初始化 RAG 管道 ...")
    rag = RAGPipeline()
    logger.info("RAG 管道就绪")
    yield
    if rag:
        rag.close()
        logger.info("RAG 管道已关闭")


app = FastAPI(
    title="OreMind RAG API",
    description="矿业情报 RAG 检索增强生成 API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 数据模型 ──────────────────────────────────────────────────


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000, description="用户问题")
    top_k: int = Field(default=3, ge=1, le=10, description="参考上下文条数")


class SourceItem(BaseModel):
    title: str = ""
    url: str = ""
    source: str = ""
    score: float = 0.0
    scores: dict | None = None


class LatencyInfo(BaseModel):
    retrieval: float = 0.0
    llm: float = 0.0
    total: float = 0.0


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceItem]
    intent: str = ""
    latency_ms: LatencyInfo


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"


# ── API 端点 ──────────────────────────────────────────────────


@app.get("/health", response_model=HealthResponse)
async def health():
    """健康检查。"""
    return HealthResponse()


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest) -> QueryResponse:
    """RAG 检索增强生成。

    接受用户问题，执行混合检索 + DeepSeek 生成，返回答案和来源。
    """
    if rag is None:
        raise HTTPException(status_code=503, detail="RAG 管道未初始化")

    try:
        result = rag.query(req.question, top_k=req.top_k)
        return QueryResponse(
            answer=result["answer"],
            sources=[SourceItem(**s) for s in result["sources"]],
            intent=result.get("intent", ""),
            latency_ms=LatencyInfo(**result["latency_ms"]),
        )
    except Exception as e:
        logger.exception("RAG 查询失败: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/query/stream")
async def query_stream(req: QueryRequest):
    """流式 RAG — SSE 逐 token 输出。"""
    if rag is None:
        raise HTTPException(status_code=503, detail="RAG 管道未初始化")

    async def event_generator():
        try:
            result = rag.query(req.question, top_k=req.top_k)
            answer = result["answer"]
            # 逐 token 推送
            for i in range(0, len(answer), 4):
                token = answer[i : i + 4]
                yield f"data: {json.dumps({'token': token})}\n\n"
                await asyncio.sleep(0.02)  # 模拟打字速度
            # 结束 + 元数据
            yield f"data: {json.dumps({'done': True, 'sources': result['sources'], 'intent': result.get('intent'), 'latency_ms': result['latency_ms']})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'token': f'\\n\\n> 错误: {e}'})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/search", response_model=dict[str, Any])
async def search_only(req: QueryRequest) -> dict[str, Any]:
    """仅检索（不生成），返回原始检索结果。"""
    if rag is None:
        raise HTTPException(status_code=503, detail="RAG 管道未初始化")

    try:
        result = rag.retriever.search(req.question, top_k=req.top_k)
        return result
    except Exception as e:
        logger.exception("检索失败: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── 直接运行 ──────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)
