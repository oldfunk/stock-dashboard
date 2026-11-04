# P5/P6/P7 — 母库契合与 AI 决策深化（长线）

> 依据：`docs/subproject-protocol.md`（母整合/子施工；域内子项目修，接缝母项目修，Issue 是唯一跨仓通道）、
> `docs/mother-interface.md`（子项目接口契约）。
> 目标：让子项目真正“切到母库策略”跑起来，验证与母库结构是否契合；发现接缝问题按协议给母库提 Issue。

## P5 — AI 决策深化（组合级 + 止损门禁 + 回放）

- [x] P5.1 组合级约束进 prompt：总敞口/现金占比/行业集中度（`stock_snapshot.sector`）/相对基准（`benchmark_daily`）；`basis` 允许引用 `portfolio.*`
- [x] P5.2 止损门禁：`agent.require_stop=true` 时，买入缺 `risk_stop` 且无对应卖出条件 → 拒单；`risk_stop` 与引擎 `trailing_stop/time_stop` 口径对齐说明
- [x] P5.3 AI 决策落库 `agent_decisions`（decision_date/plan_id/actions_json/basis/result）供回放与审计
- [x] P5.4 决策回放/对照：CLI `agent replay --date`（AI 决策 vs 规则信号，只读不下单）
- [ ] P5.5 AI 成绩校准：`analysis_outcome` 有 verdict 后接入（**等母库回填**，覆盖度低则 `unavailable`）

## P6 — 母库数据补全适配与监控

- [x] P6.1 schema 对齐：`instrument`（symbol→instrument_id）、`kline_daily.market/instrument_id`、`trading_calendar`（空则回退本地日历）
- [x] P6.2 覆盖度监控：每接口 coverage/stale 汇总，CLI `data coverage`（面板 provenance 面板待做）
- [x] P6.3 母库行情口径：`close_adj` 为后复权、本地为前复权；`data compare` 量化差异
- [x] P6.4 契约测试扩展：`instrument`/`trading_calendar` 列校验
- [x] P6.5 执行侧切库：`config.data.daily_source` + `BarSource`（引擎日线规则/AI 信号/`price_map` 母库优先、缺标的回退本地）；`data compare` 量化口径差异
- [x] P6.6 **qfq 口径打通（v0.7.3，母方 Issue #8 交付后）**：母库取价切 `close_qfq`（NULL 逐行降级 `close_adj`→`close`）；`calendar sync` 母库优先（`trading_calendar` → akshare 兜底）；契约测试加数据级校验（近 30 天 `close_qfq` 非空、基准代码、日历 `is_open=1` 行）

## P7 — 可观测/审计/跨仓协作

- [x] P7.1 三表契约文件（拥有/只读/永不碰）供母项目 vendor 门禁 → `docs/CONTRACT.md`
- [x] P7.2 面板“数据来源”面板（`/api/data-provenance`，source/asof/stale 可视化）
- [x] P7.3 母库 Issue 提交（接口级）：qfq 缺失、`trading_calendar` 空、`market_state_daily` 未落库（#4–#7）
- [ ] P7.4 契约测试纳入母项目 CI（接缝测试位）—— **母项目侧**，子项目不越界

## “切到母库策略”验证路径

1. `data compare`：本地 qfq vs 母库日线（v0.7.3 起母库也取 `close_qfq`，同口径）的 MA5/MA20 交叉差异 + 收益相关性 → 量化口径影响。
2. `agent run --dry-run --data-source mother`：用母库日线喂信号，与本地结果对照（默认仍本地，开关式）。
3. 差异写入 `op_log`（`data:compare`）与 `docs/mother-interface.md` 结论；接缝问题提母库 Issue。

## 母库 Issue（本轮已提交，`oldfunk/stock-dashboard`）

- #4 `kline_daily` 缺 qfq 口径（`close_adj` 有 NULL）→ 执行信号不可比 → **✅ 已交付（close_qfq）**
- #5 `benchmark_daily` 指数代码约定不一致（sh000300/sh000922）→ **✅ 已补 000001/399001/399006**
- #6 `trading_calendar` 未回填、`market_index` 过期、`market_state_daily` 未落库 → ⚠️ 部分（日历有历史无未来，market_index 已新鲜；待母方补未来交易日）
- #7 `kline_daily` 覆盖不全（959/5527，如 000858 缺失）→ ⏳ 回填进行中，子项目逐标的回退
- #8 最小数据契约汇总（qfq/基准/日历/主键/instrument/覆盖度）→ **✅ P0/P1 已交付；验收见 v0.7.3/v0.7.4**

## 执行侧验证结果（2026-10-27，Pi）

- `daily_source=auto`：002215/600989/000792/002192/300390 → `mother:kline_daily`（最新 10-26）；
  000858 母库缺失 → 回退 `local:akshare`。
- 本地日线此前对部分持仓滞后（000858 曾停在 2025-10-14），母库优先后执行信号不再受本地同步滞后影响。

## 母库契约验收结果（2026-10-27，Pi，v0.7.3/v0.7.4）

- 真库契约测试 `PAPER_TRADING_MOTHER_DIR=/home/pi/stock-dashboard`：**14 passed**（含近 30 天
  `close_qfq` 非空、基准代码 `000001/399001/399006`、日历有 `is_open=1` 行三项数据级校验）。
- `data compare`（60 天窗口）：600036/002215 两侧 `adj=qfq`、收益相关性 **1.0**；002215 交叉数差
  +1/-1（停牌日两边 bar 集对齐差异，净 0）；000858 母库缺失回退本地。
- `agent replay --date 2026-10-27`：**v0.7.2 决策落库 + 回放链路首次实盘验证通过**
  （计划 #14 开盘执行落库，AI vs 规则一致度 4/5）。
- **发现并热修（v0.7.4）**：母库 `trading_calendar` 只有历史无未来（max=10-26），母库优先会把
  本地缓存写成“覆盖不到今天”触发 fail-closed 停摆 → sync 只在母库覆盖今天/未来时采用，
  否则回退 akshare；已在 Issue #8 反馈请母方补未来交易日。
