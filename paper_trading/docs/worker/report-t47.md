# report-t47 · M3 四缺口补齐 G1-G4 + 顺手项（Qoder，P0·M3 硬前提）

分支 `worker/t47`（堆叠在 `worker/t48` ⇒ `worker/t50` ⇒ `origin/main@085e318`，**待 review 未合并**）。
全套 **568 passed / 9 skipped**（基线 515/9 ⇒ 本任务新增 **53 条**，零回归）。
`spikes/mutation_t47.py` 十四个源码级变异逐个被杀（§6）。
**没有对现网账本做过任何写操作**：所有落账/归档都在临时库上跑；
`rq day --ledger` 的现网演练需要主 AI 另行授权（§7-Q2）。

## 0. 一句话结论

T45 列的四个缺口现在都有实现和测试：**G1** rqalpha 成交经
`PaperBroker.apply_external_fill` 落 `orders/fills/positions/t1_freeze`（不重判价、
不重算费、守卫失败就停笔）；**G2** 收盘 NAV 由 `record_close_nav` 出，业务日判重、
零成交也记、旧线已记 `nav:close` 就让位、缺价直接拒；**G3** `engine.executor`
进 `DEFAULTS` 白名单 + 进 `session_fingerprint` + 落 `op_log[m3:switch]` +
守护进程启动校验（未知值/框架缺失 ⇒ exit 2 不降级）；**G4** `cli ledger archive`
冷备（sqlite backup API + chmod 444）→ 拒绝抹掉有数据的现网账 → 新开一本并搬
持仓/未解冻 T+1 → 留痕 `op_log[ledger:archive]`，同一 `--stamp` 重跑不产生第二份归档。
顺手项 `fetch-raw --start/--end` 也通了（裁决 3）。

## 1. 派发原文与裁决逐条核销

| 派发/批复原文 | 状态 | 落地点与证据 |
|---|---|---|
| **G1**「rq_engine runner 接 PaperBroker 适配（影子 evidence 路径升级为正式执行路径；`shadow.py` 的对照用法保留），成交/拒单/费用落 `orders/fills/op_log`——钱类件，逐字段测试 + 变异」 | ✅ | `broker/paper_broker.py:397` `apply_external_fill()`（新增）+ `LedgerConsistencyError`（`:25`）；`integration/rq_engine/ledger.py` `post_session()`；`mod_entry.py:_on_trade` 出三分量费用（`commission/tax/other_fees`，见 T38 追加）；`cli.py:_ledger_apply`。**`shadow.py` 一字未改**（对照路径与执行路径分开，影子仍写 `--shadow-dir`）。逐字段测试 15 条（§5），变异 ①②③④⑤ 全被杀 |
| **G1** 判据「一笔计划订单从 rqalpha 成交 → 落到现网账本三张表，字段与旧线口径可逐位对照」 | ✅ 离线等价 | 落账走的是旧线**同一批原语** `_execute_buy/_execute_sell/_persist_order/_persist_fill` ⇒ `avg_cost` 含费、T+1 解冻日、`peak_price` 口径天然一致（`test_apply_external_fill_avg_cost_includes_fees_like_legacy`）。真·同日双写对照需现网授权（§7-Q2） |
| **G1** 判据「失败路径（拒单/资金不足）也落 `op_log` 且原因可读」 | ✅ | 引擎拒单 ⇒ `op_log[rq:reject]`（`ok=0`，原因原样进 result，`test_post_session_normalizes_order_book_id_and_traces_rejects`）；账本守卫失败 ⇒ `errors=[{why: ledger_inconsistent, biz_id, error}]` + `op_log[rq:reject]` 之外的 CLI exit≠0（`cli.py:1953`），错误串直接写"引擎要买 X 股花 Y，账本只有 Z" |
| **G2**「新线每日收盘 `record_nav`（对齐旧线 `nav:close` 语义：业务日判重、无交易也记、幂等）」 | ✅ | `ledger.record_close_nav()`：判重用既有 `broker.has_operation(action, day)`（**它本身就认 result 里的业务日**，Issue #2 的教训），并且 `nav:close` 已记同一天时新线让位（双轨期不产生两行净值）；金额调 `broker.get_nav()` 不另写公式（`test_record_close_nav_uses_the_same_math_as_legacy` 断言与旧线逐位相同）；缺价拒记（`price_missing`）而不是按 0 市值写 |
| **G3**「config 键（**必须进 `engine/config.py:27-37` DEFAULTS 白名单**否则静默丢弃）」 | ✅ | `engine/config.py:33` `"executor": "legacy"`（默认旧线，M3 之前行为不变）；变异 ⑧ 删掉这行 ⇒ 2 条必红，证明这条判据不是嘴上写的 |
| **G3**「状态进 session_fingerprint」 | ✅ | `runner.py:143` `"ledger": cfg["mod"]["ptpaper"]["ledger"]`；`build_config(ledger=…)` 默认 False（`test_session_fingerprint_changes_with_ledger_flag`）|
| **G3**「落 `op_log`」 | ✅ | `ledger.trace_switch()` ⇒ `op_log[m3:switch]`，同日同值只记一行；`rq day` 在"没有显式旗子、听配置"那条分支上留痕（`_ledger_decide`），显式 `--ledger/--no-ledger` 属排障不当日切底座 ⇒ 不留痕不误导 |
| **G3**「切换/回退各一次演练测试」 | ✅ 离线两向 | `test_trace_switch_records_once_per_day_and_executor`（rqalpha→legacy 各一条留痕）+ `test_legacy_key_absent_and_explicit_produce_identical_book`（§5.2 的回退回归：**键不存在** 与 **键=legacy** 两次跑，六张表逐位一致）+ `test_startup_guard_refuses_ambiguous_base`(5 组参数) + `test_engine_daemon_exits_2_on_unknown_executor`（真子进程 exit 2）。**现网真·跑一天的切换/回退**未做——它等于写钱类账本，需要授权（§7-Q2） |
| **G4**「`cli` 子命令（冷备 → chmod 444 → 新开账 NAV 对齐 → 留痕 `op_log[ledger:archive]`），幂等可重入」 | ✅ | `cli.py` 新 `ledger` 组：`ledger archive --archive-dir … [--stamp --allow-existing --yes]`、`ledger executor`（只读）。实现 `ledger.archive_and_reopen()`：`sqlite3.backup()` 冷备（不是 `cp`）→ `chmod 444` → 非空账本无 `--allow-existing` 直接拒 → `.pre-reopen-<stamp>` 现场副本 → 新账按归档 `cash + 持仓 + 未解冻 t1_freeze` 重建 → `op_log[ledger:archive]`（含归档行数清点）。幂等：同 `--stamp` 重跑 `archive_reused=True` 且归档字节/mtime 不变（`test_archive_is_idempotent_on_rerun`）|
| **G4** 判据「产出一份"衔接说明"（哪几天归谁）」 | ✅ 语义 + ⏳ 文件 | 说明进了 `op_log` 的 result（`cash/positions/frozen_rows/total_value/nav_anchor/archive_rows`）与命令返回值；runbook §4 ③ 要求的 `M3-BRIDGE-<date>.md` 是切换日运维动作，本工具给的是**生成它所需的数**，不替主 AI 落笔 |
| 顺手「`fetch-raw --start/--end`（裁决 3）」 | ✅ | `raw_feed.sync_raw(start=, end=)` + `flip_watch_with_mother(start=, end=)`；CLI `data fetch-raw --start/--end`（`cli.py:2176`）。三处窗口仍同源（采集/母库参照/逐日闸），变异在 §5 说明。**新语义**：区间终点早于真实今天时**不做盘中跨源**（历史日没有当天实时价可比），默认路径照旧强制 |
| 「`report-t47.md`；按协议附裁决核销清单」 | ✅ | 本文件 + §1 表 |

**Batch 10 其余裁决的对照**：裁决 2（影子池 38 只）与裁决 4 的两条（agent tick 保留、
两轮否决）属 T42/T48 范畴，本任务未触碰；裁决 1 已在 T50 落地。

## 2. 写测试时被抓出来的三个我自己的缺陷（都是产品代码，不是测试）

按 AGENTS 纪律"fail-loud 优先"，这三处如果留到现网第一次真跑才暴露，代价分别是一次
不可归因的账本损坏 / 一次归档半途失败 / 一次回填全线红。

1. **`with sqlite3.connect(...)` 不关句柄**（`ledger.archive_and_reopen`）。
   sqlite3 的连接上下文管理器只提交/回滚事务，**不 close**；Windows 上残留句柄让
   紧接着的 `src.unlink()` 抛 `PermissionError: [WinError 32] 另一个程序正在使用此文件`。
   ⇒ 冷备做完、归档已 444、新账没开起来——**最难收拾的半途状态**。
   修：所有连接套 `contextlib.closing()`（`ledger.py:235` 注释记着这条实测）。
2. **`sqlite3.Row.get()` 不存在**：`cash = (row or {}).get("cash")` 在表存在时抛
   `AttributeError`，只在"表不存在"时侥幸走 `{}.get`。⇒ 第一次真归档就炸。
   修：显式 `cash_row["cash"]`，并把 `has(name)` 从 lambda 提成函数。
3. **`--start/--end` 半成品改名事故**：我把 `start/_end` 换成 `start_d/end_d` 时漏了
   两处引用 + `flip_watch_with_mother` 的新参数没进签名 ⇒ `NameError`，
   全套 **17 条**既有测试立刻红（`test_raw_feed`/`test_raw_pipeline_t41`/
   `test_caliber_defense_t34`/`test_m3_verify_t46`/`test_postclose_defense_t50`）。
   ⇒ 这不是"新测试没覆盖到"，是**既有回归网接住了我**（T46 那张网的第一次实战价值）。

## 3. 一处我刻意改掉的设计：幂等判定不能用"最近 N 条"窗口

初版 `_already_posted()` 扫 `broker.get_op_log(200)`。而本仓 `get_op_log_day` 的
docstring 已经写明为什么窗口不可信：**面板高频问答会把决策记录挤出最近 N 条**
（曾致同日重复跑）。幂等判定漏一次 = 凭空多一笔成交，是最贵的错。

⇒ 改成 `PaperBroker.has_op_with(action, needle)`：按 action 全表查
`params/result LIKE`（`_`/`%` 转义 + `ESCAPE '\'`）。
`test_post_session_biz_id_survives_op_log_window` 直接把 `get_op_log` 打成空表来证明
判定不依赖窗口。同理 `record_close_nav` 复用 `has_operation()`，并顺手把 `date`
放进 **result**（Issue #2 的跨日补记判重只认 result 的 `date`）。

## 4. 三条设计决定（都可反对，写在这是为了 review 时少绕）

1. **落账默认关**：`build_config(ledger=False)`、`engine.executor` 默认 `legacy`、
   `rq day` 不传旗子时按配置决定。M3 之前现网账本仍由旧线独占写。
2. **`--ledger-dry`**：切换日演练要看得见钱但不碰钱——dry 分支返回
   `{biz_id, commission/stamp_duty/transfer_fee}` 而不写任何表（含 NAV）。
3. **守卫失败 = 停批**：`post_session` 遇到 `LedgerConsistencyError` 就 `break`，
   后面的笔不写；但**费用拆分缺分量**是另一级——先整批预检，任何一笔缺就**一笔都不写**
   （缺分量意味着这份 evidence 不是当前格式，写一半只会得到半本对不上的账）。

## 5. 测试清单（`tests/test_ledger_gaps_t47.py`，53 条）

- **G1 broker（9）**：逐字段落账（价/量/方向/三分量费用，**0 费不回填费率**）、
  `avg_cost` 含费、T+1 冻结行与解冻日、现金不足零写入、可卖量不足拒卖、
  卖光后不留 0 股幽灵行、垃圾入参参数化（0 量/0 价/负价/方向 0）。
- **G1 ledger（7）**：三分量缺一即拒（三个键分别删）、整批缺分量零写入、
  biz_id 幂等、窗口失效攻击、守卫失败停批、dry-run 零写入、
  `000651.XSHE`→`000651` 归一 + `rq:reject` 留痕。
- **G2 NAV（6）**：零成交也记、同日判重、`nav:close` 让位、缺价拒记、
  与 `broker.get_nav()` 逐位相同（含 `pnl` 对 `initial_cash`）、
  `close_prices` 只取 ≤当日的最近一根 raw。
- **G3 开关（9）**：DEFAULTS 白名单 + yaml 覆盖不被吞、大小写/空格归一、
  未知值抛错（4 个坏值）、指纹随 `ledger` 变、`trace_switch` 幂等、
  `_ledger_decide` 三态（显式开/显式关/听配置）+ 只在"听配置"时留痕、
  `startup_guard` 5 组参数、守护进程子进程 exit 2、
  **键不存在 vs 键=legacy 六表逐位一致**。
- **G4 归档（8）**：冷备+只读位、新账 cash/持仓一致、历史表不搬、
  `.pre-reopen` 现场副本、拒绝抹掉非空账本（**拒绝时 SHA 不变**，但冷备已先落盘）、
  同 stamp 重跑不产生第二份、T+1 未解冻跟着搬且解冻入口还在、
  `-journal` sidecar 存在即拒绝、账本不存在时 `FileNotFoundError`。
- **顺手项（4）**：`--start/--end` 过滤越界行 + `ref_window` 回显、
  历史区间不强制盘中跨源（`intraday_missing=0`）、默认路径仍强制（一字未松）、
  `flip_watch_with_mother` 三处窗口听同一份 start/end。

## 6. 变异验证（`python spikes/mutation_t47.py`，改源码不改断言）

```
① G1 apply_external_fill 不查现金        → 2 failed   ② 不查可卖量      → 1 failed
③ G1 费用拆分缺失就猜                    → 2 failed   ④ biz_id 幂等失效  → 3 failed
⑤ G1 守卫失败后继续写后面的笔            → 1 failed   ⑥ G2 同日判重失效  → 2 failed
⑦ G2 持仓缺价不拦                        → 1 failed   ⑧ executor 掉出 DEFAULTS → 3 failed
⑨ 未知底座静默降级                       → 3 failed   ⑩ ledger 不进指纹  → 1 failed
⑪ G4 非空账本不拒绝重开                   → 1 failed   ⑫ 不搬 t1_freeze   → 1 failed
⑬ 不查在写事务                           → 1 failed   ⑭ 框架缺失不拦启动  → 1 failed
还原后 → 88 passed, 3 skipped（三个测试文件基线：t47 52 + rq 适配器/引擎切换 36）
```

## 7. 遗留与请主 AI 决定的三件（同步进 `questions.md`）

- **Q1 双活防护归谁**：`engine.executor=rqalpha` 时，`paper_trading.engine` 守护进程
  **本身仍是旧线撮合**（T47 只让 `rq day` 落账）。我只按 §5.2 做了"未知值/框架缺失
  ⇒ exit 2"，**没有**擅自让守护进程在 rqalpha 底座下停单——那等于改现网执行语义，
  且与 T48 的 ①/②/③ 拍板纠缠。请拍板后一并实施（runbook §4 顺序铁律"不可双活一秒"
  现在仍靠人工）。
- **Q2 现网演练授权**：G1/G2 的"真·跑一天写账本"与 G4 的"真·归档现网账本"都需要
  一次受控的现网操作。要么给一个 scratch 账户库路径（我照 T42 的做法先做前后指纹
  核对），要么等 T48 拍板后随切换日一起做。
- **Q3 `m3 verify` 要不要长出新项**：现在有四类新留痕（`rq:fill`/`rq:reject`/
  `rq-nav`/`m3:switch`/`ledger:archive`）和 `store.gate_rejected`，T46 的十项里没有
  检查它们的条目（沿用 T46 报告里那条待答问题）。要加的话我另开一件，不动 T46 成品。

## 8. 回应 Hermes T49 复核点 #2（`entry_day` 盲区）

T49（`e718a26`）写「新线若不写 `positions.entry_day`，T+1 检查永远查不出；建议
m3 verify 用 fills 推断（fallback），**或要求新线写 entry_day**」——那份报告完成时
T47 还没交付（它自己写了"未见 report-t47"）。选后者，且已经成立：G1 的落账复用旧线
同一条 `_execute_buy`，`entry_day=COALESCE(NULLIF(entry_day,''), today)` 与
`peak_price=MAX(...)` 都写。新增 `test_apply_external_fill_writes_entry_day_and_peak`
把这条钉住——**防止以后有人给新线另写一条不写 entry_day 的落账路径**（那条假绿比没绿更贵）。
⇒ `m3 verify` 的 `t_plus_one` 项在新线下不需要 fills 兜底也能成立。

## 9. 交付物清单

| 文件 | 作用 |
|---|---|
| `paper_trading/broker/paper_broker.py` | `LedgerConsistencyError` + `apply_external_fill()`（G1 落账原语）+ `has_op_with()`（幂等判定，全表按 action） |
| `paper_trading/integration/rq_engine/ledger.py`（新） | G1 `post_session`/`trade_fee_split`；G2 `record_close_nav`/`close_prices`；G3 `executor_from_config`/`startup_guard`/`trace_switch`；G4 `archive_and_reopen` |
| `paper_trading/integration/rq_engine/runner.py` | `build_config/run_session(ledger=)`、指纹含 `ledger`、`ptpaper.ledger` 透传 |
| `paper_trading/integration/rq_engine/mod_entry.py` | 成交明细出 `commission/tax/other_fees` 三分量（G1 的前置：不猜拆分） |
| `paper_trading/engine/config.py` | `DEFAULTS["executor"]="legacy"`（白名单，默认不变） |
| `paper_trading/engine/__main__.py` | 启动时底座校验与打印；未知值/框架缺失 ⇒ exit 2 |
| `paper_trading/cli.py` | `rq day --ledger/--no-ledger/--ledger-dry`、`_ledger_decide`/`_ledger_apply`、`ledger executor`（只读）、`ledger archive`（`--yes` 才动账本）、`data fetch-raw --start/--end` |
| `paper_trading/data/raw_feed.py` | `sync_raw(start=, end=)` + `flip_watch_with_mother(start=, end=)`；历史区间不做盘中跨源，默认路径语义不变 |
| `tests/test_ledger_gaps_t47.py`（新） | 53 条钱类逐字段测试 |
| `spikes/mutation_t47.py`（新） | 13 个源码级变异 + 还原全绿 |
| `docs/migration-m3.md` | §0 表格后加"T47 实现落点"小节（只标实现与命令，**不宣布缺口已关闭**——关闭判据要 review + 现网演练） |
