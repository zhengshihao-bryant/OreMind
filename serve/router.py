"""Router — FastAPI 路由定义，不包含业务逻辑。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from pipeline.config import settings

router = APIRouter()


# ── 数据模型 ──────────────────────────────────────────────────


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000, description="用户问题")
    top_k: int = Field(default=settings.TOP_K_FINAL, ge=1, le=10)


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


# ── 端点 ──────────────────────────────────────────────────────


def _init_service():
    """懒加载 service（在 router 注册后由 main 注入）。"""
    from serve.service import RAGService
    return RAGService()


_service: RAGService | None = None


def get_service() -> RAGService:
    global _service
    if _service is None:
        _service = _init_service()
    return _service


@router.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse()


@router.get("/metrics")
async def metrics():
    """监控指标: 向量库状态 + 查询计数 + 延迟。"""
    from pipeline.config import settings
    from pipeline.vectordb import VectorStore
    import time

    store = VectorStore()
    collections = {}
    for name in ["news", "policy", "price"]:
        count = store.count(name)
        collections[name] = {"vector_count": count}

    return {
        "status": "ok",
        "version": "1.0.0",
        "embedding": {
            "model": settings.EMBED_MODEL,
            "dimension": settings.EMBED_DIM,
        },
        "collections": collections,
        "total_vectors": sum(c["vector_count"] for c in collections.values()),
        "timestamp": time.time(),
    }


@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    svc = get_service()
    try:
        result = svc.query(req.question, top_k=req.top_k)
        return QueryResponse(
            answer=result["answer"],
            sources=[SourceItem(**s) for s in result["sources"]],
            intent=result.get("intent", ""),
            latency_ms=LatencyInfo(**result["latency_ms"]),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/query/stream")
async def query_stream(req: QueryRequest):
    """流式接口，SSE 逐 token 输出。"""
    import asyncio
    import json
    from fastapi.responses import StreamingResponse

    svc = get_service()

    async def event_generator():
        try:
            result = svc.query(req.question, top_k=req.top_k)
            answer = result["answer"]
            for i in range(0, len(answer), 4):
                token = answer[i: i + 4]
                yield f"data: {json.dumps({'token': token})}\n\n"
                await asyncio.sleep(0.02)
            yield f"data: {json.dumps({'done': True, 'sources': result['sources'], 'intent': result.get('intent'), 'latency_ms': result['latency_ms']})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'token': f'\\n\\n> 错误: {e}'})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/search", response_model=dict[str, Any])
async def search_only(req: QueryRequest):
    svc = get_service()
    try:
        return svc.search(req.question, top_k=req.top_k)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
