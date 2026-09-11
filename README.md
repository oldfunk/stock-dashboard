# Stock Dashboard — AI/量化驱动的 A 股价值投资选股看板

全自动 A 股价值投资筛选系统，每日收盘后跑完量化筛选（全市场），AI 分析每周五收盘后运行（长线逻辑，基本面周级稳定）。

## 项目初衷

做一个自己用的 AI 自动盯盘投资工具，最终目标是让 AI 接管投资决策。

项目立项很大，需要漫长的有序迭代。当前阶段利用已有的 AI Berkshire 项目作为核心思路和 AI 分析的指导理念，围绕这个算法搭一个好用的网页面板。网页面板的 UI 设计已基本定型，但要做到足够细致和直观还有距离。

当前系统能做详尽的分析（筛选 + 结构化 AI 评估 + 估值 + 策略），但还做不到 AI 接管操作——这是终极目标，不是现在。

## 核心理念

**不靠排名，只靠及格线。** 基于 AI Berkshire 7条门规的硬性指标过滤，结合 LLM 生成的结构化分析（护城河评估 / 管理层打分 / 内在价值估值 / 买卖策略），每只股票必须通过全部门规才能进入候选池。

每只候选股展示：
- 4个结构化标签页：Analysis / Strategy / Risks / Trade
- Trade 标签含 Signal(BUY/HOLD/AVOID) + 置信度 + 买入区间 + 目标价 + 止损 + 止盈
- 历史分析可追溯，每条记录以相同的结构化格式展示

## 运行方式

### 方式 A：纯独立运行（推荐，不需要 Hermes Agent）

```bash
# 1. 安装依赖
pip install akshare fastapi uvicorn jinja2 httpx python-dotenv schedule

# 2. 启动 Web 看板（内置调度器，每日 15:30 自动跑流水线）
python -m src.main serve

# 3. 可选：设置 OS 定时任务（crontab），作为兜底/无需 Web 服务时运行
# crontab -e 添加：
# 30 15 * * 1-5 /home/debian/stock-dashboard/scripts/daily_cron.sh >> /var/log/stock-dashboard-cron.log 2>&1
# 0 16 * * 5 /home/debian/stock-dashboard/scripts/ai_analysis_cron.sh >> /var/log/stock-dashboard-ai.log 2>&1
```

Web 看板启动后包含：
- **内置调度器**（`src/scheduler.py`）— 交易日 15:30 自动触发数据采集 + 量化筛选
- **大盘指数** — 每 30 分钟更新
- **实时行情** — 交易时段每 5 分钟刷新

### 方式 B：全量脚本（流水线 + AI 分析一起跑）

```bash
bash scripts/stock-ai-slow-feed.sh
# 等同于：
#   python3 scripts/run_pipeline.py     # 采集 → 筛选（会重新采集全量数据）
#   python3 scripts/run_ai_analysis.py --all  # AI 分析全量模式
```

### 方式 C：补跑失败的 AI 分析

每周五 AI 分析后，个别股票可能因模型限流/解析失败而漏分析。`scripts/retry_ai.py`
会自动定位失败股票并补跑，无需手改 run_id：

```bash
# 默认：补跑最新 run 中 ai_analysis 为空的股票
python3 scripts/retry_ai.py

# 指定某个 run 补跑
python3 scripts/retry_ai.py --run-id=20260715_153012

# 扫所有 run 里的失败记录
python3 scripts/retry_ai.py --all-failed

# 只列出待补跑股票，不实际调用（确认清单用）
python3 scripts/retry_ai.py --dry-run
```

### 部署：systemd 常驻（生产推荐）

项目在树莓派等小设备上推荐用 systemd 常驻运行，内置调度器会自动完成每日流水线
与周五 AI 分析，无需额外配置 OS cron。服务异常退出会自动重启：

```bash
# /etc/systemd/system/stock-dashboard.service
# ExecStart=/home/pi/stock-dashboard/.venv/bin/python -m src.main serve
sudo systemctl enable --now stock-dashboard
```

> `scripts/daily_cron.sh` / `ai_analysis_cron.sh` 仍保留，作为 Web 服务未运行时的
> OS cron 兜底（可选安装，非必需）。

## 快速启动

```bash
git clone https://github.com/oldfunk/stock-dashboard.git
cd stock-dashboard
pip install akshare fastapi uvicorn jinja2 httpx python-dotenv schedule
python -m src.main serve
```

然后浏览器打开 `http://localhost:9527/`。

### AI 分析配置（可选，默认无需配置）

提供 `.env` 文件（可选）：

```env
STOCK_AI_API_KEY=your_api_key_here    # 仅在使用非免费模型时需要
STOCK_AI_API_BASE=https://opencode.ai/zen/v1  # 可改为任意 OpenAI 兼容 API
STOCK_AI_MODEL=deepseek-v4-flash-free  # 免费模型，无需 API Key
```

**免费模型无需任何 API Key**，自动从 OpenCode Zen 发现可用 free 模型，支持故障轮换。

## 数据源架构

```
腾讯行情 (qt.gtimg.cn)
  └─ 核心行情: PE/PB/市值/价格/涨跌幅（主数据源，覆盖 A 股三大交易所+科创板）

AKShare（社区维护的中国金融数据工具箱）
  ├─ stock_yjbb_em（东方财富底层）→ 全A股批量财务：ROE/毛利率/OCF/EPS/增长率
  ├─ stock_financial_abstract_ths（同花顺底层）→ 逐只深度补充：净利率/负债率
  ├─ stock_profit_sheet_by_report_em → 利润表明细：利息费用/总股本
  └─ stock_cash_flow_sheet_by_report_em → 现金流量表：经营/投资现金流 → FCF
```

> **腾讯做行情主力**：唯一免费且稳定提供 A 股实时 PE/PB/市值/价格的公开接口，财务数据完全不用腾讯。**兜底策略**：腾讯失败时自动切换到新浪证券 + AKShare 财务自算。

> **AKShare 做财务主力**：社区维护，数据来源覆盖东方财富、同花顺等，长期数据稳定性优于手写直连。`stock_yjbb_em` 一次 HTTP 调用获取 ~5800 只 A 股最新财务数据，深度数据只在候选股上调用。

## AI Berkshire 7条门规

| # | 规则 | 指标 | 豁免条件 |
|:-:|:----|:----|:--------|
| 1 | 5年均ROE < 8% → 排除 | `roe_5y_avg` | 高增长(>20%)新上市公司 |
| 2 | 5年累计FCF ≤ 0 → 排除 | `fcf_5y_sum` (FCFF年报) | 小额负FCF(<1亿) |
| 3 | 利息覆盖 < 2x → 排除 | `intcov_5y_avg` | — |
| 4 | 毛利率 < 15% → 排除 | `gross_margin` | 高ROE(≥20%)薄利模式 |
| 5 | OCF/股 ≤ 0 → 排除 | `ocf_per_share` | 高毛利率(≥30%)+高增长(≥20%)投入期 |
| 6 | 5年均净利率 < 5% → 排除 | `net_margin_5y_avg` | 高毛利率(≥30%)主动压利 |
| 7 | 5年稀释 > 20% → 排除 | `share_dilution_5y` | — |

叠加筛选：PE(3~20) / PB(≤3.5) / 营收增长≥0 / 净利增长≥0 / 负债率<65% / 市值30~50000亿 / 排除ST。

## 流水线

```
交易日 15:30（收盘后自动触发）:
  1. 腾讯行情 → 全A股行情 → 初筛预过滤
  2. AKShare stock_yjbb_em → 当前财务（ROE/毛利率/OCF）
  3. AKShare 深度补充（净利率/负债率）
  4. AKShare 利润表+现金流表 → 历史财务采集 → financial_history
  5. 重建 5年/10年汇总 → financial_summary
  6. 7条门规筛选 → 评分 → 候选池（≤20只）

每周五（流水线完成后自动触发）:
  7. AI 分析 → 护城河 / 管理层 / 估值 / 逆向思考 / 买卖策略
  注：AI 分析在每日流水线成功后，仅周五触发；失败股票可用 scripts/retry_ai.py 补跑
```

## 本地数据仓库

系统在运行中自动积累历史财务数据：

- **financial_history** — 按季度/年度累积 AKShare 全量财务数据
- **financial_summary** — 从历史数据计算的 5年/10年衍生指标（ROE波动/ROE改善/FCF一致性/股本稀释等）
- **增量采集** — 已有数据的股票跳过，只拉新的
- **数据覆盖** — 随运行天数自动扩展到 5~10 年区间

数据每天积累，财务指标覆盖年限随时间自然增长，本地数据仓库越来越完整。

## AI 分析输出结构

每只候选股的 AI 分析包含完整的 JSON 结构化字段：

```json
{
  "analysis": "投资人笔记风格完整分析",
  "moat_evaluation": [
    {"type": "转换成本/网络效应/无形资产/成本优势/有效规模", "score": 1-5, "trend": "稳定", "evidence": "..."}
  ],
  "management_score": {"capital_allocation": "1-10", "shareholder_friendliness": "1-10"},
  "intrinsic_value": {"conservative/base_case/optimistic": "估值(亿)", "margin_of_safety": "安全边际"},
  "reverse_thinking": "2-3个致死场景",
  "investment_strategy": "仓位和持有周期建议",
  "trade_strategy": {"signal": "BUY/HOLD/AVOID", "confidence": "高/中/低", "buy_zone": "...", "target_price": "...", "stop_loss": "...", "take_profit": "..."},
  "mirror_counts": "转折词计数"
}
```

## 配置

详见 `config/config.yaml`。主要参数：

- `screener.conditions.*` — 7条门规的阈值（PE/PB/ROE/增长率/负债率等）
- `schedule.daily_update_time` — 每日自动运行时间（默认 15:30）
- `web.port` — 看板端口（默认 9527）
- `ai.*` — LLM API 配置（默认用 OpenCode Zen 免费模型）

## 技术栈

- **数据采集**: httpx + AKShare（东方财富/同花顺底层）+ 腾讯行情API
- **存储**: SQLite (WAL 模式)
- **AI 分析**: OpenAI 兼容 API + OpenCode Zen 免费模型池（自动故障轮换）
- **Web 看板**: FastAPI + Jinja2
- **调度**: 内置调度器（`src/scheduler.py`，daemon 线程）+ systemd 常驻；OS cron 脚本可选兜底

## 与 AI Berkshire 的关系

量化筛选规则对齐 AI Berkshire 的 `quality-screen.md`，但做出 A 股适配：

1. **自动化** — AI Berkshire 需手动跑，本项目全自动
2. **本地数据仓库** — 逐日累积历史财务数据
3. **A股适配** — 数据源换为 AKShare / 腾讯，规则兼容 A 股特性
4. **简化代理指标** — 利息覆盖/稀释率/FCF 使用 AKShare 可获取的字段
5. **结构化分析** — 每只股票输出完整的护城河/管理层/估值/策略 JSON
