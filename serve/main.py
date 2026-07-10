"""OreMind API — FastAPI 应用入口。

启动:
  python -m serve.main
  uvicorn serve.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pipeline.config import settings
from serve.router import router

logger = logging.getLogger(__name__)

# 确保项目根目录在 Python 路径中
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("OreMind API 启动")
    yield
    logger.info("OreMind API 已关闭")


app = FastAPI(
    title="OreMind RAG API",
    description="矿业情报 RAG 检索增强生成 API — news / policy / price 三源聚合",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="")

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )
    uvicorn.run("serve.main:app", host=settings.API_HOST, port=settings.API_PORT, reload=False)
