# Day-1 P0 复核跟进（2026-11-04，Qoder）· 无新批次，自主迭代

主 AI 修了两个现网 day-1 P0（`d1a487d`）并合了我的 T67/T68（`58ec7b8`）。
没有新批次下发，本轮做的是**跟进**：给那两个修复补回归测试、复核同一条分支上
是否还有别的漏项、并把 T60 留下的可证伪判据验掉。

## 1. T60 的可证伪判据：**成立**（task #30 结）

判据写在 `report-t60-raw-gate.md`：如果 11-04 早上的采集能拿到 11-03 的 raw 日线，
"15:40 采集早于数据源发布 T 日日线"这条归因就被证实；拿不到就说明归因错了，
要回到母库 `close_raw` 供给本身重查。

现网只读核对（`/home/nec/paper-trading/data.db`，11-04 傍晚）：

| 检查 | 结果 |
| --- | --- |
| `daily_bars_raw` 最大日期 | **2026-11-03**（今天 11-04 那根要到 20:40 那班才该有 ⇒ 与归因一致） |
| 11-03 覆盖标的数 | **40 / 40**（池子 40 只） |
| 10-26 起逐日 | 连续 7 个交易日都是 40/40 ⇒ 没有逐标的滞后残留 |
| `m3 verify` | 绿 9 / 警 3 / **红 0**，`rollback_required=False` |

三个 warn 逐个看过，都不是失真：`parity_trace`（影子对账 cron 仍未装，Batch 13 遗留第 3 件）、
`heartbeat`（非应跳时段，工具自己会免责）、`raw_gate waiting_days=20`
（逐日闸的**母库参照**有 20 天还没出数，`audit_complete=True` 且 `incomplete_days=0`；
我们的 raw 侧自己是全的，报的是"参照没到"而不是"我们缺料"）。

派发当初说的"5 只未翻转"确实是 `stale[:5]` 截断造成的误读，真实形状是全池缺 11-02 那根。

## 2. `d1a487d` 的修复没有测试 ⇒ 补 7 例（`tests/test_m3_day1_p0s.py`）

形状统一是"**新底座分叉出去的那条分支，漏了旧分支上的某个副作用**"。没有回归测试的
钱类修复只修今天：下一条新路径、下一次重构会原样再犯。

| 锁的东西 | 用例 | 备注 |
| --- | --- | --- |
| 入队即 `mark_order`（P0-1） | `test_rq_enqueue_marks_the_symbol_so_the_rule_does_not_reflood` | 冷却必须用现网值 900 才测得到——`cooldown=0` 时那道判断 `if last and cooldown > 0` 直接跳过，**这条防线本来就不存在**（写测试时才看清，注释里记下了） |
| rq 分支不碰账本（纪律 1 边界） | `test_rq_fork_does_not_touch_the_ledger` | 只排队，`orders/fills` 必须为 0 |
| **入队累加预算（本轮新发现）** | `test_rq_enqueue_consumes_the_daily_order_budget`、`test_rq_enqueue_counts_turnover_toward_the_daily_cap` | 见 §3 |
| `rq-nav` 让位看 `nav_history`（P0-3） | `test_rq_nav_yields_to_history_rows_without_op_log` + 反向 `..._still_records_when_history_is_empty` | RunDaily 那种"写净值不留 op_log"的形状 |
| 让位检查的失败姿态 | `test_nav_history_helper_is_fail_open_and_uses_a_read_only_uri` | 查不到按"没有"（宁可多记不可断供）；且必须 `mode=ro`——纯检查函数不该有机会改账 |

## 3. 复核照出来的第三处漏项：`bump_budget`（已修）

`d1a487d` 给规则引擎的 rq 分支补了 `mark_order`，但同一条分支仍然**没有**
`self.state.bump_budget(...)`，而：

- 旧路径（`submit_order` 之后）是 `mark_order` + `bump_budget` 两个一起做；
- 条件单的 rq 分支在 `ok` 之后是有 `bump_budget` 的（`runner.py:584`）；
- 两边共用同一个 `engine_state.budget:<日>` 计数器。

⇒ 在 `executor=rqalpha` 底座下，规则引擎驱动的报单**永远不占用日内预算**，
`max_orders_per_day` / `max_turnover_per_day` 这两个限额形同虚设。
这正是 T68 行为级断言该抓的那类（静态读点看不见：键有读点，只是那条分支不写它）。
已补 `bump_budget(day, 1, amount)`，语义按 config 注释的"**报单**笔数上限"取入队时刻，
估算金额=决策价×数量；两条测试先跑红再修绿的形状在注释里写清了原因。

## 4. 顺带记录、但**没动**的一处两路径共有缺陷

`EngineRunner.tick()` 在**每轮开头读一次** budget 并把同一个 dict 传给本轮所有规则：
一轮里两条规则都能过限额检查（第一条 bump 了库里的值，本地副本没刷新）。
这不是 rq 分支引入的——旧路径同样如此，且修法会把限额**收紧**（同一轮内第 N 单开始被挡）。
收紧限额会改变现网行为，属于口径，不自己拍：写进 questions，让主 AI 决定改不改、
以及是"重读预算"还是"就地累加本地副本"。

## 5. T62 还挂在未合并分支上（今天第一次真用它就照出东西）

`git merge-base --is-ancestor ada5279 origin/main` ⇒ **False**：
`worker/t59-t60-caliber` 只合并到 `54642a6`（T59/T60），T62 的 6 个提交
（`d635d60..ada5279`）不在 main 里 ⇒ 现网没有 `ledger reset-state`。

我把 T62 分支代码导出到 `/home/nec/tmp/t69`，对**今天**的账本副本跑了一次 dry-run
（只读，生产 md5 未动）：

```
🔴 intent_ledger_conflict 20 项
   002192_BUY_100@2026-10-30T16:45:25 标 rejected 但成交 #13 002192 BUY 100@44.94 仍在账
   002192_BUY_100@2026-10-30T16:49:24 标 rejected 但成交 #13 ...
   300390_BUY_200@2026-11-04T09:54:02 标 rejected 但成交 #14 300390 BUY 200@34.61 仍在账
   300390_BUY_200@2026-11-04T09:58:03 / 10:03:03 / 10:34:02 ... 同上一笔成交
钱类对照（本命令不碰）：cash=60495.89 持仓股数=1000
   行数={'account':1,'positions':7,'orders':14,'fills':13,'nav_history':18,'t1_freeze':10,'op_log':407}
```

两件事：① 它**独立印证**了 d1a487d 的成因——11-04 那 18 条重复意图全部标着 `rejected`
却都指向同一笔成交 #14，与主 AI 描述的"18 条 fake-executed 无成交"是同一堆残骸的
两个方向；② 11-03 我上报要裁决的 `t1_unfreeze_overdue`（002192 冻结股卖不掉）
今天已自行消失（结算路径跑到了），说明当时的判断是对的：那不是要清的状态，是要等的流程。

这批"被改判 rejected 却仍有成交"的意图行**留着比删掉更有价值**（它是 P0 的物证），
所以我没有清它，也不建议清；要清的话 `reset-state` 会逐条写原因进 `op_log`。

## 6. 交付物与验证

- `paper_trading/engine/runner.py`：rq 分支补 `bump_budget`（一处，带原因注释）。
- `tests/test_m3_day1_p0s.py`：7 例（含两条"限额真的挡单"与一条"被挡下不占预算"）。
- 全量 **745 passed / 9 skipped**；`tests/wiring_audit.py --quiet` → 断层：无。
- 生产侧：本轮只做了只读查询（`m3 verify`、raw 覆盖、账本副本 dry-run）；
  `paper_account.db` md5 与 crontab 未变，未部署、未合并。

---

## 7. T69（Hermes 审查）回执

Hermes 独立审 `d1a487d`，两条建议与我这轮重合（budget 记账、补"同日只入队一次"断言），
另外两条我按它的口径补了：

| T69 建议 | 处置 |
| --- | --- |
| `_nav_history_has_today` fail-open「money-class 略松，建议至少 warn」 | ✅ 改为 warn 后再回 `False`（行为不变：宁可多记不可断供；但让位失败从此有声音）。测试 `test_nav_history_helper_is_fail_open_and_uses_a_read_only_uri` 同时钉住"只读连接"与"必须 warn" |
| LIKE 前缀匹配 `timestamp` 的格式一致性 | ✅ 复核过：写入侧统一是 `datetime.isoformat()`，`{today}%` 前缀可命中；不改成 `substr` 是为了少一处口径分叉 |
| 同形问题顺手排查 | 冷却时间戳解析失败原先是 `except: pass` —— 那正是 11-04 P0（同规则每分钟重灌）**唯一那道闸**的输入。坏时间戳会让闸门静默打开，已改成 warn + 放行（宁可放行也不误拦，但必须留声音），配 `test_corrupt_cooldown_timestamp_warns_instead_of_silently_opening_the_gate` |

本轮之后：`m3 verify`/`reset-state` 之外，钱类路径上**再没有"读失败=当没这回事且不留声"**
的两处（其余 40 个静默 handler 集中在行情回退、心跳、日志与 UI 侧，逐个看过不改是对的）。
全量 **746 passed / 9 skipped**，门禁 断层：无。

