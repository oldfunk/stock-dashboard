# report-t71.md — 决策时点门边界 + T+1 对齐 fail-closed（Qoder · 2026-11-05）

派发：`docs/WORKER.md` Batch 17 · T71 两条 + `report`；同日一并落地
主 AI 11-04 批复里"已批未做"的 **Q1 / Q4 / Q5**（批复原文见
`docs/worker/questions.md` §2026-11-04 主 AI 批复）。分支同 report-t73，未合 main。

## 1. 决策时点门边界测试（`tests/test_decision_gate_t71.py`，5 例）

门本身：`_SessionState.decide(act, now)` = `wait` if `now < decided_at`；
日内续跑会重放当天全部 bar（`core/executor.py:64-67` 只跳 before_trading 序列），
没有这道门就会把 16:49 的决定成交在 09:31 那根 bar 上（P0-2 原貌）。

派发点名的两个改坏方向都各有一条测试挡着：

| 测试 | 挡的是什么 |
|---|---|
| `test_decide_boundary_equal_minute_is_go` | `now == decided_at` 的边界（等一分钟算不算到点）；解不出时刻不许变成"永不执行" |
| `test_post_close_intent_never_fills_same_day` | **当日强交**：15:09 的决定在 09:31–15:00 的 bar 上一根都不交，且 `waiting` 只记一条（逐 bar 累积 240 条的归因表没法看） |
| `test_intraday_intent_submits_at_first_bar_after_decision` | **永不执行**：14:50 的决定必须在当天第一根 ≥14:50 **且有 bar** 的分钟交出去；之后的重放不能再提交第二笔 |
| `test_no_bar_after_decision_means_waiting_not_silent_drop` | 决定后当天再没 bar ⇒ 保持 WAITING（不静默丢、也不硬凑一根） |
| `test_cross_day_post_close_intent_current_caliber` | 跨日现状：`expire_stale` 按 `created_at` 的**日期**过期 |

跑法不需要 rqalpha：`_fake_rqalpha` 用 `monkeypatch.setitem(sys.modules, ...)`
把 `rqalpha.api.order_shares` 换成记数桩（与 T68 的 `test_m3_takeover.py` 同一手法，
作用域自动还原）。

### 现网冲突（已提 **Q6**，我不擅自改语义）

Batch 17 的话是"盘后入队 → 当日全 WAITING → **次日** bar 提交（转次日语义）"，
但 `queue.expire_stale` 的口径是"决策只在当日内有效，隔日补执行不可归因"。
现网实况：`002192_SELL_100@2026-11-05T15:09:20`（`pending()` 里
`decided_at == created_at == 15:09:20`）今天所有剩余跳都在 15:00 之前的 bar 上 ⇒ 全 WAITING，
明天 09:02 那一跳 `expire_stale(db,"2026-11-06")` 会把它 expired ⇒
**Batch 17 里"明天开盘验证执行"这条不会发生**。

两个口径都能自洽（过期=信号已失效；转次日=成交价仍晚于决策时刻，归因没破）。
我的倾向：**盘后（收盘后）做出的决定转次日开盘执行，盘中做出的决定当日过期**——
按 `decided_at` 的**时刻**分，不按日期一刀切；但这改的是交易语义，等裁决。
测试已经把两端钉住，裁决下来只需翻 `test_cross_day_post_close_intent_current_caliber`
一条（以及 `rq tick` 里 expire 的调用时机），其余四条不动。

## 2. state 重建的 T+1 对齐（比"重建后跑的工具"更硬的形态）

派发的原话是"写对齐工具（重建后跑）+ 测试"。我做成了**会话内自动对齐 + 两道闸**，
理由是同一件事交给"记得跑的工具"必然会有人忘：

1. `runner.t1_freeze_plan(frozen, framework)`（纯函数）：账本 `available<total` 的
   冻结量 → 该写哪些仓的 `_non_closable`，**只加不减**（调小等于解锁 T+0 卖出，
   多冻一天只是漏机会，两者代价不对等），封顶在持仓量。
2. 时机（`mod_entry._apply_t1_freeze`）：`POST_BEFORE_TRADING`（新会话，必须在框架
   `Position.before_trading()` 把 `_non_closable` 清零之后——`portfolio/position.py:248`）
   + `POST_SYSTEM_RESTORED`（续跑跳，`executor._ensure_before_trading` 当天不再发盘前序列）。
   旧写法在 `start_up` 里读 `env.portfolio`（那时 Portfolio 还没建，`main.py:147` vs `:174`），
   异常被 `except` 咽成 warning ⇒ 对齐从未生效。
3. **fail-closed**（T72 的审点）：
   * 有仓要冻却找不到可挂的事件 ⇒ mod 直接 `InitPositionError` 拒开；
   * `check_session_gates(..., init_frozen=…)` 新增 `T1FreezeMissing`：账本冻结在会话
     留痕里没有 ⇒ 本会话**不落账**、`rq tick` exit 1；
   * 框架仓比账本少 ⇒ 在 `POST_SYSTEM_INIT` 与 `POST_SYSTEM_RESTORED` 都拒开
     （重建/续跑两种入口都堵）；
   * 留痕进 `evidence.frozen_align`（含 `closable`），四场景 e2e 的 A 场景就断这个。
4. 需要"挪开旧状态"的运维动作由 `ledger reset-state --only rq_session_state` 承担，
   并且本分支起它会探 `var/rq_state/live`（11-05 改成固定 live 目录后，原来只探
   `<日>` 的工具会谎报"没有要清的"）。

## 3. Q1（批复"改"）当日预算轮内现读

`engine/runner.py` 在循环外读一次 `budget = self.state.budget(day)`，同轮多条规则
都对着 `orders=0` 判定 ⇒ `max_orders_per_day` 实际成了"每轮 N 单"。改成每条意图现读
（`bump_budget` 本来就每单都写状态）。

变异验证（不是嘴上说说）：把那一行改回快照后
`tests/test_engine_behavior_t68.py::test_daily_budget_applies_inside_the_same_round`
**变红**（日志实测 `BUY 100 600036` + `BUY 100 600519` 两笔都成交），改回来 13 passed。

## 4. Q5（批复"给"）raw_gate 母库参照滞后分档

`utils/m3_verify.waiting_grade()`：`≤2 green / >2 warn / >5 red`，
键在 `WAIT_GREEN_MAX=2` / `WAIT_RED_MIN=5`；
按**单只最大滞后天数**判，不用合计（合计会把"19 只各滞后 2 天"和
"1 只滞后 38 天"压成同一个数，那正是壁纸化的成因）。
green 也必须在 detail 里把天数与只数写出来——绿不等于没事，是"这事不到阈值"。
测试 9 例：6 档参数化（0/2/3/5/6/8）+ green/warn/red 三条走真 `check_raw_gate`。

批复文字里"0/2/8 三档"与"≤2 green / >2 warn / >5 红"不完全一致，我按后者实现、
前者当测试取样点（0/2/8 三点都在），如按别的数取阈值只需改两个常量。

## 5. Q4（批复"跑"）2×2 归因缺格补齐

只补没测到的那一格（1 次云端请求，直调 `provider._request`、账本副本、不写 op_log；
`paper_account.db` md5 前后同值 `d3d98da6…`）：

| 上下文 | max_tokens | 耗时 | 真实 usage | finish |
|---|---|---|---|---|
| 大（97,910 字符） | 4096 | 82.4s | — | stop |
| 大（45,364 tok） | **1200** | **66.5s**（新） | prompt 45,364 / completion 512 | stop（812 字） |
| 小（24,312 字符） | 8192 | 74.9s | prompt 11,440 / completion 673 | stop |
| 小（11,440 tok） | 1200 | 28.3s | prompt 11,440 / completion 642 | stop |

**这条数据修正了 T63 的说法**：当时只有三点，我写的是"上下文瘦身有效但不是主因"
（82.4→74.9 只差 9%）。固定输出预算在 1200 时，大/小上下文差 **66.5 vs 28.3 = 38.2s
（-57%）**；大预算下差异被输出时间掩盖了。所以两个变量都是真的，
**而且上下文的影响在小预算下才被暴露出来**——不是"耗时主要跟 max_tokens 走"。
每格 n=1（同一条云端链路、同一时刻窗口），要拿它做默认值决策的话得重复几轮；
`ask_max_tokens` 缺省仍保持 1536 不动（口径未变，等主 AI 定）。

## 6. 回归

`tests/`：806 passed / 11 skipped（新增 19 例：T71 5 + T73 单测 15 里的 CI 部分
+ Q5 9 + Q1 1，e2e 在无 rqalpha 环境 skip）；`wiring_audit` 退出码 0。
