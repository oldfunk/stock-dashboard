# 批19：EM F10 深层字段 `interest_coverage` 缺口修复（2026-11-12）

派工：Issue #9 comment 6819599078（母方 OpenCode）。
分支：`worker/qoder-20261112-b19`，基线 `origin/main@933d256`。
三件各自一个 commit，**未合并**，产线写库由母方执行。

| 件 | commit | 用例 | 判别性变异 |
|---|---|---|---|
| 19-a 陈旧度规则收录"近 8 期深层字段缺失" + EM 冷却 | `31471ea` | 24 | 12/12 全红 |
| 19-b `force_full` 死条件 → 周六维护线程真的全量 | `9e29d65` | 27 | 22/22 全红 |
| 19-c 回填 2026 三期 `interest_coverage` | `a563849` | 37 | 23/23 全红 |

---

## 1. 根因：母方两层之外还有第三层

母方派工单指出两层（todo 判定太严、周末全量是死条件）。只读取证后确认**还有第三层，
而且它是"就算前两层修好也填不上"的那一层**：

### 第一层：todo 判定把 769/771 只候选挡在门外

`collect_historical_financial_data` 的增量入选只看"最新报告期距今 > `incremental_days`(60) 天"
（外加"无历史"）。产线 2026-11-12 候选池 **771 只**，按 `created_at < 15:30` 还原开跑前状态：

| 开跑前最新报告期 | 距 11-12 | 只数 | 旧规则 |
|---|---|---|---|
| 2026-09-30 | 43 天 | **769** | 不入选 |
| 2026-06-30（605259） | 135 天 | 1 | 入选 |
| 2025-12-31（000983） | 316 天 | 1 | 入选 |

→ 当天旧规则实际入选 **2 只**，与产线实况吻合：11-12 15:30–15:40 之间
`financial_history` 只有这 2 只被写入（000983 79 行、605259 1 行，合计 80 行）。

**关键在于跑完之后**：这 2 只补完后，全池 771 只最新报告期都变成 2026-09-30（43 天）
→ **次日起旧规则入选 0 只**，而 ic 的采集侧写入方正是这个抓取，缺口永不闭合。
本文与 19-a commit message 里的"0 → 771"指的是这个**跑完后的稳态**对比，
不是 11-12 当天的入选数（当天是 2）；此处按开跑前状态重新取证后写清。

新规则在稳态下会让 771 只**全部**入选：

```
pool=771  no_history=0  stale60=0  deep_gap=771  gap_only=771  union=771
requests/day  current=0  new=2313
```

### 候选池规模澄清：771 不是 20

派工单根因 1 写的是"20 只候选没有一只触发"。只读复核：`run_log` 里
`total_stocks=5527`、`screened_count=20`，`screening_result` 当天 20 行 / 20 个代码
——那 20 只是**筛选输出**（`value_screener` 写入，`scheduler:455` 又按 `limit=25` 取它做日 K）。
当天真正进入历史财务采集的候选池是 **771 只**（`rebuild_financial_summaries(candidates)`
当天重建 771 条 `financial_summary`，N20 修好后该数就等于 `len(candidates)`）。
两个口径下旧规则都几乎不入选，但**请求量估算差 38 倍**，冷却的必要性按 771 算。

### 第二层：周末全量从未发生

`src/orchestrator.py` 旧写法 `is_weekend_full = datetime.now().weekday() == 5`，
而流水线只在工作日 15:30 跑（`scheduler._check_daily_pipeline` 里 `weekday() >= 5` 直接 return）
→ 该条件**永远 False**，`force_full=True` 这条路径在生产里一次都没走过。

### 第三层（本次取证新增）：东财利润表对非金融股根本不给利息支出

采集器算 ic 的公式是 `OPERATE_PROFIT / abs(INTEREST_EXPENSE)`。直连东财 F10 利润表实测：

| 代码 | 上游行数 | `INTEREST_EXPENSE` 非空 | `FE_INTEREST_EXPENSE` 非空 |
|---|---|---|---|
| 605259 | 31 | **0 / 31** | 24 / 31 |
| 000983 | 108 | **0 / 108** | 37 / 108 |

`INTEREST_EXPENSE` 是金融股专用列，非金融股恒为 `"nan"`；有值的是
`FE_INTEREST_EXPENSE`（财务费用明细里的利息支出），采集器没读它。
所以**把前两层修好、让 EM 全量真的跑起来，ic 这一格依然是 NULL**。

这也解释了库内 ic 的真实来源。按"是否两位小数"分辨算法指纹：

```
report_type=A  n=34509  looks_rounded=1  looks_raw=34508
```

34,509 条年报 ic 里 34,508 条是**未取整的原始值**，与 mainfin `INTSTCOVRATE` 逐位相同
（真正的历史写入方是 `backfill_financial_full.py` 走的 mainfin，不是采集器 `:896`）。
本次再测 000983：库内已有 8 期 ic，与上游 8 期**全部逐位相同**。

**结论：ic 必须走 mainfin `INTSTCOVRATE`，不能靠修 EM 采集公式。**
（`FE_INTEREST_EXPENSE` 是另一条可行路，但口径是"营业利润/利息支出"且需取整，
与库内 34,508 条原始值不同源；混进同一列会把历史序列变成两种算法的拼接。见 §7 附带发现。）

---

## 2. 19-a：陈旧度规则 + EM 冷却（`31471ea`）

**改了什么**

- 增量入选加第二条理由：近 `EM_DEEP_WINDOW=8` 期里 `EM_DEEP_FIELDS`（ic/fcf）仍有 NULL。
  判据复用采集侧 `_em_sheets_needed`，与循环内两张东财表的开关**同一份逻辑**，
  不新造一套窗口口径。
- 判定所需三份集合**一次批量取回**（`get_deep_field_dates` / 新增 `get_report_dates` /
  新增 `get_backfill_marks`），不在 771 只的循环里逐只查库；
  `filled` 集合同时喂给两张表开关，省掉原来那次重复查询。
- `EM_DEEP_FIELDS` 保持单一来源，`get_deep_field_dates` 口径未动。

**为什么冷却必须同批落地（否则 19-a 是净倒退）**

新规则在稳态下会让 771 只候选**全部**入选（旧规则 0 → 新规则 771），
而第三层根因决定了 ic 永远拉不到：

```
requests/day  current=0  new=2313
```

每天 2,313 个必然白烧的上游请求。按母方 2026-11-05 的 EM 冷却裁定：
拉完记 `backfill_progress(task=em_deep_gap)`——

| 情形 | status | covered | 明天 |
|---|---|---|---|
| 请求失败 / 骨架没出数 | `partial` | 不写 | 重试 |
| 拉成功但字段仍缺 | `no_data` | 最新报告期 | 冷却到新报告期 |
| 补齐 | `done` | 最新报告期 | 冷却到新报告期 |

冷却**只挡"字段缺"这一条理由**：报告期过旧、无历史照拉；`force_full` 不受冷却影响
（这正是 19-b 周六全量能绕过冷却的依据）。

---

## 3. 19-b：`force_full` 死条件 → 周六维护线程（选项①）

### 3.1 为什么选①不选②

派工单给的两个选项：①挂到 `_run_weekly_refresh`；②修条件并确保周六跑流水线。选①：

- 选项②要把整条采集+筛选+AI 链路在周末再跑一遍，而周末没有新行情、没有新披露，
  代价远大于收益；还会多产一份周末 `screening_result`，污染"最新一次筛选"的口径。
- 周六维护线程已经是后台 daemon 线程、已有进行中闸（N9 的落点），
  本来就是"每周一次的慢活"该待的地方。

同时**保留 `force_full` 参数本身**：删掉它唯一的调用点会让它变成死参数（N13 规矩），
所以 orchestrator 改为平日恒增量，全量调用点移到周刷新里，参数仍然活着。

### 3.2 为什么必须分批 + 限时 + 进度表

全量模式单只约 **6 个上游请求**（同花顺骨架 + 东财利润表 + 现金流量表 + roic/fcf +
资产负债表 + 现金流），771 只 → 约 **4,600 个请求**。产线 11-12 日志实测单只耗时：

```
605259: 15:35:12 → 15:35:27  约 15s（31 期）
000983: 15:35:27 → 15:35:55  约 28s（108 期）
```

一次跑完要 **3–5 小时**，必然打爆上游。所以对齐 `backfill_financial_extra.py` 的三件套：

| 机制 | 默认 | 作用 |
|---|---|---|
| `em_batch`（限量） | 40 只 | 约 10–19 分钟；`0` = 这段不跑（上游限流时的开关，不另加 flag） |
| `em_max_seconds`（限时） | 900s | 到点优雅收尾，已处理的照常记进度 |
| `backfill_progress(task=em_full_gap)` | — | covered=最新报告期 → 下周六接着跑下一批；新报告期一到自动重新入选 |
| 连续失败中止 | 5 只 | N10：上游限流时不能把全市场标成 partial |
| `sleep` | 0.4s | 每只之间限速 |
| 耗时/只数/剩余 | — | 写进日志与返回值，母方据此调批大小（派工单要的"测速"） |

配置在 `config/config.yaml` 新增 `deep_refresh:` 段，四个键都可改，不用动代码。

### 3.3 两段顺序：先 ic 再 EM

`run_weekly_deep_refresh(cfg)` = ic 回填段 + EM 全量段，各有独立预算：

- **ic 段**（复用 19-c 的 `interest_coverage_backfill.run`）：单只 1 个请求、0.32–0.70s，
  且是唯一能填非金融股 ic 的路径。预算 `ic_max_seconds=1800`。
- **EM 段**：单只 6 个请求、15–28s。预算 `em_max_seconds=900`。

先便宜后贵，EM 段不会把 ic 段的时间吃光。ic 段跑完后 EM 目标集会向 fcf-only 集合收缩
（上界 1,061 只，见 §5）。

### 3.4 目标集口径：为什么不是全历史

"全量"指**采集模式**（`force_full=True`：两张东财表无开关、不受平日冷却限制），
目标集仍按近 `EM_DEEP_WINDOW=8` 期的缺口挑，与平日 `_em_sheets_needed` 同一窗口。

新增 `FinancialHistoryDAO.targets_missing_deep_fields(fields, window, ...)`：
`ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY report_date DESC)` 取近 window 期，
`HAVING SUM(f IS NULL) > 0 OR ...`，列名逐个过 `DEEP_FIELD_COLUMNS` 白名单（批8 P1-2），
`window<=0` 钳到 1 期（批17-a H3 建议①同规矩）。

**不选全历史**的理由（只读实测）：全历史 ic 缺口 4,854 只 / 116,529 行，其中大量是
2018–2022 的老报告期，而 EM 那条 `op/abs(ie)` 对非金融股**根本算不出 ic**，
重拉只是白烧配额；按 40 只/周要 121 周才轮完一遍，绝大部分请求注定无效。
窗口外的老 ic 缺口由 ic 段走 mainfin 覆盖**全部报告期**，那才是能填上的路径。

### 3.5 落点

- `src/collector/weekly_deep_refresh.py`（新）：逻辑在 src，因为 `src` 不允许反向
  import `scripts`（`tests/test_no_src_imports_scripts.py` 守着），`trading_calendar` 是先例。
- `src/scheduler.py:_run_weekly_refresh` 末尾追加一段，独立 try/except，
  失败只告警，不影响前面四件、不卡进行中标志。放最后，前面几件的时点不受这段慢活影响。
- `src/orchestrator.py`：删掉死条件，平日恒增量。

**触发时点**：跟着周六维护这一班车走，时刻取自 `outcomes.refresh_weekday` /
`refresh_time`（产线配置 = 周六 **09:00**），深层字段段没有自己的时点。
合并后下一个周六自动跑，无需手工触发。

---

## 4. 19-c：回填 2026 三期 ic（`a563849`）

### 4.1 为什么新写一件而不是扩 `backfill_financial_extra.py`

派工单说"你定，说明理由"。三条：

1. `update_extra_fields` 是 **A-only**，且 `EXTRA_FIELDS` 白名单里**没有** `interest_coverage`。
   扩它要同时改写入范围和列白名单，等于动另一件已验收的回填口径。
   而派工单点名的缺口正在**季报行**（2026 三个季度），A-only 正好把它们排除在外。
2. 加利润表源也修不了（第三层根因）。
3. 口径必须与库内既有值一致 → mainfin `INTSTCOVRATE`（§1 取证）。

### 4.2 改了什么

- `mainfin_fetcher`：抽出共用分页器 `_mainfin_pages`（短页即停 / 页数上限 /
  后页失败保留前页但带 error），季报披露日改走它；新增
  `fetch_mainfin_interest_coverage`——**不加 `REPORT_TYPE` 过滤**，
  东财没有"季报"这个取值，加了稳定返回 0 行（已实测）。
- `database`：`targets_missing_interest_coverage`（A+Q 全历史缺口，covered 取最新报告期）
  + `fill_interest_coverage`（**只填 NULL**、不造行、绑定参数、dry-run 同谓词；
  `ic=0` / 负值是合法值，判空用 `is None` 不用真值）。
- `src/collector/interest_coverage_backfill.py`：进度表三态、连续失败中止、时间预算、
  耗时可读到；`partial` 不写 covered → 自动重试。
- `scripts/backfill_interest_coverage.py`：薄 CLI，不 `chdir`、不另开写库口径。
  flags：`--codes --limit --offset --sleep --max-seconds --max-consecutive-failures
  --force --dry-run --status`；有 partial/aborted 时 exit 1。

**只填 ic 这一列**，不顺带 fcf/roic：fcf 在库里有两种口径（采集器"经营+投资现金流"
vs 上游 `FCFF_BACK`），混进同一列会把历史序列变成两种算法的拼接。

### 4.3 母方实跑命令（产线写库归母方）

```bash
cd /home/nec/stock-dashboard
# 先干跑看目标集与可填行数（不写库）
python3 scripts/backfill_interest_coverage.py --dry-run --limit 20
# 小批实跑，确认逐位一致后再放量
python3 scripts/backfill_interest_coverage.py --limit 50 --max-seconds 600
# 全量（4,854 只，单只 0.32-0.70s + sleep 0.4 → 约 1 小时；可分次续跑）
python3 scripts/backfill_interest_coverage.py --max-seconds 3600
# 进度
python3 scripts/backfill_interest_coverage.py --status
```

验收 SQL（派工单要求"回填后产线 2026-09-30 ic 覆盖率 > 0"）：

```sql
SELECT report_date, COUNT(*) n,
       SUM(interest_coverage IS NOT NULL) filled,
       SUM(interest_coverage = 0) zeros
FROM financial_history
WHERE report_date IN ('2026-03-31','2026-06-30','2026-09-30')
GROUP BY report_date;
```

---

## 5. 产线只读实测（2026-11-12，`mode=ro` + `PRAGMA query_only=ON`）

连接方式：`sqlite3.connect("file:/home/nec/stock-dashboard/data/db/stock_dashboard.db?mode=ro", uri=True)`，
全程未对产线写库、未调 `init_database()`。

### 5.1 缺口现状

| report_date | 行数 | ic 非空 | ic=0 |
|---|---|---|---|
| 2026-09-30 | 852 | **0** | NULL（即无 0 值） |
| 2026-06-30 | 975 | **0** | NULL |
| 2026-03-31 | 1048 | **0** | NULL |
| 2025-12-31 | 5526 | 4437 | 0 |
| 2025-09-30 / 06-30 / 03-31 | 各 1048 | **0** | NULL |

→ 是 NULL 不是 0，派工单描述准确。缺口不止 2026 三期，2025 三个季报同样全空。

### 5.2 两个目标集

| 目标集 | 规模 | 查询耗时 |
|---|---|---|
| 19-c ic 段（A+Q 全历史任一 ic NULL） | **4,854 只 / 116,529 行** | — |
| 19-b EM 段（近 8 期缺 ic 或 fcf） | **2,238 只**（ic 2238 / fcf 1061） | 0.40s |
| 对比：近 8 期缺 ic **或** fcf 的全历史行数 | 4,854 只 / 116,537 行 | — |
| 候选池 771 只里近 8 期有缺口的 | **771 / 771** | — |

（116,529 与 116,537 差 8 行：前者只数 ic NULL，后者数 `ic IS NULL OR fcf IS NULL`，
即 8 行 ic 已填而 fcf 空。两个数字来自不同谓词，不是时间漂移。）

### 5.3 上游可供性（mainfin `INTSTCOVRATE`，单只 1 请求）

| 代码 | 上游行数 | 有 ic 期数 | 耗时 | 库内已有 ic | 与上游逐位相同 | 2026 三期可填 |
|---|---|---|---|---|---|---|
| 600519 | 100 | 21 | 0.70s | 0 | — | 06-30、03-31（09-30 库内无此行） |
| 000983 | 100 | 33 | 0.36s | 8 | **8 / 8** | 09-30、06-30、03-31 |
| 605259 | 31 | 24 | 0.32s | 0 | — | 09-30、06-30、03-31 |

600519 的 2026-09-30 上游给 2790.41764651321（三季报），但库内该股最新行只到 06-30
——`fill_interest_coverage` **不造行**，这一格要等采集侧建行后才填得上，符合设计。

### 5.4 进度表现状

`em_deep_gap`（19-a 新增）、`interest_coverage_backfill`（19-c）、`em_full_gap`（19-b）
三个 task 在产线**均为 0 条标记**（新任务，尚未实跑）。既有 7 个 task 未受影响。

### 5.5 节奏估算

- ic 段：4,854 只 × (0.32–0.70s + 0.4s sleep) ≈ **1 小时**；默认预算 1800s → 单次约 2,000 只，
  分 2–3 次续跑。
- EM 段：默认 40 只 × 15–28s ≈ **10–19 分钟**，`em_max_seconds=900` 兜底；
  2,238 只按 40/周约 56 周轮完一遍——但 ic 段先跑完后目标集向 fcf-only（≤1,061）收缩，
  且新报告期一到会重新入选，所以实际是"每周把最新一批缺口清掉"，不是"轮完历史"。
  母方可据日志里的耗时/剩余调 `em_batch`。

### 5.6 开跑前状态还原（`created_at < '2026-11-12T15:30'`）

| 桶 | 只数 |
|---|---|
| `fresh_le60`（最新 2026-09-30，43 天） | **769** |
| `stale_gt60` | **2**（000983 → 2025-12-31 / 316 天；605259 → 2026-06-30 / 135 天） |
| `no_history` | 0 |

当天 15:30–15:40 实际写入 `financial_history` 的正是这 2 只（80 行），
与旧规则入选数逐只吻合 → 取证链闭合，不是推断。

`run_log` 最近 6 次：`total_stocks=5527`、`screened_count=20`、`status=completed`
（11-12 / 11-11 / 11-10 / 11-09 / 11-06 均为 15:30 开跑、约 6 分钟结束；
11-07 只有 `..._review`）→ **周末确实没有流水线运行**，第二层根因成立。

---

## 6. 自测证据（本机）

环境：Windows / Python 3.14 / `PYTHONPATH=G:/qoder/local_stubs`（akshare 桩，
任何真实 akshare 访问都会抛异常，保证本机不打上游）。

```
python -m pytest tests -q --basetemp=/g/qoder/pytest_b19b_full
```

- 19-c 提交时全量：**1340 passed / 12 failed / 3 skipped**（125.74s）
- 19-b 提交时全量：**1367 passed / 12 failed / 3 skipped**（146.20s）
  → 比 19-c 基线正好 **+27**，即本件新增用例数；失败集与跳过集逐条一致，无新增失败。

12 个失败是**本机桩的既有失败**，与本批无关：11 个
`tests/collector/test_kline_fetcher.py` + 1 个
`tests/news/test_news_feature.py::test_caixin_fetch_market_parses_headline_and_date`。
权威门禁是 nec 上的 `scripts/gate.sh`，由母方跑。

**判别性变异**（每条都改坏一处实现，要求至少一个用例转红；含反向变异）：

- 19-a：12 条全红（含"判据恒真 → 省量用例红"的反向）
- 19-c：23 条全红（含 A-only / Q-only 两个反向）
- 19-b：22 条全红，锚点失效 0 条。覆盖：窗口失效、window<=0 钳制、列名白名单、
  只看第一个字段、进度表排除失效、covered 差等号、latest 取窗口内最早一期、
  **周刷新不再 force_full**、整批一次调、限量不接线、去掉时间预算、
  partial 也写 covered、拉完仍缺标 done、骨架没出数当上游没有、中止阈值差一只、
  周刷新沿用平日冷却、周入口跳过 ic 段、两段顺序颠倒、配置预算被忽略、
  `em_batch=0` 关不掉、调度器不挂周刷新、orchestrator 恢复周末死条件。

派工单四条验收用例逐一对应：

| 验收要求 | 用例 |
|---|---|
| 60 天内但近 8 期 ic 缺失 → todo True | `test_em_deep_todo_20261112.py`（19-a，含产线 NaN 形状） |
| 深层字段齐全 → todo False | 同上（省量用例，反向变异守着） |
| `force_full` 周末为 True **或周维护路径真的调用全量** | `test_weekly_em_really_calls_force_full`：断言 `calls == [(["000001"], True), (["600519"], True)]`；`test_saturday_maintenance_invokes_deep_refresh`：断言维护体 `called == ["dividend","calendar","bench","outcome","deep"]` |
| 回填后产线 2026-09-30 ic 覆盖率 > 0 | 需母方实跑（§4.3）；本机已验"只填 NULL / 不造行 / 幂等 / dry-run 同谓词 / ic=0 与负值合法" |

不破坏既有口径的守护用例：`get_deep_field_dates` 口径未动、`EM_DEEP_FIELDS` 单一来源
（`test_recent_gap_is_selected_and_field_list_is_single_source`）、
批16-a"仅缺字段才拉"的省量断言、`src` 不反向 import `scripts`、脚本不 `chdir`。

---

## 7. 附带发现（**未修**，提请母方裁定）

### 7.1 同一类死周六条件还有一处：分红历史

`src/orchestrator.py:140` 附近：

```python
if _dt.now().weekday() == 5:
    refresh_dividend_history()
```

与 19-b 修的是**同一个 bug 类**：流水线只工作日跑 → 多年分红历史从未刷新。
而周六维护线程里的 `ensure_dividend_data` 只刷**最新年报**分红，不刷 10 年历史，
所以"连续分红年数"依赖的历史序列在生产里是静态的。

建议（3 行移动，未在本批动手，因为派工单范围是 ic）：把 `refresh_dividend_history()`
从 orchestrator 挪进 `_run_weekly_refresh`，与 `ensure_dividend_data` 并列。
请母方裁定是否单开一件。

### 7.2 `FE_INTEREST_EXPENSE` 是另一条 ic 路径，但口径不同

§1 实测：非金融股东财利润表的 `FE_INTEREST_EXPENSE` 有值（605259 24/31、000983 37/108），
采集器读的 `INTEREST_EXPENSE` 恒空。改用前者能让 EM 路径自己算出 ic，
但结果是"营业利润/利息支出"且采集器会取整，与库内 34,508 条 mainfin 原始值不同源。
**本批不改**：混口径会污染历史序列。若母方想让 EM 路径也产出 ic，
需要先裁定"以哪个口径为准、历史值是否重算"。

### 7.3 批18 未交付项

批18-a/b/c/e 仍在母方队列，本批未触碰。19-a 里同批落地了母方 2026-11-05 的
EM 冷却裁定（原批18-d），已在 commit message 里注明。

---

## 8. 边界

- 本批**未对产线写库**，所有产线数字来自只读连接；实跑与验收归母方。
- 未合并、未碰 `nightly/20261023`、未改共享历史。
- 未动 `get_deep_field_dates` / `EM_DEEP_FIELDS` / 批16-a 省量逻辑的口径。
- 未修 §7 的三项附带发现。