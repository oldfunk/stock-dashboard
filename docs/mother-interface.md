# 母库（Stock Dashboard）接口契约

> 本文档是**子项目 → 母库**的只读接口契约，供母项目合并子项目时对齐。
> 子项目仓库：`paper-trading`；母项目：`stock-dashboard`。
> 子项目**只读**母库，永不写入；母库缺数据时逐接口降级，不整体切换。

## 1. 总原则

| 原则 | 说明 |
|---|---|
| 母库优先 | 母库有该接口的数据就用母库（`source="mother:<table>"`） |
| 逐接口降级 | 母库缺某标的/某表时，**只回退该接口**的本地来源；本地也没有 → `source="unavailable"`（`data=None`） |
| 不造假 | 缺失即缺失，**绝不填 0**；母库回填中覆盖不到是正常状态 |
| 行情边界 | 日线/分钟/实时行情最终可由子项目自有 akshare/腾讯管线独立提供；母库日线作为优先来源，缺该标的时自动回退本地 |
| 来源标注 | 每个富化块带 `source/asof/stale/available`，进 AI 上下文 `data_provenance` |
| 只读 | 一律 `sqlite3 mode=ro` + `PRAGMA query_only`；不建索引、不写、不触发回填 |

## 2. 接口契约表

所有接口在 `paper_trading/integration/mother.py`，返回 `block()` 结构：
`{"available": bool, "source": str, "asof": str|None, "stale": bool, "note": str, "data": ...}`。

| 接口 | 母库表 | 依赖列 | 本地兜底 | 说明 |
|---|---|---|---|---|
| `daily_bars` | `kline_daily` | `code, trade_date, close` | `local:akshare`（`data.db.daily_bars`，前复权） | 逐标的降级；取价优先 `close_qfq`（前复权，2026-10-27 起）→ `close_adj`（后复权）→ `close`，返回带 `adj` 标注 |
| `financial_summary` | `financial_summary` | `stock_code` | 无 → `unavailable` | ROE 5y/10y、毛利/净利率、OCF、负债、ROIC、数据年数 |
| `dividend` | `dividend_annual` | `stock_code` | 无 → `unavailable` | 年度分红 |
| `benchmark` | `benchmark_daily` | `code, trade_date, close` | 无 → `unavailable` | 基准指数日线（相对强度） |
| `news` | `news_item` | `code, title` | `local:akshare`（财联社/同花顺快讯） | 母库个股 + 全局（`code=''`）；缺则本地 |
| `screening_detail` | `screening_result` | `run_id, run_date` | `local:config`（手填池，无评分） | 含 `score_detail/reason/strategy_tags` |
| `thesis` | `watchlist_thesis` | `code` | 无 → `unavailable` | 论点/卖出条件 |
| `outcomes` | `analysis_outcome` | `stock_code` | 无 → `unavailable` | AI 结论追踪；verdict 为空时从 `stock_analysis_history.ai_analysis` JSON 原文回填；覆盖度低时给 `note` |
| `outcome_samples` | `analysis_outcome` + `stock_analysis_history` | `analysis_id, benchmark_code, ret_20` | 无 → `unavailable` | 成绩校准全量样本：固定 `sh000300`、按 `analysis_id` 去重；旧样本无 verdict 时保留 signal+前向收益 |
| `raw_close_map` | `kline_daily.close_raw` + `daily_close.close` | `code, trade_date` | 无 → `{}` | raw 管道的独立收盘参照；`kline_daily` 按标的滞后时用 `daily_close` 补洞（2026-11-28 实测 69,560 重叠行零差异） |
| `sectors` | `stock_snapshot` | `code, sector, snapshot_date` | 无 → `unavailable` | 行业分布；NULL/缺行保持 None（按“未知”），不回退 |
| `regime` | `market_index` | `index_code, date` | 无 → `unavailable` | 大盘指数状态 |

此外，`paper_trading/integration/analysis.py` 仍读这些表（保留兼容）：
`stock_snapshot`、`stock_analysis_history`、`watchlist_notes`、`watchlist_thesis`、`market_index`。

## 3. 来源标签（provenance）

```
local:akshare   子项目自有 akshare 管线（前复权日线）
local:tencent   腾讯实时行情
local:sina      新浪 5 分钟线
local:config    本地手填池
mother:<table>  母库只读表
unavailable     两边都没有（缺失，不是 0）
```

AI 上下文里的 `data_provenance` 汇总每个接口的 `source/asof/stale`，
以及 `recent_closes` 的 `sources` 集合。排查问题时先看这里。

## 4. 母项目侧要求

1. **表/列稳定**：合并子项目时保留上表所列表与列；改名/删列会触发子项目契约测试
   （`tests/test_mother_contract.py`，需 `PAPER_TRADING_MOTHER_CONTRACT=1` 才跑真库校验）。
2. **索引**：`kline_daily(code, trade_date)`、`benchmark_daily(code, trade_date)`、
   `news_item(code, published_at)`、`financial_summary(stock_code)`、`screening_result(run_date)`
   已有；新增大表请一并建索引，避免子项目全表扫描拖慢回填。
3. **数据语义**：缺失保持 `NULL`，不要用 0 填充；子项目会把它当"缺失"处理。
4. **回填并发**：母库正在回填时，子项目的读查询都是只读 + 短超时（2s）+ 内存 TTL 缓存
   （日线 60s、基本面/新闻 300s、筛选 600s），不会长期占用 I/O。
5. **不需要通知**：母库新增数据子项目自动可用；只有**破坏性 schema 变更**需要同步本文档与契约测试。

## 5. 子项目侧降级行为（举例）

- 母库有 `600519` 日线 → `source="mother:kline_daily"`；母库没有 `000001` → 该标的回退
  `source="local:akshare"`，其它标的不受影响。
- 母库整个不可读（未部署/路径不存在）→ 所有接口 `unavailable`/本地兜底，交易链照常运行。
- 母库 `financial_summary` 未覆盖某标的 → 该标的 `unavailable`，AI 看到"缺失"，不臆测。

## 5.1 执行侧（引擎/AI 信号/计价）

- `TradingBridge.bar_source`（`paper_trading/data/source.py`）统一取数：**母库优先、缺该标的回退本地**。
- 开关在 `config.yaml`：`data.daily_source = auto|local|mother`（缺省 `auto`）。
- 引擎日线规则、AI 信号、`price_map`（NAV/风控计价）都走 `bar_source.get_bars`。
- 复权口径（**2026-10-27 母方 Issue #8 交付后**）：`kline_daily` 已有 `close_qfq`
  （前复权），子项目取价优先 `close_qfq` → 与本地 akshare **同口径、绝对价位可比**；
  `close_qfq` 为 NULL 的老数据逐行降级 `close_adj`（后复权）→ 不复权，并通过
  `adj` 标注。`data compare` 输出的 `mother.adj` 反映当前口径（`qfq`/`hfq`/`raw`）。

## 5.2 母库 Issue 台账与验收（#4–#11）

| 现象（v0.7.1 观测） | 母库 Issue | 验收（2026-10-27 母方交付） |
|---|---|---|
| `kline_daily` 无 `qfq` 列；`close_adj` 有 NULL（002215） | #4 | ✅ `close_qfq` 已有、近 30 天无 NULL；子项目切 `close_qfq` 优先（v0.7.3） |
| `benchmark_daily` 代码为 `sh000300`/`sh000922`，子项目按 `000001` 查不到 | #5 | ✅ 已补 `000001`/`399001`/`399006` |
| `trading_calendar` 空；`market_index` 停在 2026-07-03；`market_state_daily` 未落库 | #6 | ⚠️ 部分：`trading_calendar` 3949 行但**只有历史**（max=2026-10-26，无未来交易日），子项目仅在覆盖今天/未来时采用，否则回退 akshare（v0.7.4 防 fail-closed 停摆）；`market_index` 已更新至 2026-10-27 ✅。**请母方补未来交易日（≥3 个月）** |
| `kline_daily` 仅 959/5527 只（如 000858 缺失） | #7 | ⏳ 覆盖仍 959 只（母方回填进行中）；子项目逐标的回退本地，不阻断 |
| 最小数据契约汇总（qfq/基准/日历/主键/覆盖度） | #8 | ✅ P0/P1 项已交付；P2（覆盖度视图）随回填推进 |
| `benchmark_daily` 只在周六刷新，`sh000300` 停在 11-13；`sector` 缺契约 | #10 | ✅ 2026-11-24 关闭：每交易日 15:30 后刷新；`sh000300`/`000001`/`399001`/`399006` 到 11-23，`sh000922` 11-20（stale）；sector 全表 5247/5527，NULL 不回退；探针 `data contract` 验收 |
| 子项目 v0.10.1 vendor 通知（:8081 方案切换免口令 + intents 降级） | #11 | ✅ 2026-11-24 关闭：gate 母 1522 / vendor 996；:8081 三条验收通过 |

验收方式：Pi 上 `PAPER_TRADING_MOTHER_DIR=/home/pi/stock-dashboard venv/bin/python -m pytest tests/test_mother_contract.py -q`
（含 `close_qfq` 近 30 天非空、基准代码、日历有 `is_open=1` 行三项数据级校验）。

## 6. 价值策略接口（2026-11-24 实测）

母项目的“价值投资策略”不是一张策略注册表（`strategy_version/deployment/attribution`
当前都是 **0 行**），而是流水线写入 `screening_result` 的 **AI 交易建议 JSON**。
子项目只读这一处，不反向写。

| 位置 | 关键列 | 说明 |
|---|---|---|
| `screening_result` | `code, run_date, run_id, score, status, ai_failed` | 最新一轮筛选结果；`run_date` 是新鲜度锚点 |
| 同上 | `ai_trade_strategy` | **JSON 字符串**（兼容 dict）：`signal`, `buy_zone`, `stop_loss` 必填；`confidence`, `target_price`, `take_profit` 可选 |
| 同上 | `ai_analysis`, `ai_investment_strategy`, `reason`, `score_detail` | 分析正文 / 投资策略 / 七门原因 / 五维拆解 |
| `stock_analysis_history` | `stock_code, analysis_date, ai_trade_strategy, model` | 同一策略的历史序列 |
| `ai_watchlist` | `code, status, added_reason, monitor_condition` | 观察池（core/dropped） |
| `watchlist_thesis` | `code, core_thesis, assumptions, red_lines, sell_conditions` | 论点与卖出条件 |
| `analysis_outcome` | `signal, verdict, ret_20/60/120, excess_*` | AI 结论追踪（P5.5 用） |

**取值约定**：`signal ∈ {BUY, HOLD, AVOID}`（大小写不敏感，子项目统一转大写）；
`confidence ∈ {高, 中, 低}`（可空）；`buy_zone` 是文本区间（如 `10-12元`），由
`mother_strategy.parse_price_zone` 解析，解析失败按“区间不可用”处理。

### 6.1 探针（不是接口）

母项目负责供数；子项目只做**只读探针**，检测“接口对歪”而不是等它伪装成缺数据：

```bash
python -m paper_trading.cli data contract --json
```

输出三组 `ok/available/asof/stale/errors/warnings/shape`：

- `strategy`：`screening_result` 列、`run_date` 新鲜度、`ai_trade_strategy` JSON 形状、
  signal/confidence 取值、`buy_zone/stop_loss` 缺失计数；
- `sector`：`stock_snapshot(code, sector, snapshot_date)` 覆盖度与 NULL 数；
- `benchmark`：`benchmark_daily` 五个契约键的最新日期与缺失键。

形状错误（缺列 / 坏 JSON / 未知 signal / 缺指数键）→ 退出码 **3**，必须人工归因；
新鲜度单独用 `stale` 表达，不混进形状 `ok`。

**边界**：探针不建表、不写母库、不缓存长期结果；母库不可读时三组都返回
`available=false`，不抛异常、不阻断交易链。
面板运维页“母项目契约”卡调用同一探针（`/api/contract`，30 秒缓存）。


## 7. 性能约束（Pi 实机）

- 母库 363MB 且正在回填：子项目**不新增常驻进程、不触发回填、不 `SELECT *`**。
- 查询按 `code` 精确命中索引；小表（<10 万行）允许 `IN (...)`。
- 富化只在**构建 AI 上下文时**发生（计划/事件复盘/问答），不是每分钟轮询。
