# report-t14 — 研究流水线移植设计：grid / walkforward → rqalpha（P1，只设计不写码）

执行人：员工 AI（Qoder）｜2026-10-28｜分支 `worker/t14`（**纯设计文档**，不碰产品代码）
任务书验收：**映射表每行有现代码 `文件:行` 出处** —— §2/§3 逐行给到，rqalpha 侧同样给 `文件:行`。

> **一句话结论**：研究流水线的**判定部分**（成交/费用/指标）该交给 rqalpha，
> 但**"筛选器 + 样本外 + 0 笔剔除 + 基准超额 + 指纹注册表"这五件纪律 rqalpha 一件都不提供**
> ——实测 rqalpha 6.4.2 core **没有任何 optimize/寻优设施**（全库 grep `optimize` 零命中），
> `utils/concurrent.py:40` 只有一个带进度的进程池。所以移植 = **把"跑一组参数"换成
> rqalpha 会话，调度/排名/门禁/注册表继续由我们做**（L2 层，符合 §5.4 边界）。
> 顺带在映射过程中发现现 `walk_forward()` ** promised 的过拟合量度其实没算**
> （`optimize.py:251-252` 的 `oracle` 与 `picked` 是同一行，`picked` 未被使用）——见 §6。

---

## 1. 边界：哪些进框架，哪些留给我们

| 研究动作 | 归属 | 依据 |
|---|---|---|
| 一次 trial 的成交/费用/T+1/涨跌停/资金曲线 | **rqalpha**（判定） | AGENTS 引擎教条 + `refactor-plan` §5.4"判定全在框架内" |
| 指标（sharpe/max_drawdown/alpha/收益） | **rqalpha `sys_analyser`** | `mod/rqalpha_mod_sys_analyser/mod.py:490,513,515,523,545` |
| 网格枚举、变体分组、样本内/外切分、排名、剔除 | **我们（L2 调度）** | rqalpha 无此设施（实测 grep 零命中） |
| 基准（等权买入持有）对照 | **我们**（可让 rqalpha 出 `alpha/beta`，但我们的纪律是"对比等权买入持有"，不是对比指数） | AGENTS 路线图纪律 #7（`AGENTS.md:172`）；analyser 的 benchmark 是指数口径（`mod.py:144-156`） |
| 指纹/注册表/有限性门禁 | **我们** | `provenance.py:1-13` 是我们的件，rqalpha 无实验追踪 |

## 2. 纪律逐条映射表（现代码出处 → rqalpha 对应物/缺口 → 迁移动作）

| # | 纪律（AGENTS 原文位置） | 现代码实现（`文件:行`） | rqalpha 对应物 / 缺口 | 迁移动作 |
|---|---|---|---|---|
| ① | 网格只是筛选器，输出是"待验证线索"（`AGENTS.md:172`） | `optimize.py:125-202 grid_search()` 产出 `best/top` + `note`（`optimize.py:187` 明写"样本内结果，需样本外复验"） | **缺口**：rqalpha 一次 `run()` 只出一个结果（`main.py:133-253`），没有"变体"概念 | 调度层保留在我们侧：`grid_search` 的循环体改成"起一个 rqalpha 会话"（复用 `integration/rq_engine/runner.run_session` 的会话构造方式，`spikes` 已证），`note` 文案不变 |
| ② | 必须样本外（前 60% 选参数、后 40% 检验）（`AGENTS.md:172`） | `optimize.py:205-267 walk_forward()`：`optimize.py:224` 计算 `cut`，`optimize.py:225` 切 `is_days/oos_days`，`optimize.py:239-241` 用**同一** `_evaluate` 跑样本外 | **对应物**：rqalpha 的窗口就是 `base.start_date/end_date`（`main.py:50-64 _adjust_start_date`）⇒ 样本内/外 = 两次不同窗口的会话 | 直接沿用切分逻辑，只把 `_evaluate` 换成 `run_rq_trial(cfg, rules, start, end)`；窗口边界必须显式传，禁止让 `available_data_range` 的 bundle 末日悄悄夹掉（`report-t13 §4.6`） |
| ③ | 0 笔成交的变体不参与排名（`AGENTS.md:172`，"熊市里什么都不做会排第一"） | `optimize.py:149` `_min_trades`；`optimize.py:180-183` 剔除并入 `dropped`；`optimize.py:194-201` 输出 `dropped_no_trade/dropped_top` | **缺口**：rqalpha 不知道"0 笔"是无效结论，`sys_analyser` 会给 0 收益 0 回撤的漂亮 summary | 排名/剔除继续在我们侧；对 rqalpha 结果取 `summary.total_trades` 作判据（`analyser/mod.py` 的 summary 键），并在注册表里给被剔除 trial 记 `status=excluded_zero_trade` |
| ④ | 超额 = 变体收益 − 等权买入持有 | `optimize.py:109-120 _evaluate()` 调 `buy_and_hold(...)` 并算 `excess_pct`；`optimize.py:184` 按 `excess_pct` 排序 | **缺口**：analyser 的基准是指数（`mod.py:144-156`，需 `benchmark` 配置），而 `benchmark` 校验在 bundle 覆盖不足时会**直接抛错**（`mod.py:203`，我在 T13 §3 S1 现场撞过） | 两种做法：(a) 保持"等权买入持有"自算 —— 用同一段 rqalpha 会话跑一个 buy-and-hold 策略（判定仍归框架，最干净）；(b) 若改用指数基准，必须**先喂指数日线**（我们的 `market_index`）否则 analyser 崩。**建议 (a)** |
| ⑤ | 无未来函数：日线 T 收盘判定、T+1 成交；回测与实盘同一套判定（`AGENTS.md:166`，口径一致 `AGENTS.md:184`） | `optimize.py:93-107 _evaluate()` 与实盘共用 `Backtester`/`rules.py`；日内规则丢弃 `ts > now` 的分钟 | **对应物**：rqalpha 的撮合时序由 `matching_type` + 事件源保证（日频强制 `next_bar→current_bar`，`sys_simulation/mod.py:59-62`；T+1 在 `position_model.py:176-177/118-123`） | 研究线/模拟线**必须共用同一 `matching_type` 配置**，否则回测选出的参数在模拟线上口径不同——建议把 `matching_type/price_limit/slippage` 纳入 §4 注册表的 `params_hash` |
| ⑥ | 不静默（`AGENTS.md:186`） | `optimize.py:151-153` 分钟线缺失直接返回 `error`；`optimize.py:145-146` 网格无变体返回 `error` | **缺口**：rqalpha 在窗口越界/缺数据时**只吐 NaN 不抛错**（`report-t5 §10.3`；`main.py:52-64` 夹窗口是唯一的显式失败） | 每个 trial 结果过 **有限性门禁**（§4 的 `metrics_finite`），不通过的 trial 记 `status=invalid_nan` 而不是进排名 |
| ⑦ | 结果绑定数据快照与参数（`provenance.py:1-13`） | `backtest.py:388-397` 挂 `res.fingerprint`；`cli.py:1978-1980` grid 路径补 `data_fingerprint`；`cli.py:2033` 输出 | **缺口**：rqalpha 无实验概念，`jsonpickle` 状态也不含数据版本 | 注册表以我们的 `bars_hash/params_hash/days_hash` 为主键，另加 **bundle 版本 + 适配器版本**（§4） |
| ⑧ | 并行不能破坏"一天一进程"（T13 §4.7 / G-T13-9） | 现 `grid_search` 同进程循环（`optimize.py:166`） | rqalpha `cleanup_resources()`（`main.py:301-320`）置空 Environment 单例，同进程重入会让日志报错掩盖真因 | 用 `rqalpha/utils/concurrent.py:40 ProgressedProcessPoolExecutor`（或我们的 `ProcessPoolExecutor`）**一 trial 一进程**；`initializer` 里只做 import，不在 worker 里循环跑第二个会话 |

## 3. 指标口径对照（排名必须用同一把尺）

| 指标 | 我们（`backtest.py metrics()`） | rqalpha `sys_analyser` | 移植处理 |
|---|---|---|---|
| 年化/sharpe | 自算：日收益序列 + `√244` 年化（`backtest.py:663`，输出键 `sharpe_annual` `backtest.py:679`） | `risk.sharpe`（`mod.py:515`，rqrisk 口径，交易日数来自 `env.trading_days_a_year`） | **不要混用**：注册表存 `sharpe_source ∈ {rq, ours}` + 参数（`custom_trading_days_a_year` 可对齐 `main.py` config） |
| 最大回撤 | 自算（`backtest.py:678 max_drawdown_pct`） | `risk.max_drawdown`（`mod.py:523`） | 同上；F-tier 容差要写进注册表的 `tolerance` 字段 |
| 成交笔数/费用 | `backtest.py:680 trades`、`backtest.py:384/518 total_fees` | `summary.total_trades` + 逐笔 `trades` 表（含 `commission/tax/transaction_cost`，T29 实测列名） | 费用差异**已知且量化**＝过户费 0.001%（T29 R3、T32 E6 实测 0.0222 元/笔）→ 注册表记 `fee_model_version` |
| 基准 | 等权买入持有自算 | 指数 alpha/beta（可选） | 见 §2-④ |

## 4. 实验注册表设计（先 schema，不落库不改代码）

新表 `experiments`（建议放 `data.db`，与研究数据同库；不碰账本库）：

```sql
CREATE TABLE experiments (
  exp_id        TEXT PRIMARY KEY,          -- sha256(bars_hash|params_hash|days_hash|engine_ver)[:16]
  kind          TEXT NOT NULL,             -- grid | walkforward | single
  grid          TEXT,                      -- 网格名（optimize.py:270 available_grids()）
  symbol        TEXT,
  params_json   TEXT NOT NULL,             -- 变体参数（optimize.py:171-179 的 rec["params"]）
  stage         TEXT NOT NULL,             -- in_sample | out_of_sample | single
  bars_hash     TEXT NOT NULL,             -- provenance.py:55-91
  minutes_hash  TEXT,                      -- 日内网格才有
  days_hash     TEXT NOT NULL,             -- 同 bars 不同窗口 = 不同实验
  params_hash   TEXT NOT NULL,             -- provenance.py:48-52（须含 matching_type 等 §2-⑤）
  data_source   TEXT NOT NULL,             -- rq_engine@<ver> + bundle 版本 + caliber='raw' + 因子末日
  engine_ver    TEXT NOT NULL,             -- rqalpha 版本 + 适配器指纹（runner.session_fingerprint）
  status        TEXT NOT NULL,             -- ok | excluded_zero_trade | invalid_nan | failed
  metrics_json  TEXT,                      -- ret/mdd/sharpe(+source)/trades/fees/excess/bench
  metrics_finite INTEGER NOT NULL DEFAULT 0,  -- 有限性证明：1=全指标 isfinite
  drop_reason   TEXT,                      -- 剔除/失败原因（不静默）
  trial_cmd     TEXT,                      -- 可复现命令（含 --data-db/--bundle/--today 等）
  created_at    TEXT NOT NULL
);
CREATE INDEX idx_exp_rank ON experiments(grid, stage, status, metrics_json);
```

**状态机与门禁（每条对应一个失败出口，不许静默）**

1. `failed`：会话 `error != null` 或 `gates.ok == false`（`runner.check_session_gates`）；
2. `excluded_zero_trade`：`summary.total_trades < min_trades`（纪律 ③）；
3. `invalid_nan`：**指标有限性门禁** —— 递归 `math.isfinite` 扫 `metrics_json`
   （沿用 `spikes/rqalpha_ma_backtest.py:63-74 _walk_finite()` 的已验证实现），
   命中即 `status=invalid_nan`、`metrics_finite=0`（纪律 ⑥，rqalpha 越界只吐 NaN）；
4. `ok` 才进排名；排名键与现实现一致：`(excess_pct, -mdd_pct)`（`optimize.py:184`）；
5. **可复现性证明**：`exp_id` 由三个 hash + `engine_ver` 合成；重跑同一 `exp_id` 必须
   得到逐字段相同的 `metrics_json`，否则记 `data:exp_drift` 告警（这是
   `provenance.py:1-5` "同一 hash ⇒ 同一实验，指标相同是可验证推论"的落地）；
6. walkforward 的产出要**同时**存样本内第 1 名在样本外的表现与样本外最优（见 §6 的缺陷）。

## 5. 实施步骤（每步可独立验收，建议顺序）

| 步 | 内容 | 验收 |
|---|---|---|
| S1 | `run_rq_trial(cfg, rules, start, end) -> metrics`：把一次 rqalpha 会话包成"一个变体的评估"（复用 `runner.run_session` + `_SessionState`，一 trial 一进程） | 同一变体两次运行 `metrics_json` 完全相同；0 笔变体被标 `excluded_zero_trade` |
| S2 | `grid_search` 的循环体切到 S1，加注册表写入（`experiments`）+ 有限性门禁 | 网格结果与旧 `Backtester` 路径**同序**（S 级判据：变体排序一致率 ≥95%，未对齐逐条归因，沿用 T19 的 S 级方法） |
| S3 | `walk_forward` 切到 S1/S2，并**修 §6 缺陷**：显式记录"样本内第 1 名的样本外表现"与"样本外最优"两个数 | 输出含 `in_best_oos_excess_pct` 与 `oracle_oos_excess` 两个不同字段 + 过拟合差值 |
| S4 | 面板/CLI 增加实验列表与指纹展示（`cli.py:2033` 已有 `fingerprint` 单例展示，扩到研究线） | 每条研究结论都能报出 `exp_id` + `data_source` + `trial_cmd` |

**不做**：不引入 rqalpha 之外的研究框架（backtesting.py/AGPL 有许可证阻断，T6/缺陷归因已论述）；
不为了"框架原生"把排名/剔除逻辑塞进 rqalpha mod（那会让判定回到我们手里，违反 §5.4 的意图反转）。

## 6. 映射过程中发现的现实现缺陷（P1，不改代码，只报）

`walk_forward()` 的文档承诺"样本内第一名与样本外第一名的差距就是过拟合的量度"
（`optimize.py:215-218`），但代码里：

- `optimize.py:249` 先把 `oos_rows` **按样本外超额重排**；
- `optimize.py:251` `oracle = oos_rows[0]`、`optimize.py:252` `picked = oos_rows[0]` ——
  两者是**同一行**，且 `picked` 在返回值里**从未被使用**（`optimize.py:254-267` 无该键）；
- 返回的 `oracle_oos_excess`（`optimize.py:264`）因此只是"样本外最好那个候选的表现"，
  **不是**"样本内第 1 名在样本外的表现"——而后者才是过拟合量度。
  排名信息只在 `out_of_sample[]` 列表里以 `in_excess_pct` 形式并存（`optimize.py:243`），
  需要读者自己去对照，面板/结论没有给那个差值。

**建议修法**（给主 AI，我不动手）：在重排前留存样本内第 1 名的 `tag`，
显式产出 `in_best_oos_excess_pct`、`oracle_oos_excess_pct`、`overfit_gap_pct = 后者 − 前者`，
并把 `picked`（未使用变量）删掉。这条与 S3 合并做最省。

## 7. 出处自检

- 现代码：`optimize.py:93-107/125-202/205-267`、`backtest.py:388-397`、
  `provenance.py:1-13/48-91`、`cli.py:1420/1432-1433/1907/1978-1980/2033`、
  `AGENTS.md:166/172/184/186`。
- rqalpha 6.4.2：`main.py:50-64/133-253/301-320`、
  `mod/rqalpha_mod_sys_simulation/mod.py:59-62`、
  `mod/rqalpha_mod_sys_analyser/mod.py:144-156/203/490/513/515/523/545`、
  `utils/concurrent.py:40`、
  以及"全库无 optimize 设施"的 grep 结果（本轮实测：`grep -rln optimize` 零命中）。
- 交叉引用：`report-t13.md` §4.6（窗口夹掉）/§4.7（进程内重入）；
  `report-t19.md`（S 级排序一致率方法）；`report-t29.md` §3 / `report-t32.md` §4（费用差＝过户费）；
  `spikes/parity_t9.py`、`spikes/signal_gate_t19.py`（可复用的对照与门禁实现）。

## 8. 我不做的事

不改 `paper_trading/**`（本任务纯设计，分支只含这份文档）；不建 `experiments` 表；
不动 cron/部署；§6 的缺陷只报告不动手，等主 AI 决定是否与 S3 合并修。
