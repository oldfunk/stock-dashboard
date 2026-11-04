# report-t41 · raw 管道加固（Qoder，P0·Batch 9 批复 2）

分支 `worker/t41`（**待 review，未合并**）。基线 `origin/main@b5983f6`。
生产口径 venv（无 rqalpha）全套 **470 passed / 9 skipped**（基线 453/9 ⇒ 新增 17 条，零回归）。
全部证据来自 NEC 现网真数据 + 真母库，命令与输出可复算（脚本在 `/home/nec/tmp/pt-t41`）。

## 0. 一句话结论

T39 报的三条缺陷都修了，并且在真机上互相验证：
**同一份旧库（590 行、190 行从没独立验料）在旧的逐日盲区下报 `violations=[]`，
换上 T41 的台账后报出 `unflipped_days=157`、10 只全部停线**；
用 T41 重新采集同一窗口则是 **557/590 行验料（94%）、`unflipped_days=0`、gate 绿**，
剩下的 23 行全是"母库自己还没出到那些天"（`waiting_reference`，告警不停线）；
`--no-mother-ref` 现在真的一个字节都不读母库（`checked=0` 且闸口停线，不装没事）。

## 1. 裁决逐条核销清单（`docs/WORKER.md:22` 条款）

对照 `docs/worker/questions.md` 批复节（2026-10-29 主 AI 批复 "T39 三件拍板" 第 2 条）
+ `docs/WORKER.md` Batch 9 派发文本：

| 拍板原文 | 状态 | 落地点与证据 |
|---|---|---|
| "参照窗口对齐：母库参照查询覆盖采集窗口（days*2+10 自然日对应交易日），消灭 590 行只验 400 的漏验" | ✅ | `raw_feed.py:45-53` 新增 `fetch_window()`（采集/参照/审计**同一个**窗口定义）；`mother.py:514-521/537-548` `raw_close_map(..., since=)` 按日期下界取参照（老调用方 `days` 语义不变）；`raw_feed.py:421` `sync_raw` 默认路径传 `since=start`，结果回显 `ref_window`。真机：`checked 400 → 557`（同库同窗口，拒收 0） |
| "`raw_flip_watch` 逐日检查（每个采集日都要 checked 或显式 no-reference 原因），消灭只看最后 checked 距今的 `violations=[]` 盲区" | ✅ | `db_manager.py:357-376` 新增 `raw_date_flags()`（逐日台账底座）；`raw_feed.py:226-360` `raw_flip_watch` 六态分类 + 聚合计数；`raw_feed.py:365-390` `flip_watch_with_mother()` 让 CLI 与 `sync_raw` 同一口径；`cli.py:2026-2046` 输出新增 `audit` 块并纳入 `--gate`。真机：旧库 `unflipped_days=157`、10 只停线（旧实现 `violations=[]`） |
| "`--no-mother-ref` 语义修正：真不读母库（reference=None 不得回退）或改名为实际语义；不许名不副实" | ✅（选了"真不读"） | `raw_feed.py:56-77` `NO_REFERENCE` + `reference_for()`（旗子→参照提供者的唯一映射）；`cli.py:1996-2002` 改用它。真机：带旗 `ref_window.single_source=true`、`symbols_with_ref=0`、590 行全部 `checked=0` |
| "回归测试 + `report-t41.md`" | ✅ | `tests/test_raw_pipeline_t41.py` **17 条**（含四条源码级变异必红，见 §4）+ 本文件 |
| 派发头一句："T41→T40→T42 有依赖顺序（先修管道再回填再挂 cron）" | ✅ 顺序就位 | T40 的输入已在本报告 §6 备好（现网 `daily_bars` 深度 742 交易日/38 只，`--days` 该传多少） |

## 2. 三个根因（都不在原报告的字面上，是读代码读出来的）

1. **窗口单位不同源**：采集起点是 `today - (days*2+10)` **自然日**，而母库参照是
   `ORDER BY trade_date DESC LIMIT days` **交易日行数**（`mother.py:536-539`）。
   同一个 `days=40` 在两边含义不同 ⇒ 老的一半天生没有参照。
   附带第二处：默认 `fetch` 里写死 `date.today()`，**不看 `today` 参数**
   （回填历史日时会拉到错误的窗口）——一起收进 `fetch_window(days, today)`。
2. **闸只测"最后 checked 距今"**：`raw_latest_checked()` 是一个 MAX，
   中段一批从没验料的行既不动最新 bar 也不动最后 checked ⇒ 门禁结构上看不见它。
   所以这不是判据写错，是**判据的形状**看不见这类错。
3. **`None` 的语义被复用**：CLI 用 `reference=None` 表示"不读母库"，
   而 `sync_raw` 里 `None` 的含义是"用默认（读母库）"（main 上 `raw_feed.py:244-250`）
   ⇒ 旗子等于没拉。修法不是加 `if`，是让旗子只能映射到一个显式哨兵。

## 3. 逐日闸的六态（新增的可审计语义）

| 状态 | 含义 | 停线？ |
|---|---|---|
| `verified` | 该行 `checked=1` | — |
| `today_pending` | 就是今天那一根 | 否（§5.5 裁决 2-B 明文允许） |
| `unflipped` | 母库**有**该日参照而我们没重跑 | **是**（我们的锅） |
| `waiting_reference` | 母库还没出到该日（晚于母库最新日） | 否，告警 |
| `no_reference_available` | 母库出了更晚的日子却没有这一天（停牌/母库缺口） | 否，告警但**显式可见** |
| `audit_incomplete` | 参照存在性未知（母库读失败/调用方没给 ref_days） | 否，但 `audit_complete=False` |
| 另有 `raw_stale` / `no_raw_data` / `reference_source_unavailable` | 喂料断了 / 参照整条没回来且窗口内一行都没验过 | **是** |

两条刻意的取舍，写下来免得被当成宽松：
- **母库不可达不冤枉我们，但也不放行裸奔**：窗口内一行都没验过时
  ⇒ `reference_source_unavailable` 停线（钱类数据宁停不单源）；
  只要验到过行（`verified>0`）而最后 checked 落后，仍按 T34 的 `ck_lag` 规则算我们的锅。
  **T34 已验收的判据一条都没放松**（`tests/test_caliber_defense_t34.py` 12 条全绿）。
- `close_raw=NULL` 的母库行**不算有参照**（`_ref_date_set`），否则母库 T 日的 NULL
  会把"我们没跑"误判成 unflipped——这正是裁决 2-A 存在的原因。

## 4. 真机证据（NEC，命令见 `/home/nec/tmp/pt-t41`；报告用的数字都可复算）

```
A 旧库(590 行/190 行未验) + 新逐日闸  → exit=1
  audit: audited=590 verified=400 unflipped_days=157 waiting_days=23 no_reference=0 incomplete=0
  violations = 全部 10 只；000858 reason=checked_not_flipped
               unflipped=[2026-07-31, 08-03, 08-04, 08-05, 08-06, 08-07 …]（母库明明有）
  —— T39 当时同一份库报的就是 violations=[]（盲区消灭）

B 新代码重采同一窗口(--days 40, 10s) → written=590 checked=557 no_reference=23 拒收=0
  ref_window={start:2026-07-31, end:2026-10-29, single_source:false, symbols_with_ref:10}
  flip_watch: unflipped_days=0  stop_line=false  audit_complete=true
  对照：同一库在 T39 的实现下 checked 只有 400

B2 新库过 gate(--gate --days 40)     → exit=0  violations=[]  waiting=10 只（23 天，告警）

C --no-mother-ref                    → ref_window.single_source=true symbols_with_ref=0
  written=590 checked=0（库内 checked=1 行数 0）
  flip: stop_line=true verified_days=0 incomplete_days=580 → 归因 reference_source_unavailable
```

**过程中我自己撞破并当场修掉的缺陷**（这就是"真机跑一遍"的价值）：
第一版 `flip_watch_with_mother` 把 `raw_close_map` 的**外层** `{code: {date: row}}`
当成内层参照表用了 ⇒ `max(v)` 取到的是标的代码而不是日期、`ds in have` 恒假，
真机表现是**双向误判**：母库明明有的未验日不再算 `unflipped`（漏报），
而母库单纯滞后的 7 只标的被 `ck_lag` 规则误判成 `checked_not_flipped`（误报停线）。
已修（`raw_feed.py:365-390`），并加形状回归用例
（`test_flip_watch_with_mother_reads_the_inner_reference_dict`：同一轮里
 000792 该 unflipped 停线、600036 该只告警不停线）。

**变异检查（源码级，改完即还原，脚本 `spikes/mutation_t41.py`，可重跑）**：

| 变异回旧形状 | 结果 |
|---|---|
| ①内层参照表改回外层包 | 1 failed / 54 passed |
| ②参照查询丢掉 `since` | 1 failed / 54 passed |
| ③`reference_for` 永远返回 None | 2 failed / 53 passed |
| ④`unflipped` 分支不算停线 | 3 failed / 52 passed |
| 全部还原 | **55 passed** |

## 5. 输出契约的变化（面板/巡检要跟着改的地方）

- `data raw-coverage` 的结果新增 `audit` 块（`since/audited/verified/unflipped/waiting/no_reference/incomplete/complete`），
  `flip_watch.per_symbol[sym]` 新增 `daily`、`ref_days_known`；**旧键一个没删**，
  `violations/stop_line/warnings` 语义不变（只是现在能看见以前看不见的东西）。
- `data fetch-raw` 结果新增 `ref_window`（含 `single_source`）。
- `--gate` 的失败面变大：**这是有意的**（T39 那批数据今天就该被判不合格）。
  现网 `data.db` 的 `daily_bars_raw` 是 0 行，所以暂时不会有人被误伤；
  T40 回填后必须看到 `unflipped_days=0` 才算完成态。

## 6. 给 T40 的输入（下一步要用，这里先钉住）

- 现网 `daily_bars`（前复权）实测：**27,951 行 / 38 只 / 2023-10-09…2026-10-29，每只 742 行**。
  ⇒ "历史深度与 daily_bars 对齐"= 要拉约 742 个交易日 × 38 只 ≈ 2.8 万行 raw。
- `--days` 现在是**窗口规模参数**（真实交易日数 ≈ `days*2+10` 自然日换算而来）：
  要覆盖 742 个交易日大约要 `--days 550`（`550*2+10=1110` 自然日 ≈ 3 年）。
  这个参数含义含糊，我建议 T40 用显式 `--start/--end`（需要一个小改动）
  或者就按 `--days 550` 跑并如实报告实际深度——**请主 AI 定**，我不擅自加 CLI 参数。
- 母库侧的已知滞后（**跨仓事实，只报告、我不动母项目**）：
  同一轮采集里深市标的（000858 等）母库最新到 **2026-10-27**，
  沪市标的（600036/601318 等）只到 **2026-10-23**。
  ⇒ 影子期这几天会持续看到 `waiting_reference` 告警（不停线，符合设计）。
  如果主 AI 认为沪市滞后是母库侧的同步问题，需要转给负责 stock-dashboard 的对话处理。

## 7. 环境与红线

- 只动本 worktree（`C:\Users\yanzh\AppData\Local\Temp\pt-worker-t41`，分支 `worker/t41`）。
- NEC 上只写我自己的 scratch：`/home/nec/tmp/pt-t41`（bundle 送过去的代码副本，
  不在现网仓库里建 worktree）、`/home/nec/tmp/t41_*.db`、日志 JSON。
  **现网 `data.db` / `paper_account.db` 一字未动**（`daily_bars_raw` 仍是 0 行，
  等 T40 授权后回填；只读体检一律 `mode=ro` + `PRAGMA query_only`）。
- 没跑任何写账本命令；没碰 Pi/secrets/cron/tag；没 force push；`config.yaml` 只读。
- 生产 venv（有 akshare 的那个）与引擎 venv 都没装新东西；
  证据脚本只用现有依赖。
