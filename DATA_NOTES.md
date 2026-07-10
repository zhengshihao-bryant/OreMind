# OreMind 数据管道文档

## 1. 数据源概览

| 源 | 采集器 | 集合 | 30天产量 | 更新频率 |
|------|--------|------|---------|---------|
| 矿业新闻 | `pipeline/collectors/news.py` | `news` | ~200 条 | 每次采集 |
| 关键矿产政策 | `pipeline/collectors/policy.py` | `policy` | ~200 条 | 每次采集 |
| 金属价格 | `pipeline/collectors/price.py` | `price` | ~200 条 | 每次采集 |

---

## 2. 数据 Schema

### 2.1 新闻 (news)

```json
{
  "title":        "str — 文章标题",
  "url":          "str — 原文链接 (主键)",
  "published":    "str — ISO8601 发布时间",
  "summary":      "str — RSS 摘要",
  "content":      "str — 全文 (最大 100KB)",
  "author":       "str | null — 作者",
  "source":       "str — 来源标识 (如 mining_ir)",
  "source_feed":  "str — RSS feed URL",
  "category":     "str — 固定 'news'",
  "content_length": "int — 正文字节数",
  "content_status": "str — 'full' | 'summary'"
}
```

- **主键**: `url`
- **去重策略**: `seen_urls` 集合，同 URL 只保留第一条

### 2.2 政策 (policy)

```json
{
  "title":      "str — 文章标题",
  "url":        "str — 原文链接 (主键)",
  "content":    "str — 正文全文",
  "published":  "str — ISO8601 发布日期",
  "source":     "str — 来源标识 (regcc / mining.com / im-mining.com 等)",
  "category":   "str — 固定 'policy'",
  "list_date":  "datetime — 列表页解析日期 (仅 regcc)"
}
```

- **主键**: `url`
- **去重策略**: `seen_urls` 集合；regcc 源在列表页提前用日期预筛
- **RSS 源**: 17 个 RSS feed，含 `?paged=2/3` 翻页
- **正文过滤**: 已去除 `content_policy_filter`（标题 `is_policy` 已足够），mining_rss 用标题词过滤

### 2.3 价格 (price)

```json
{
  "title":    "str — 格式 '{Commodity} Price'",
  "content":  "str — 格式 '{Commodity} price is {price} {currency} per {unit} on {date} at {exchange}'",
  "source":   "str — 固定 'price'",
  "category": "str — 固定 'price'",
  "metadata": {
    "commodity": "str — 矿种 (Copper / Iron Ore / Lithium ETF 等)",
    "price":     "float — 收盘价",
    "currency":  "str — USD / CNY",
    "unit":      "str — ton / oz / lb / share",
    "exchange":  "str — LME / CME / COMEX / NYSE / SGX",
    "date":      "str — YYYY-MM-DD"
  }
}
```

- **主键**: `commodity + exchange + date`
- **去重策略**: `seen` 集合 `{commodity}_{exchange}_{date}`
- **数据源**: Yahoo Finance (`yfinance`)，11 个品种 × ~23 交易日 = ~253 条原始

---

## 3. 管道流程

```
采集 (collectors/) → 清洗 (cleaners/) → 去重 (dedup/) → 切块 (embedding/index.py) → 嵌入 (embedding/) → 入库 (vectordb/)
```

### 3.1 采集
- `python -m pipeline.collectors.run -p news policy price` — 全量采集
- 各采集器独立运行，串行执行

### 3.2 清洗
- HTML 标签剥离 (`pipeline/collectors/base.py:parse_content`)
- 短段落过滤 (< 10 字符跳过)
- script/style/nav/footer 等噪声标签移除

### 3.3 去重
- **URL 去重**: `seen_urls: set[str]`，按 `url` 判重
- **价格去重**: `{commodity}_{exchange}_{date}` 复合键
- **RSS 内去重**: 同一 feed 内按 title 判重

### 3.4 切块 + 入库
```bash
python -m pipeline.embedding.index --rebuild
```

| 源 | 切块策略 | 参数 |
|----|---------|------|
| news | RecursiveCharacterTextSplitter | chunk_size=700, overlap=120 |
| policy | 同上 | 同上 |
| price | 不切块，1记录=1向量 | — |

### 3.5 检索
```
Query → 查询扩展 → 意图路由 → BM25 + Vector → RRF → (可选 Cross-Encoder) → Top-3
```

---

## 4. 向量库

| 项 | 值 |
|----|-----|
| 引擎 | ChromaDB (本地持久化) |
| 路径 | `data/vectordb/` |
| 嵌入模型 | `BAAI/bge-small-en-v1.5` (384 维) |
| 距离度量 | 余弦距离 (cosine) |
| 集合 | news (1518), policy (796), price (200) |

---

## 5. API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/query` | POST | RAG 检索生成 (JSON) |
| `/query/stream` | POST | 流式 SSE 逐 token |
| `/search` | POST | 仅检索不生成 |
| `/health` | GET | 健康检查 |

**请求体:**
```json
{ "question": "lme copper price", "top_k": 3 }
```

**响应:**
```json
{
  "answer": "…",
  "sources": [{"title": "", "url": "", "source": "price", "score": 0.99, "scores": {"confidence": 1.0}}],
  "intent": "price",
  "latency_ms": {"retrieval": 500, "llm": 2000, "total": 2500}
}
```

---

## 6. 检索策略

| 策略 | 实现 | 说明 |
|------|------|------|
| 查询扩展 | `query_transform.py` | 中英同义词互补，commodity/exchange/region 别名 |
| 意图路由 | `intent.py` | price(policy(news(general 三级) |
| 元数据过滤 | `build_filter()` | price 注入 commodity/exchange/category |
| BM25 稀疏检索 | `bm25.py` | 词频匹配，中英混合分词 |
| 向量密集检索 | `embedding/__init__.py` | BGE 384 维 |
| RRF 融合 | `_rrf_fuse()` | k=60 倒数秩融合 |
| Cross-Encoder 重排 | `reranker.py` | 英文查询用 ms-marco-MiniLM (中文跳过) |
| 置信度 | BM25/10 或 1-向量距离 | 绝对值，永不归一化 |
