# report-t50 · 盘后跨源防线修复（Qoder，P0·Batch 9 裁决 1 的 (a)+(c)）

分支 `worker/t50`（基于 `origin/main@085e318`，**待 review 未合并**）。
生产口径 venv 全套 **515 passed / 9 skipped**（基线 499/9 ⇒ 本任务新增 16 条，零回归）。
**全部判据都在 NEC 现网真实数据 + 真实腾讯快照上验过**（19:32 北京，写 scratch 副本，现网库未动）。

## 0. 一句话结论

(a) 先修：盘后 19:32 采集，**当日跨源对照从 `checked=0 / missing=38` 变成 `checked=38 / missing=0`**，
38 只标的的今天那根全部拿到独立参照（`daily_bars_raw` 今日行 checked=1 从 **0 → 38**）；
(c) 后加：当日没有独立参照的 bar **不再进引擎**——实测抹掉 600036 的当日参照后，
新线只供 000651、600036 的订单被框架以"没有市场数据"挡下，
`evidence.gate_rejected = {"600036": "today_bar_unchecked"}` 可审计，
而旧线照买 ⇒ 影子 `position_delta=-1000` 把这次"挡"显式照出来。
**顺序不可反**这条派发文是对的：先做 (c) 会让每天 15:35–母库更新之间全线停摆。

## 1. 裁决逐条核销清单（`docs/worker/questions.md` 批复 "四裁决" 第 1 条 + Batch 10 派发原文）

| 拍板原文 | 状态 | 落地点与证据 |
|---|---|---|
| 裁决 1：「**(a) 盘后专用新鲜度（收盘快照可用——realtime 收盘后 return {} 与 180s 新鲜度是自断防线）**」 | ✅ | `realtime.py`：新增 `QUOTE_CLOSING_MAX_AGE=9h`/`CLOSE_MINUTE` + `is_closing_snapshot()`（当天 + ts≥15:00 + 时龄在窗口内，**跨日自动作废**）+ `quote_usable(..., allow_closing=)`；`get_quotes(..., allow_closing=False, now=None)` 默认行为一字未改（撮合/风控路径仍走 `quote_fresh` 180 秒）；`raw_feed.intraday_reference()` 默认入口带 `allow_closing=True`。真机：`intraday_checked 0→38`、`intraday_missing 38→0` |
| 裁决 1：「再 **(c) 引擎取数口 `source.py` 查 checked fail-closed（当日无参照的 bar 不喂引擎）**」 | ✅ 两处都装 | `raw_feed.today_reference_state()` 三态；`source.engine_raw_bars()`（文档上的唯一取数口）返回 `[]` + `last_engine_reject="today_bar_unchecked: <date>"`；**并且装在 `RawDayBarStore.from_data_db()`**——见 §2，那才是真正喂 rqalpha 的路 |
| 裁决 1：「(b) 采集挪盘中否决」 | ✅ 未做 (b) | 未改任何采集时刻；批复 ③ 的 ≥15:35 保持 |
| 派发：「回归测试（盘后采集能对照、无参照 bar 进不了引擎）」 | ✅ | `tests/test_postclose_defense_t50.py` 15 条：`test_post_close_collection_then_engine_serves_the_bar`（(a)→(c) 因果链）、`test_engine_refuses_today_bar_without_independent_reference`、`test_store_drops_symbol_without_reference_and_records_reason`、`is_closing_snapshot` 四种错形状参数化、窗口跨日作废、`get_quotes` 双路径对照等 |
| 派发：「+ `report-t50.md`」 | ✅ | 本文件 |

## 2. 顺手撞破的一件事：**"引擎唯一取数口"是纸面的**

`BarSource.engine_raw_bars` 的 docstring 写着"引擎唯一允许的取数口"，
但真正的引擎喂料路径 `RawDayBarStore.from_data_db()`（`store.py:95`）**直接调
`db.get_raw_bars()`**，绕过这个口——全仓除测试外没有任何生产代码调用 `engine_raw_bars`
（`grep engine_raw_bars` 只有定义 + 3 条测试）。

⇒ 如果我只按派发文在 `source.py` 加检查，(c) 会**完全不生效**。
所以两道闸都装：`source.engine_raw_bars`（保持文档承诺为真）+ `RawDayBarStore.from_data_db`（真正起作用的那道），
并把被挡的标的记进 `store.gate_rejected` → `mod_entry` 写入 `evidence`，
这样"今天这只为什么没成交"在账本外面也能查到，不是一句"没有 bar"。

## 3. 真机验证（NEC 现网数据，scratch 副本；命令与输出都可复算）

```
① 盘后采集（19:32 北京，真实 qt.gtimg.cn）
   跑前：today(2026-10-29) 38 行，checked=1 的行 0
   written=28339  checked=28302（跑前 28264，+38）  no_reference=37  拒收=0
   当日跨源: enforced=True checked=38 missing=0 no_today_bar=0        ← 修前是 0/38
   flip: stop_line=False unflipped_days=0 waiting_days=37 audit_complete=True
   跑后：today 38 行，checked=1 的行 38

② (c) 引擎侧
   有参照：成交 2 笔、两侧费用 20.9352 == 20.9352、fee_delta=0.0、gate_rejected={}
   抹掉 600036 当日参照后：
     store_stats 只剩 000651；evidence.gate_rejected = {"600036": "today_bar_unchecked"}
     新引擎成交 ['000651.XSHE']；600036 被框架拒："当前合约[600036.XSHG]没有市场数据"
     旧引擎成交 ['000651','600036'] ⇒ diff.position_delta={"600036": {rq:0, legacy:1000, delta:-1000}}
     影子对账 exit=1（**这是预期的"挡"被照出来，不是新 bug**）
```

**测试**：新增 15 条 + 既有 `test_engine_raw_bars_returns_fresh_raw` 按新语义补 `checked_dates`
（它原本验的就是"无参照也放行"这个旧行为）；`spikes/mutation_t50.py` 四个源码级变异
（①盘后不放行 2 红 / ②intraday_reference 丢 allow_closing 1 红 / ③source 不查 1 红 /
④store 不查 1 红），还原后 54 条全绿。

## 4. 行为变更与代价（影子期/切换要知道的三件事）

1. **当日采集失败 = 那只标的当天不下单**（以前会用未验证的价下单）。
   这正是裁决要的 fail-closed，但运维上会把"数据没到"从静默变成停单 ⇒
   cron 的顺序必须是 `fetch-raw(15:40) → shadow-day(15:45) → gate(15:50)`，
   采集失败要在 shadow-day 之前就叫人看见（`data raw-coverage --gate` 已在做）。
2. **窗口取舍**：`QUOTE_CLOSING_MAX_AGE=9h` 是"当晚还能补跑采集"的余量
   （实测腾讯收盘定稿 ts=16:14，19:32 采到时龄 3h18m）。跨日一定作废——
   不能拿昨天的收盘价冒充今天的独立参照。要收紧到"只允许 15:00–24:00"其实就是现在这个语义。
3. **`require_today_reference` 进了会话指纹**：开/关会改变账本语义（G-T13-5），
   所以同日换档会被 `SessionGate` 拒（需要 `--force`，而 `--force` 又意味着重复成交——
   排障时请换新 state 目录，别用 --force）。

## 5. 遗留 / 请主 AI 定

- **母库自身的近端滞后**仍在：38 只里 18 只的母库最新到 10-23/10-27，
  这次能验料靠的是**收盘快照**这条独立参照（A 半边），母库那条（B 半边，T+1 翻转）
  还会有 37 行停在 `waiting_reference`。**不是我们的锅，也不停线**，
  但如果母库长期不更新沪市近几天，`checked` 的"双参照"就只剩一条腿——
  这条属跨仓事实，是否转给 stock-dashboard 那边请主 AI 定（我不碰母项目代码）。
- `m3 verify`（T46）现在**看不到**这道新闸：它的 `raw_gate` 只看逐日台账。
  若你希望"当日无参照被挡"也进第一小时清单，我可以加一项
  `engine_gate_rejected`（读 evidence 的 `gate_rejected`）——**要不要加请给一句**，
  我不在 T50 里顺手扩 T46 已验收的交付面。
- 现网 `data.db` 的今日 38 行仍是 `checked=0`（我只写了 scratch）。
  下一次授权采集（或 15:40 cron 装上后第一次跑）就会翻成 checked=1；
  在那之前，新线对"今天"是不下单的（by design）。

## 6. 环境与红线

- 改动只在 `paper_trading/{data/realtime.py, data/raw_feed.py, data/source.py,
  integration/rq_engine/{store,mod_entry,runner}.py}` + 两个测试文件 + 一个变异脚本。
- NEC 上只写 `/home/nec/tmp/*`（代码副本 + scratch 库）；
  **现网 `data.db` 与 `paper_account.db` 未写**（影子跑用 `--no-op-log`，账户库是 scratch）。
- 没跑任何写账本命令；没碰 Pi/secrets/cron/tag；`config.yaml` 只读；没 force push。
