# OreMind — 矿业情报 RAG 平台

**两个生命周期：**

> **Pipeline（建库）** → 采集 → 清洗 → 去重 → 切块 → 嵌入 → 入库。每天/每周跑一次。
>
> **Runtime（查询）** → 用户提问 → 检索 → 重排序 → LLM 生成。每次请求实时执行。

---

## 生命周期一：Pipeline（建库）

```
  ┌──────────────┐
  │  数据源       │
  │  RSS / 网页   │
  │  Yahoo Fin   │
  └──────┬───────┘
         │
         ▼
  ┌──────────────┐      pipeline/collectors/
  │  采集 collect│      news.py / policy.py / price.py
  └──────┬───────┘
         │
         ▼
  ┌──────────────┐      pipeline/cleaners/
  │  清洗 clean  │      extract_content() / is_policy_relevant()
  └──────┬───────┘
         │
         ▼
  ┌──────────────┐      pipeline/dedup/
  │  去重 dedup  │      URLDeduplicator / PriceDeduplicator
  └──────┬───────┘
         │
         ▼
  ┌──────────────┐      pipeline/chunkers/
  │  切块 chunk  │      RecursiveCharacterTextSplitter
  └──────┬───────┘
         │
         ▼
  ┌──────────────┐      pipeline/embedding/
  │  嵌入 embed  │      BAAI/bge-small-en-v1.5 (384d)
  └──────┬───────┘
         │
         ▼
  ┌──────────────┐      pipeline/vectordb/
  │  入库 store  │      ChromaDB (data/vectordb/)
  └──────────────┘

  调度入口:  pipeline/pipeline.py
             collect → clean → dedup → chunk → embed → store
```

## 生命周期二：Runtime（查询）

```
  ┌──────────────┐
  │  用户提问     │
  │  "铜价走势"   │
  └──────┬───────┘
         │
         ▼
  ┌──────────────────┐      retriever/query_transform.py
  │  查询扩展         │      "copper price" / "铜 价格" ...
  └──────┬───────────┘
         │
         ▼
  ┌──────────────────┐      retriever/intent.py
  │  意图路由 + 元过滤 │      price → filter: {commodity, exchange}
  └──────┬───────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
  ┌──────┐  ┌──────┐      retriever/bm25.py         ← BM25 (关键词)
  │ BM25 │  │Vector│      retriever/__init__.py     ← Vector (语义)
  └──┬───┘  └──┬───┘
    │         │
    └────┬────┘
         │ RRF 融合
         ▼
  ┌──────────┐            retriever/reranker.py
  │Reranker  │            Cross-Encoder (英文查询)
  └────┬─────┘
         │
         ▼
  ┌──────────┐            rag/__init__.py
  │ DeepSeek │            DeepSeek Chat API
  └────┬─────┘
         │
         ▼
  ┌──────────┐
  │ 答案+来源 │
  │ 置信度气泡 │
  └──────────┘

  服务入口:  serve/main.py → router.py → service.py
              → retriever/ + rag/
```

## 快速开始

```bash
# ── 建库 ──
python -m pipeline.pipeline                        # 全量: 采集→入库
python -m pipeline.pipeline --skip-embed            # 仅采集→切块(调试)

# ── 查询 ──
python -m serve.main                                # 启动 API :8000
curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"lme copper price"}'

# ── 评测 ──
python -m eval.run --skip-rag
```

## 目录结构

```
OreMind/
│
├── pipeline/                 ← 建库 (Build Time)
│   ├── pipeline.py           ← 统一调度 (collect→clean→dedup→chunk→embed→store)
│   ├── config/settings.py    ← 单一配置源
│   ├── collectors/           ← 采集: news(200) / policy(200) / price(200)
│   ├── cleaners/             ← 清洗: HTML / 正文 / 关键词过滤
│   ├── dedup/                ← 去重: URL / 复合键
│   ├── chunkers/             ← 切块: RecursiveCharacterTextSplitter
│   ├── embedding/            ← 嵌入: BGE (384维)
│   └── vectordb/             ← 向量库: ChromaDB
│
├── serve/                    ← 查询 (Runtime)
│   ├── main.py               ← FastAPI 应用
│   ├── router.py             ← API 路由
│   └── service.py            ← RAGService 业务逻辑
│
├── retriever/                ← 检索 (Runtime)
│   ├── __init__.py           ← HybridRetriever 主入口
│   ├── bm25.py               ← BM25 稀疏索引
│   ├── intent.py             ← 意图路由 + 元数据过滤
│   ├── query_transform.py    ← 查询扩展
│   └── reranker.py           ← Cross-Encoder 重排序
│
├── rag/                      ← 生成 (Runtime)
│   └── __init__.py           ← RAGPipeline → DeepSeek
│
├── eval/                     ← 评测 (20 GT, recall@5 100%)
├── frontend/                 ← Vue3 前端
├── api/                      ← (旧入口, 向后兼容)
├── data/
│   ├── raw/                  ← 采集数据 (JSON)
│   ├── processed/            ← 清洗后数据
│   ├── vectordb/             ← ChromaDB 持久化
│   └── logs/                 ← 管道监控日志
├── README.md
└── DATA_NOTES.md             ← Schema / 字段 / 去重策略
```

## 配置

所有配置集中在 `pipeline/config/settings.py`:

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | 嵌入模型 |
| `CHUNK_SIZE` | 700 | 文本切块大小 (token) |
| `TOP_K_FINAL` | 3 | 检索最终返回条数 |
| `DEEPSEEK_MODEL` | `deepseek-chat` | 生成模型 |
| `COLLECT_DAYS` | 30 | 采集时间窗口 |

### 嵌入模型选型说明

采用 **BAAI/bge-small-en-v1.5** (384维)，原因:
- 当前数据源以英文为主 (global mining news + LME/CME prices)
- bge-small 在 MTEB 英文榜单上与 large 模型差距 < 2%，但速度快 5 倍
- 若后续扩展中文数据 (中国部委政策、SMM 价格)，可切换为 `bge-m3` (支持 100+ 语言)

环境变量: `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `OREMIND_USER_AGENT`

## 评测结果

```bash
python -m eval.run --skip-rag

  recall@5:       100%   ← 20/20 问题有相关结果
  precision@5:     90%   ← 检索精准度
  intent_acc:      95%   ← 意图识别正确率
  faithfulness:    TBD   ← 需运行完整 RAG 评测 (含 LLM 调用)
  latency_p50:    175ms  ← 检索延迟中位数
```

## 数据源

| 源 | 类型 | 30天产量 | 采集方式 | 说明 |
|-----|------|---------|---------|------|
| global mining news | news | 200 条 | RSS (17 源) | mining-journal / northern-miner 等 |
| 中国稀土 + global policy | policy | 200 条 | regcc + RSS | 栏目分页 + 17 RSS 源 |
| LME / CME / ETF 价格 | price | 200 条 | **Yahoo Finance** | 见下方说明 |

### 价格数据源说明

原需求涉及 **LME** (伦敦金属交易所)、**SHFE** (上海期货交易所)、**上海钢联** 等数据源。
实际调研发现:
- LME 官网 → 403 封锁 (IP 限制)
- SHFE → 全 JS 渲染，无公开 REST API
- Mysteel → 数据接口需付费企业账号

考虑到开发周期与数据可获取性，本项目采用 **Yahoo Finance** (`yfinance`) 提供的公开行情接口。
覆盖 11 个品种 × ~23 交易日 = ~253 条原始收盘价，包含 LME/CME/SGX/NYSE 等交易所数据，
满足价格趋势分析的核需需求。

## API 端点

| 端点 | 说明 |
|------|------|
| `GET /health` | 健康检查 |
| `GET /metrics` | 监控指标: 向量库规模 / 模型信息 |
| `POST /query` | RAG 检索 + DeepSeek 生成 |
| `POST /query/stream` | 流式 SSE (打字机) |
| `POST /search` | 仅检索 (不生成) |

## 检索流程 (Runtime)

```
  Query
    │
    ├─ Intent Router      → price / policy / news
    ├─ Metadata Filter    → {"commodity": "Copper", "exchange": "LME"}
    │
    ├─ BM25 (关键词)      → 精确匹配
    └─ Vector (语义)      → BGE 384维 余弦相似度
           │
      RRF Fusion (k=60)
           │
    Cross-Encoder (英文查询)
           │
      Top-3  → LLM 生成
```

## Future Work

| 方向 | 说明 |
|------|------|
| **Milvus** | 数据量 > 100 万向量时从 ChromaDB 迁移至 Milvus 分布式集群 |
| **Graph RAG** | 构建知识图谱 (矿种→公司→政策)，支持多跳推理查询 |
| **增量更新** | 当前全量重建，改进为增量 append + 过期淘汰 |
| **定时调度** | 接入 APScheduler / Airflow，每日自动采集 + 入库 |
| **Docker Compose** | API + ChromaDB + Frontend 三容器编排 |
| **CI/CD** | GitHub Actions 自动跑采集 → 评测 → 部署 |
