# Stock Dashboard — 价值投资选股看板

基于 AI Berkshire 方法论的 A 股价值投资自动化选股系统。

## 核心理念

融合 AI Berkshire 的**7条硬性门规 + 3条豁免规则 + 镜子测试**，结合全自动的量化筛选流水线，每天收盘后自动跑完全市场筛选并给出 AI 分析。

**不靠排名，只靠及格线。** 每只股票必须通过全部门规才能进入候选池。

## 数据源架构

```
腾讯行情 (qt.gtimg.cn)
  └─ 核心行情: PE/PB/市值/价格/涨跌幅（主数据源，覆盖原A股三大交易所+科创板）

AKShare（社区维护的中国金融数据工具箱）
  ├─ stock_yjbb_em（东方财富底层）→ 全A股批量财务：ROE/毛利率/OCF/EPS/增长率
  ├─ stock_financial_abstract_ths（同花顺底层）→ 逐只深度历史：净利率/负债率/流动比率
  ├─ stock_profit_sheet_by_report_em → 利润表明细：利息费用/总股本
  └─ stock_cash_flow_sheet_by_report_em → 现金流量表：经营/投资现金流 → FCF
```

> **为什么选腾讯做行情主力**：腾讯 `qt.gtimg.cn` 是唯一免费且稳定提供 A 股实时 PE/PB/市值/价格的公开接口。AKShare 和东财旧版 API 均无法可靠获取等价数据。**财务数据完全不用腾讯**，全部走 AKShare。

> **兜底策略**：腾讯行情失败时自动切换到新浪证券+Sina原始API + AKShare 财务自算 PE/PB/市值（EPS/BVPS 来自 stock_yjbb_em，价格来自 sina hq 原始接口，不走 AKShare 封装）。已在实现中验证过茅台、五粮液等股票的计算值与腾讯直给值一致。

> **为什么选 AKShare 做财务主力**：社区主力维护，数据来源覆盖东方财富、同花顺等多个渠道。底层 API 变更时自动被社区修复，长期数据稳定性优于手写直连。`stock_yjbb_em` 一次 HTTP 调用可获取 ~5800 只 A 股的最新财务数据。逐只深度数据和历史数据只在初筛后的候选股（~200 只）上调用。

## 本地数据仓库

系统在运行中自动积累历史财务数据：

- **financial_history** 表 — 按季度/年度累积 AKShare 全量财务数据
- **financial_summary** 表 — 从历史数据计算的 5 年衍生指标
- **增量采集** — 已有数据的股票跳过，只拉新的
- **自动迁移** — 旧数据库首次启动自动 ALTER TABLE 加列

数据每天积累，随着时间推移，本地数据库逐渐拥有完整的 5~10 年历史。

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

同时叠加：PE(3~20)、PB(≤3.5)、营收/净利增长(≥0)、负债率(<65%)、市值(30~50000亿)、排除ST。

## AI 分析 — 镜子测试

每只候选股会经过 LLM 分析，输出包含：

- **镜子测试** — 用恰好5句话说清：生意本质 / 护城河 / 管理层 / 价格 / 下行风险
- **转折词计数** — 每句出现"但是/然而/除非/如果/只要"的数量，累计>2即违反纪律
- **逆向思考** — 至少2-3个致死场景，防止买入确认偏误
- **投资策略** — 仓位/周期/买卖信号/止盈止损

## 流水线

```
每日 15:30（收盘后自动触发）:
  1. 腾讯行情 → 全A股行情（PE/PB/市值）→ 初筛预过滤
  2. AKShare stock_yjbb_em → 当前财务（ROE/毛利率/OCF）
  3. AKShare stock_financial_abstract_ths → 逐只深度补充（净利率/负债率）
  4. AKShare 利润表+现金流表 → 历史财务采集 → financial_history
  5. 重建 5年汇总 → financial_summary
  6. 7条门规筛选 → 计算评分 → 候选池
  7. AI分析（镜子测试+逆向思考）→ Top 20
```

可通过 `http://<host>:9527/api/trigger_update` 手动触发流水线。

## 安装与运行

```bash
git clone https://github.com/oldfunk/stock-dashboard.git
cd stock-dashboard
pip install akshare fastapi uvicorn jinja2 httpx schedule
python -m src.collector.akshare_fetcher && python -m src.web.routes      # 首次运行: 采集+启动看板
```

无需 API Key（腾讯行情 + 东方财富 + 同花顺均为免费公开接口）。

## 配置

详见 `config/config.yaml`。主要参数：

- `screener.conditions.*` — 7条门规的阈值
- `schedule.daily_update_time` — 每日自动运行时间
- `web.port` — 看板端口

## 技术栈

- **数据采集**: httpx + AKShare（东方财富/同花顺底层）+ 腾讯行情API
- **存储**: SQLite (WAL模式)
- **AI分析**: 通过通用LLM API (支持OpenAI兼容接口+免费模型池)
- **Web看板**: FastAPI + Jinja2
- **定时任务**: 内置调度器 (schedule)

## 与 AI Berkshire 的关系

本项目的量化筛选规则完全对齐 AI Berkshire 的 `quality-screen.md` 方法论，但做出了以下适配：

1. **自动化** — AI Berkshire 需要人手动跑，本项目全自动
2. **本地数据仓库** — 逐日累积历史财务数据
3. **A股适配** — 数据源换为 AKShare/腾讯，规则兼容A股特性
4. **简化代理指标** — 利息覆盖/稀释率/FCF 使用 AKShare 可获取的字段
