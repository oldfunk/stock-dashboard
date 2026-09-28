# Paper Trading Framework — Agent Rules

## Project Overview

Local event-driven paper trading framework for A-share markets. Pure local operation, no third-party broker API required.

## Hard Rules

1. **Never fabricate market data** — always fetch via `AkshareFetcher` or read from local SQLite
2. **Never bypass risk checks** — all orders must go through `RiskManager`
3. **T+1 is mandatory** — `available_volume` must be 0 for same-day buys; `unfreeze_t1()` only runs on next settlement
4. **Costs are real** — commission (0.025%, min 5 CNY), stamp duty (0.05% sell-side), transfer fee (0.001%) must be deducted
5. **Slippage is configurable** — default 0.01 CNY fixed or 0.1% percentage
6. **Never claim "verified" without actual execution evidence** — manual step-by-step verification ≠ live verification

## Agent Interaction

### CLI Commands

```bash
# Check account status
python -m paper_trading.hermes_bridge status --json

# Run daily settlement
python -m paper_trading.hermes_bridge run --symbols 600519 000858 --json

# Place orders
python -m paper_trading.hermes_bridge buy --symbol 600519 --volume 100 --json
python -m paper_trading.hermes_bridge sell --symbol 600519 --volume 100 --price 1500.00 --json

# View history
python -m paper_trading.hermes_bridge nav --json
python -m paper_trading.hermes_bridge history --type orders --limit 20 --json
python -m paper_trading.hermes_bridge history --type fills --limit 20 --json
```

### Cron Integration

```bash
# Daily settlement at 4pm on weekdays
hermes cron add \
  --name "paper-trading-daily" \
  --schedule "0 16 * * 1-5" \
  --command "cd /path/to/paper-trading && python -m paper_trading.hermes_bridge cron-run --symbols 600519 000858 --json" \
  --no-agent
```

## Database

- `data.db` — market data (daily_bars, stock_pool)
- `paper_account.db` — account ledger (account, positions, orders, fills, nav_history, t1_freeze)

## Trading Rules

| Rule | Value |
|------|-------|
| Commission rate | 0.025% |
| Commission minimum | 5 CNY per order |
| Stamp duty | 0.05% (sell-side only) |
| Transfer fee | 0.001% |
| Slippage (fixed) | 0.01 CNY |
| Slippage (percentage) | 0.1% |
| T+1 freeze | Same-day buys unfrozen next day |

## A股合规（Compliance，2026-09 口径）

以下为本框架相对真实 A 股的合规映射。状态 `已建模` 表示撮合/风控强制执行；
`简化` 表示与实盘有差异、agent 不得向用户承诺一致。

| # | 规则 | 真实口径 | 本框架状态 |
|---|------|----------|------------|
| 1 | T+1（当日买入次日可卖） | 全市场，回转交易禁止 | 已建模：`available_volume` 冻结，解冻日=下一交易日（跳周末，节假日需在 `config.yaml holidays` 补） |
| 2 | 买入 100 股整数倍 | 竞价买入必须 100 股倍数 | 已建模：买入非 100 倍数直接拒单 |
| 3 | 卖出零股 | 整手余额须整手卖；不足 100 股部分一次性卖出（上交所 3.4.7） | 简化：本框架持仓恒为整手，卖出按整手校验（与规则一致；不产生零股） |
| 4 | 涨跌幅 10%（主板，含 ST） | 主板 ST 自 2026-07-06 起由 5% 放宽至 10% | 已建模：默认 10% |
| 5 | 涨跌幅 20%（创业板/科创板，含 ST） | 含 ST/新股上市前 5 日除外 | 已建模：`300/688` 开头 20%（ST 同幅，无需特判） |
| 6 | 涨跌幅 30%（北交所，含 ST） | 8/4 开头；上市首日不设限（不建模） | 已建模：30% |
| 7 | 佣金万 2.5、最低 5 元 | 上限 0.3%，交割单佣金已含规费（经手费+证管费） | 已建模：`max(金额×0.025%, 5)`，规费打包不单列 |
| 8 | 印花税 0.05% 卖出单边 | 财政部 2023-39 号公告（2023-08-28 起减半） | 已建模：仅卖出扣 |
| 9 | 过户费 0.001% 双向 | 中结算 2022-04-29 起 0.01‰ 双向 | 已建模：买卖双扣 |
| 10 | 最小变动价位 0.01 元 | A 股申报价格档位 | 已建模：成交价 `round(…, 2)` |
| 11 | 禁止裸卖空 | 普通账户无融券即不可卖空 | 已建模：卖出必须有可用持仓（不支持融资融券） |
| 12 | 单笔申报上限 100 万股 | 上交所 3.3.9 | 简化：仅做金额上限（默认单笔 20 万，远不到股数上限） |
| 13 | 交易时段 9:30-11:30/13:00-15:00 | 集合竞价/盘后定价另有规则 | 简化：不校验下单时间，按信号价撮合 |
| 14 | 集合竞价/价格笼子/排队成交 | 9:20 后不可撤、±2% 有效申报范围、涨停排队 | 简化：带内即全额成交，不做盘口模拟 |
| 15 | 资金 T+1 可取 | 卖出资金可用不可取（次日可取） | 简化：`available_cash == cash`，不区分可取 |
| 16 | 停牌/除权除息/退市整理/首日不设限 | 行情缺失、qfq 复权 | 简化：依赖前复权数据源；停牌=无新 K 线（触发无新鲜数据跳过）；首日/退市整理不建模 |
| 17 | 盘中实时价仅展示 | 面板现价盘中优先腾讯实时（60s 缓存），NAV/结算永远收盘口径 | 已建模：`data/realtime.py`（与 Stock Dashboard 同源），失败回收盘价 |

硬约束（agent 必守）：

1. 撮合参数（费率/涨跌幅/最小档位）只允许改 `config.yaml`，不许在代码里写死第二套口径；改口径必须同步更新上表。
2. 新增股票池标的时，先确认板块前缀（6/0/3/688/8/4）对应的涨跌幅档，ST 主板按 10%（2026-07 新规），不得沿用“ST=5%”旧口径。
3. 对用户只承诺 `已建模` 项；`简化` 项必须如实说明与实盘的差异，不得声称“与实盘一致”。

## LLM 接入安全（P1：只咨询，不交易）

1. **Key 三不**：不进 git（`secrets.local.json` 已 gitignore）、不进日志（`op_log` params/result 禁止出现 Key，单测锁定）、面板只回显掩码（`****末4位`）。
2. **面板写操作必须口令，但用户无感**：`admin_token` 首次保存自动生成，浏览器 localStorage 自动保管，用户永远不用看见；换浏览器凭 API Key 保存一次即接管。局域网使用，禁止把面板暴露到公网。
3. **LLM 不碰下单链**：`llm:ask` 只问答记流水；任何决策环（P2）必须走 fail-closed 钳制（schema/白名单/100 股倍数/金额上限），再经 `RiskManager`，缺一不可。
4. 命令行传 Key 会留 shell 历史，敏感环境一律用面板设置页。

## AI 交易员自治规则（P2，日内一次决策）

1. **单交易员原则**：同一账户同一天只跑 `run`（MA）或 `agent run`（AI）其一，cron 二选一；`ai:decide` 当日已落子则拒绝再跑（`--force` 除外）。
2. **三道闸**：单子数上限（`agent.max_orders_per_run`，默认 3）+ 单笔金额上限（默认 2 万）+ 日亏熔断（默认 -5%，另有回撤熔断 20% 兜底）。
3. **fail-closed**：LLM 输出非严格 JSON / 标的不在池 / 非 100 倍数 / 超限 / 风控拒绝 → 整单作废，只记 `ai:decide` 流水，不下单。坏输出永不重试下单。
4. **可审计**：每次决策记原文摘要 + 逐条处置（已成交/拒绝原因）+ 决策后资产；面板“AI 决策”中文渲染。
5. dry-run 不碰账本（不解冻不下单不记 NAV），只走校验链。
6. **休盘计划开盘执行**：`--plan-only` 基于最新定稿数据做计划存 `agent_plans`（不碰账本、不锁日）；开盘 `agent run` 优先消费当日待执行计划（价格沿用计划基准，风控重验），消费后标记 done。开盘 gap 与定稿价的差异是已知简化。
7. **Stock Dashboard 连接只读**：`paper_trading/integration/` 是唯一允许碰母库的地方，且只能 SQLite 只读打开；母库缺失/异常时静默回退本地，永不阻断交易。`--pool-from`（config/watchlist/screening/all）是唯一的池来源开关，缺省 `config`（行为零变化）。

## Risk Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| max_single_order_value | 200,000 CNY | Max value per single order |
| max_position_pct | 30% | Max single-stock position |
| max_total_position_pct | 95% | Max total portfolio exposure |
| max_drawdown_pct | 20% | Max drawdown before halt |

## Strategy: MA Cross (MA5/MA20)

- **Golden cross** (MA5 crosses above MA20): BUY
- **Death cross** (MA5 crosses below MA20): SELL
- Default volume: 100 shares per signal

## Verification Checklist

After any trading operation, verify:

- [ ] Order status is `filled` or `rejected` (not `pending`)
- [ ] `available_volume` is 0 for same-day buys
- [ ] Commission >= 5 CNY when amount * 0.025% < 5
- [ ] Stamp duty is only on sells
- [ ] NAV history is recorded
- [ ] All state changes are persisted to SQLite

## Pi Deployment Sync

`~/paper-trading` on Pi is a git clone tracking `origin/main` (read-only deploy key).
Update ONLY via `git pull` — never scp/rsync files (that breaks LF endings and bypasses history).
`data.db` / `paper_account.db` / `venv/` are gitignored and survive pulls untouched.
