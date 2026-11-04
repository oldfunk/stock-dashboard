# 子项目三表契约（供母项目 vendor 门禁）

> 依据 `docs/subproject-protocol.md` §8：子项目提供“拥有 / 只读 / 永不碰”三表契约。
> 母项目 vendor 时逐条核对；不满足不开合。子项目域内代码归子项目，接缝归母项目。

## 1. 拥有（子项目自己的资产，母项目不得改）

| 项 | 路径/说明 |
|---|---|
| 域内代码 | `paper_trading/`（策略、撮合、风控、引擎、面板、AI 决策、集成层） |
| 本地行情库 | `data.db`（daily_bars / minute_bars / stock_pool / quote_log） |
| 本地账户库 | `paper_account.db`（account/positions/orders/fills/nav_history/op_log/agent_plans/agent_decisions/risk_state/engine_state/conditional_orders/t1_freeze） |
| 配置 | `config.yaml`、`engine.local.yaml`（gitignored）、`strategy.local.yaml` |
| 密钥 | `secrets.local.json`（0600，gitignored） |
| 交易日历缓存 | `var/trading_calendar.json`（`calendar sync` 生成） |
| 发版 | tag `v0.x.y` + 单测证据；母项目只跟 tag，不跟 main |

## 2. 只读（母项目资源，子项目只读打开）

| 项 | 路径/说明 |
|---|---|
| 母库市场库 | `data/db/stock_dashboard.db`（SQLite `mode=ro` + `query_only`） |
| 读取的表 | `kline_daily`、`financial_summary`、`dividend_annual`、`benchmark_daily`、`news_item`、`screening_result`、`watchlist_thesis`、`analysis_outcome`、`market_index`、`instrument`、`trading_calendar`、`ai_watchlist`、`watchlist`、`stock_snapshot`、`stock_analysis_history`、`watchlist_notes` |
| 路径解析 | 环境变量 `STOCK_DASHBOARD_DIR`（默认 `~/stock-dashboard`）；测试用 `PAPER_TRADING_MOTHER_DIR` |
| 只读保证 | 只 `SELECT`/`PRAGMA`；不建索引、不写、不触发母库回填 |

## 3. 永不碰（红线，违反即叫停）

| 项 | 说明 |
|---|---|
| 母库写入 | 任何 `INSERT/UPDATE/DELETE/DDL` 到母库 |
| 母库源码 | `stock-dashboard/` 下除只读 DB 外的一切（src、scripts、docs、tests） |
| 母库运行产物 | 母库 `.venv`、`data/`（除只读 DB）、日志、Parquet 快照 |
| 其他子项目 | 顶层其他 vendored 子目录 |
| 反向推分支 | 母项目永不反向推分支；子项目只发 tag |
| 跨仓代写 | 接缝问题用 Issue，不跨仓改代码 |

## 4. 接缝清单（母项目需保证）

| 接缝 | 约定 |
|---|---|
| DB 路径 | `data/db/stock_dashboard.db`（可用 `STOCK_DASHBOARD_DIR` 覆盖） |
| 端口 | 面板 8080（`--host/--port`），绑定 `0.0.0.0` 仅限局域网 |
| 运行锁 | `paper_trading.lock` / `paper_trading.event.lock`（CWD） |
| 配置命名空间 | `config.yaml`（母项目可 vendor 一份）；子项目不读母项目 config |
| 版本流 | 子项目 tag → 母项目 `git subtree pull --prefix=paper_trading <remote> <tag> --squash` |

## 5. 门禁建议（母项目侧）

1. 本文件三表逐条核对，不满足不开合。
2. 母项目用例（排除 `paper_trading/`）= 权威门禁；vendor 用例单独跑并记录。
3. 子项目提供接缝测试位：`tests/test_mother_contract.py`（真库存在时校验表/列，需 `PAPER_TRADING_MOTHER_DIR`）。
4. 域内失败不阻塞母项目（见协议 §6）。
