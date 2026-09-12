# Stock Dashboard — AI 驱动的 A 股价值投资选股看板

全自动 A 股价值投资筛选系统。每日收盘后跑完量化筛选（全市场 ~5500 只），AI 分析每日自动运行。最终目标：让 AI 接管投资决策。

## 文档导航

| 文档 | 内容 |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | 架构真相源：结构图、数据流、模块边界、数据源注册表、防回归门禁 |
| [`docs/roadmap.md`](docs/roadmap.md) | 开发总路线 M1–M4d |
| [`docs/paper-trading.md`](docs/paper-trading.md) | 虚拟盘 / 量化接入规划（自研 + QLib + 券商仿真） |
| [`docs/iteration-log.md`](docs/iteration-log.md) | 迭代进程账（Hermes agent 上下文源） |
| [`docs/handoff.md`](docs/handoff.md) | 班次交接速览 |

## 快速开始

```bash
git clone https://github.com/oldfunk/stock-dashboard.git
cd stock-dashboard
pip install akshare fastapi uvicorn jinja2 httpx python-dotenv schedule
python -m src.main serve
# 浏览器打开 http://localhost:9527/
```

### AI 配置（可选）

```env
# .env（免费模型无需任何 Key，自动从 OpenCode Zen 发现可用 free 模型）
STOCK_AI_MODEL=deepseek-v4-flash-free
STOCK_AI_API_KEY=your_api_key_here          # 仅非免费模型需要
STOCK_AI_API_BASE=https://opencode.ai/zen/v1
```

### 生产部署（systemd）

```bash
# /etc/systemd/system/stock-dashboard.service
# ExecStart=/home/pi/stock-dashboard/.venv/bin/python -m src.main serve
sudo systemctl enable --now stock-dashboard
```

内置调度器自动完成每日流水线 + AI 分析，无需额外 cron。`scripts/daily_cron.sh` 保留作为 Web 未运行时的 OS cron 兜底。

---

## 工作原理

### 核心理念

**不靠排名，只靠及格线。** 基于 AI Berkshire 7 条门规的硬性指标过滤，结合 LLM 结构化分析，每只股票必须通过全部门规才能进入候选池。

候选股展示 4 个结构化标签页（Analysis / Strategy / Risks / Trade），Trade 标签含 Signal + 置信度 + 买入区间 + 目标价 + 止损 + 止盈。

### 每日流水线

```
交易日 15:30（完整流程见 docs/architecture.md §2）:

  1. 腾讯行情 → 全A股行情 → 初筛预过滤
  2. AKShare 财务采集（yjbb + 深度补充，含兜底链）
  3. 历史财务采集 → financial_history → financial_summary（5y/10y 均值）
  4. 质量闸（ROE 覆盖 ≥50%）→ 7条门规筛选 → 评分 → 候选池（≤20 只）
  5. K 线拉取 → AI 分析（每日，失败可用 scripts/retry_ai.py 补跑）

每周六：AI 复盘 → ai_watchlist（5 只）+ ai_journal
```

### AI Berkshire 7 条门规

| # | 规则 | 指标 | 豁免 |
|:-:|:-----|:-----|:-----|
| 1 | 5 年均 ROE < 8% → 排除 | `roe_5y_avg` | 高增长(>20%)新上市公司 |
| 2 | 5 年累计 FCF ≤ 0 → 排除 | `fcf_5y_sum` | 小额负 FCF(<1 亿) |
| 3 | 利息覆盖 < 2x → 排除 | `intcov_5y_avg` | — |
| 4 | 毛利率 < 15% → 排除 | `gross_margin` | 高 ROE(≥20%)薄利模式 |
| 5 | OCF/股 ≤ 0 → 排除 | `ocf_per_share` | 高毛利(≥30%)+高增长(≥20%) |
| 6 | 5 年均净利率 < 5% → 排除 | `net_margin_5y_avg` | 高毛利率(≥30%)主动压利 |
| 7 | 5 年稀释 > 20% → 排除 | `share_dilution_5y` | — |

叠加筛选：PE(3~20) / PB(≤3.5) / 营收增长≥0 / 净利增长≥0 / 负债率<65% / 市值 30~50000 亿 / 排除 ST。

### AI 输出结构

```json
{
  "analysis": "投资人笔记风格完整分析",
  "moat_evaluation": [
    {"type": "转换成本/网络效应/无形资产/成本优势/有效规模", "score": "1-5", "trend": "稳定", "evidence": "..."}
  ],
  "management_score": {"capital_allocation": "1-10", "shareholder_friendliness": "1-10"},
  "intrinsic_value": {"conservative/base_case/optimistic": "估值(亿)", "margin_of_safety": "安全边际"},
  "reverse_thinking": "2-3个致死场景",
  "investment_strategy": "仓位和持有周期建议",
  "trade_strategy": {"signal": "BUY/HOLD/AVOID", "confidence": "高/中/低", "buy_zone": "...", "target_price": "...", "stop_loss": "...", "take_profit": "..."}
}
```

---

## 数据源架构

注册表摘要（完整表见 [`docs/architecture.md` §5](docs/architecture.md)）：

| 源 | 接口 | 用途 | 兜底 |
|---|---|---|---|
| **S1** 腾讯 | `qt.gtimg.cn` | 行情（PE/PB/市值/价格/涨跌幅） | 新浪 → AKShare 自算 |
| **S2** AKShare | `stock_yjbb_em` | 全 A 股批量财务（ROE/毛利率/OCF） | C2 东财直连 |
| **S3** AKShare | `stock_financial_abstract_ths` | 逐只深度补充（净利率/负债率） | onboard 重试 |
| **S4** AKShare | `stock_profit_sheet_by_report_em` | 利润表明细 | C2.5 东财直连（已挂，永久兜底） |
| **S5** AKShare | `stock_cash_flow_sheet_by_report_em` | 现金流量表 → FCF | C2.5 东财直连（已挂，永久兜底） |
| **S6** AKShare | `stock_zh_index_daily` / `fetch_kline_data` | K 线数据 | 东财 → 腾讯回退 |
| **S7** AKShare | `_poll_stocks` → `_realtime_cache` | 实时 tick（调度缓存） | `fetch_stock_realtime` 按需拉 |

---

## 脚本

| 命令 | 用途 |
|---|---|
| `python -m src.main serve` | 启动 Web + 内置调度器 |
| `bash scripts/stock-ai-slow-feed.sh` | 全量脚本（采集 + 筛选 + AI 分析） |
| `python3 scripts/retry_ai.py` | 补跑最新 run 中失败的 AI 分析 |
| `python3 scripts/retry_ai.py --all-failed` | 扫所有 run 里的失败记录 |
| `python3 scripts/retry_ai.py --dry-run` | 只列出待补跑清单，不实际调用 |

---

## 配置

详见 `config/config.yaml`：

- `screener.conditions.*` — 7 条门规阈值
- `schedule.daily_update_time` — 每日运行时间（默认 15:30）
- `web.port` — 看板端口（默认 9527）
- `ai.*` — LLM API 配置（默认 OpenCode Zen 免费模型池）

## 技术栈

- **数据采集** — httpx + AKShare（东方财富/同花顺底层）+ 腾讯行情 API
- **存储** — SQLite（WAL 模式）
- **AI 分析** — OpenAI 兼容 API + OpenCode Zen 免费模型池（自动故障轮换）
- **Web 看板** — FastAPI + Jinja2
- **调度** — 内置 `src/scheduler.py`（daemon 线程）+ systemd 常驻
- **虚拟盘（规划中）** — 自研 `src/paper/` 引擎，详见 [`docs/paper-trading.md`](docs/paper-trading.md)

## 与 AI Berkshire 的关系

量化筛选规则对齐 AI Berkshire 的 `quality-screen.md`，A 股适配：

1. **自动化** — AI Berkshire 需手动跑，本项目全自动
2. **本地数据仓库** — 逐日累积历史财务数据
3. **A 股适配** — 数据源换为 AKShare / 腾讯，规则兼容 A 股特性
4. **简化代理指标** — 利息覆盖/稀释率/FCF 使用 AKShare 可获取的字段
5. **结构化分析** — 每只股票输出完整的护城河/管理层/估值/策略 JSON

## 发展路线

- **M1 分析可信** — 估值可验算、结论三态化
- **M2 策略分化** — growth/dividend/turnaround 三策略独立候选池
- **M3 持有纪律** — 论点漂移跟踪 + 钉选股监控提醒
- **M4a 自研虚拟盘** — T+1 全建模，信号→委托→持仓→净值
- **M4b QLib 离线验证** — PC/云跑 TopK 回测，只回流结论
- **M4c 券商仿真** — QMT 模拟模式首选、PTrade 备选
- **M4d 实盘预备** — 仿真达标 + 明确下令才启动

详见 [`docs/roadmap.md`](docs/roadmap.md)。
