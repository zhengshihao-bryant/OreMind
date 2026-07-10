"""全局配置 — 单一事实来源。

所有模块从此文件读取配置，禁止硬编码 magic number。
"""

from __future__ import annotations

from pathlib import Path

# ── 路径 ──────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = PROJECT_ROOT / "data" / "raw"
VECTORDB_PATH = str(PROJECT_ROOT / "data" / "vectordb")

# ── 嵌入模型 ──────────────────────────────────────────────────

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_DIM = 384
EMBED_BATCH_SIZE = 64

# ── 切块 ──────────────────────────────────────────────────────

CHUNK_SIZE = 700
CHUNK_OVERLAP = 120

# ── 检索 ──────────────────────────────────────────────────────

TOP_K_HYBRID = 20       # BM25 / Vector 每路初筛条数
TOP_N_RERANK = 10       # Cross-Encoder 输入条数
TOP_K_FINAL = 3         # 最终返回 Top-N
TOP_K_SEARCH = 10       # BM25 / Vector 独立检索默认值

# ── DeepSeek ──────────────────────────────────────────────────

# 通过环境变量覆盖:
#   DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
import os  # noqa: E402

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")  # 必须通过环境变量设置
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
RAG_TOP_K = 3
RAG_TEMPERATURE = 0.3
RAG_MAX_TOKENS = 2048

# ── 采集 ──────────────────────────────────────────────────────

COLLECT_DAYS = 30
COLLECT_MAX_ITEMS = 200
COLLECT_PAGES = 20
CONTENT_MIN_SCORE = 1

# ── HTTP ──────────────────────────────────────────────────────

HTTP_TIMEOUT = 20
HTTP_RETRIES = 3
HTTP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# ── RRF 融合 ──────────────────────────────────────────────────

RRF_K = 60

# ── API ───────────────────────────────────────────────────────

API_PORT = 8000
API_HOST = "0.0.0.0"
