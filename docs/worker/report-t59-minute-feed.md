# T59 · 盘中分钟供给闭环（2026-11-03，Qoder）

派工原文（Batch 13）四条：① 主 AI 已装 `6,21,36,51 9-15` 的 `deploy/m3_minute_intraday.sh`；
② 验证 `minute_feed`/`heartbeat(minute)` 回绿、tick 日志出现自证；③ 复核
`ensure_today_minutes()` 触发路径与 `--intraday` 的 bar 时间戳语义；④ 若 15 分钟粒度
不足 → 评估加密到 5 分钟。**结论：粒度够，不用加密；但这条供给链上查出 5 处缺陷，全修了。**

分支 `worker/t59-t60-caliber`（与 T60 同一根因，故同批；**未合 main**）。
现网工作树已在该分支 tip；`paper_account.db` 未被本次改动写过（所有落账验证在库副本上跑）。

## 1. 逐条核销

| 派发条目 | 状态 | 现网证据（11-03，NEC） |
|---|---|---|
| ② `minute_feed` 回绿 | ✅ | 12:19 `m3 verify`：`绿 7 / 警 5 / 红 0`；该行 `✅ 分钟线供给（新线命脉）：今天 960 行 / 40 只；最新 2026-11-03T11:30`（部署前同一行是红字） |
| ② `heartbeat(minute)` 回绿 | ✅ | `var/heartbeat.json`：修之前 `"ok":false,"detail":"written=429 errors=920156"`；修之后 `"ok":true,"detail":"written=0 errors="`（已齐 ⇒ 一次接口都不打） |
| ② tick 的自证 | ✅ | `rq_tick.log` 存在且每 5 分钟追加（14.5 KB，11:47 一跳 `"idle"`）；心跳 `rq_tick` 有键；`--json` 里 `minute_feed` 字段就是自证，另加 `instrument_unavailable`（见 §2-C） |
| ③ 触发路径复核 | ✅ 两路同口径 | cron `minute sync --intraday` 与 `rq tick` 都走 `ensure_today_minutes()` ⇒ 同一个 `completed_slots()`（**严格早于当前分钟**才算收完）⇒ 不会把在走的半根 bar 落库 |
| ④ 15 分钟 vs 5 分钟 | **不用加密** | 钱类路径不依赖那条 cron：`rq tick` 每 5 分钟一跳且本跳自采。最坏推演：决策 11:16 ⇒ 需要 11:20 那根 ⇒ 11:21 的池级采集或 11:22 的 tick 自采都会带上，延迟 ≤ 一根 bar。改 5 分钟只把新浪请求量翻三倍（40 只 × 12 次/时），收益为零 |

## 2. 查出来的缺陷（5 处，全部已修）

### A. 北交所 920 段在五个调用点各判一遍市，且全判错

`920156`（海昌智能，11-02 入池）在旧写法里 ⇒ 新浪分钟线 `sh920156` **0 行**、
腾讯实时 `sz920156` 空字段、rqalpha `920156.XSHG` 挂错所、涨跌停带按主板 **10%**
（北交所 30%）、akshare 日线 `KeyError 'date'` → 东财又被断连 ⇒ **raw 日线 0 行**。
同一时刻实测三口只认 `bj`（新浪实时 39 字段 / 腾讯 87 字段 / 新浪 5 分钟 48 根）。
收敛到 `utils/markets.py` 一张表后：`fetch_daily_raw("920156")` 回 10 行、
盘中采集 `written=23 latest=11:25 missing=[]`。细节与代价见 `report-t60-raw-gate.md §2-A`。

### B. 盘中采集把同一症状刷成审计流水（一上午 9 条，还在长）

`minute sync --intraday` 每跳失败就落一条 `op_log[minute:gap]`；11-03 09:30–12:19 已
**9 条**同一症状（都是 A 的那只标的）。现在按 `gap_fingerprint(day, errors)` 去重：
**同日同症状只落一条，症状集合一变立刻再落**（与 T22 看门狗"只在翻转时落流水"同一套）。
心跳仍每跳都写，采集器停了照样响。

### C. 标的不在 bundle 里 ⇒ 意图"静默等待"到次日过期

`instruments.pk` 28,063 只里 **`.BJ` 为 0**（`stocks.h5` 也只有 5,537 只沪深）
⇒ 北交所标的新引擎**天生买不了**。旧路径：`bar_dict[ob]` 抛异常被当成"这分钟还没 bar"
跳过 ⇒ 240 根重放完一单没提 ⇒ 意图 `queued` 挂到次日被 `expire_stale` 清掉——
机会丢了、原因不可见（`IdleSpin` 只说"静默失败"，不说是为什么）。

`mark_unavailable()`：会话问一次标的表，不在的记 `INSTRUMENT_UNAVAILABLE` + 中文原因，
归因侧判 `rejected`（规则次日可重触发）；**问不到表就返回 None 并什么都不判**。
现网库副本实测（真会话，12:0x）：

```
920156_BUY_100@2026-11-03T11:20:00 | rejected
{"reason": "bundle 里没有 920156.BJ（北交所/未收录新股？）——新引擎买不了，规则可重新触发"}
```

这条修复本身又被现网照出两处我自己的错，都记在提交里：
① 定局最初挂在 `before_trading`，而日内续跑会话**不跳** `before_trading`（T13 §4.7）
⇒ 改挂到第一根 bar；② `if not known` 把"问不到表(None)"与"问到了、全都不在(set())"
混成一件事 ⇒ 单只北交所标的的会话一条都不定局，空集合恰恰是本次要处理的那一幕。

### D. `IdleSpin` 在已经解释清楚的事情上喊"静默失败"

C 修好后 `gates` 仍报 `IdleSpin: 计划有下单，但既无成交也无拒单原因`——因为框架那侧
确实一单没提。报警失真比不报更糟：`check_session_gates(..., settled_biz=)` 把会话自己
定过局的单子摘掉，没给结论时照样响（两头都有用例）。

### E. 分钟线陈旧度按墙钟算 ⇒ 整个午休每小时假红一次

12:09 现网红字 `最新 11:30；盘中陈旧 40 分钟`——11:30 收午盘后库里停在 11:30 是正确的。
新增 `minute.latest_expected_ts()`，陈旧度改成"**此刻该有的一根** 减 **库里最新一根**"：
午休绿，午后真断采（13:11 该有 13:10、库里还在 11:30 ⇒ 滞后 100 分钟）照样红。

## 3. 门禁与可复算性

- 新增/改写用例：`tests/test_market_caliber_t59.py`(13) + `tests/test_minute_supply_t59.py`(10)
  + `tests/test_m3_takeover.py` 追加 6（会话定局/None-空集合/午休绿/午后红/时钟函数/tick 定局）。
- `spikes/mutation_t59.py`：**12 条变异 12 条捕获**，基线绿。
- 全套 **678 passed / 9 skipped**（本地）。
- 部署可回退：crontab 备份 `/home/nec/crontab.pre-t60-20261103-112629.bak`、
  `/home/nec/crontab.pre-t60b-*.bak`；代码回退 `git checkout 1562c8a`。
- 顺手清掉一条被 `--symbols \` 拼坏的重复 cron 行（每 15 分钟跑一次垃圾参数，与脚本行重复）。

## 4. 需要拍板（一件）

北交所标的：bundle 里根本没有 `.BJ`，所以**池里有它们 = 每天重复"触发 → 当场 rejected"**。
两条路请选一条（我这批没动池子，也没拉白名单）：
① 换带 `.BJ` 的 bundle（`stocks.h5` + `instruments.pk` 都要补，属数据侧工程）；
② 在股票池/`universe` 侧排除非沪深标的（`000426` 之外的 `920156` 走这条），
   并让"入池即校验可交易性"成为规则（`data` 侧新增标的时先验料）。
