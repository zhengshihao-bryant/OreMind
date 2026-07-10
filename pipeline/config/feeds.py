"""RSS Feed 源配置 — 与采集代码分离，方便增删改。

添加新源的步骤:
  1. 在下方字典中添加 URL
  2. 运行 python -c "import requests; r=requests.get('URL', headers={'User-Agent':'Mozilla/5.0'}); print(r.status_code, 'rss' in r.text.lower())"
  3. 确认返回 200 且内容包含 RSS/XML 标记
  4. 重新运行采集器即可生效
"""

# ── 活跃源（每日采集） ─────────────────────────────────────

ACTIVE_FEEDS: list[str] = [
    # Mining.com 主频道（目前 CloudFront 限流中，恢复后自动生效）
    "https://www.mining.com/feed/",
    # Mining.com 矿种频道
    "https://www.mining.com/tag/critical-minerals/feed/",
    # Mining Journal — 全球矿业新闻
    "https://www.mining-journal.com/feed/rss",
    # The Northern Miner — 专注矿业投资与勘探
    "https://www.northernminer.com/feed/",
    # The Assay — 投资者视角的矿业报道
    "https://www.theassay.com/feed/",
    # Investing News Network — 资源投资全覆盖
    "https://investingnews.com/feed/",
    # Mining Technology — 侧重矿业技术与项目（/feed/ 403，/rss/ 可用）
    "https://www.mining-technology.com/rss/",
    # International Mining — 国际矿业新闻
    "https://im-mining.com/rss/",
    # Mining Mexico — 墨西哥矿业新闻
    "https://miningmexico.com/feed/",
    # Resource World — 资源投资新闻
    "https://resourceworld.com/feed/",
    # SRSrocco Report — 矿业与能源分析
    "https://www.srsroccoreport.com/feed/",
    # Junior Mining Network — 矿业投资新闻
    "https://feeds.feedburner.com/juniorminingnetwork",
    # MiningIR — 矿业投资者关系
    "https://www.miningir.com/feed/",
    # Small Caps — 澳大利亚小盘矿业股
    "https://smallcaps.com.au/feed/",
    # Australian Mining — 澳大利亚矿业新闻
    "https://www.australianmining.com.au/feed/",
]

# ── 待验证源（确认返回 XML 后再移入 ACTIVE_FEEDS） ───────

STANDBY_FEEDS: dict[str, str] = {
    # Kitco Metals — 贵金属权威媒体（待找到有效 RSS 地址）
    "kitco_metals": "https://www.kitco.com/rss/",
    # Mining Technology — 侧重矿业技术与项目
    "mining_technology": "https://www.mining-technology.com/feed/",
    # Australian Mining — 澳大利亚矿业新闻
    "australian_mining": "https://www.australianmining.com.au/feed/",
    # Mining Global — 待验证（返回 404，可能需要换路径）
    "mining_global": "https://www.miningglobal.com/feed/",
}

# ── 采集参数 ───────────────────────────────────────────────

# 单次采集最多处理文章数
MAX_ARTICLES: int = 300

# 只保留近 N 天的文章（None = 不过滤）
DAYS_FILTER: int = 30

# 正文最少字符数（低于此值标记为 "summary"）
CONTENT_MIN_LENGTH: int = 200
