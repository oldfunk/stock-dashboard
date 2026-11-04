# report-t53 · 测试日期敏感性根治（Qoder，P1·小）

分支 `worker/t53`（基于 `origin/main@901bee6`，**待 review 未合并**）。
全套 **594 passed / 9 skipped**（本任务净增 7 条守卫测试，零回归）。
**动了产品码两处**（都是"加一个可选参数、默认行为不变"），因为光改测试治不了这个病——见 §2。

## 0. 一句话结论

日期 flaky 的病根不是"日期写得不好"，而是**基准日没有作为参数传进去**：
产品读墙钟、测试写死日期，两条线在真实日历上必然分叉。
⇒ 治法＝`sources.block(..., today=)` 与 `mother.daily_bars(..., today=)` 两个注入点
（与 T41 `sync_raw(today=)`、T50 `get_quotes(now=)` 同一约定）
＋ `test_mother_contract` 的 fixture 改成"由真实今天倒推且必为工作日"
＋ 一套能证明"这条规则以后不会被再写死"的守卫测试与扫描器。

## 1. 派发逐条核销

| 派发原文 | 状态 | 落地点 |
|---|---|---|
| 「`test_mother_contract` fixture 动态化（硬编码 2026-10-2x 全改相对今天的工作日）」 | ✅ | `tests/test_mother_contract.py:12-33` 新增 `_weekday(offset)` / `D_LATEST` / `D_PREV` / `D_NEXT` / `D_SEQ`；母库 8 处日期簇（kline_daily 两行×2 只、financial_summary `updated_at`、benchmark_daily、news published_at、screening run_date、analysis_date、market_index date、trading_calendar 两行）与本地三日行情全部由这些常量给出；两处 `UPDATE ... trade_date=?` 改成参数绑定 |
| 「全测试扫描硬编码"今天/近期日期"的断言」 | ✅ 两把工具 | ①`spikes/date_flaky_scan_t53.py`：AST 取**代码字符串字面量**里的日期（注释与 docstring 里的历史叙述不算——这版是第一版误报的教训），按"距今天 <30 天 + 文件读墙钟"分 RISK-A/RISK-A′/RISK-B；②`spikes/clock_shift_t53.py`：**把墙钟拨快 N 天真跑一遍 pytest**，红了才算真耦合（grep 只能给候选） |
| 「两次 flaky 教训：today+10 落周六、stale 写死布尔」 | ✅ 都钉住 | 前者：`test_relative_dates_are_never_weekends`（偏移出来的日期必须 <5  weekday）；后者：`test_daily_bars_accepts_explicit_today`（同一份 fixture，基准日 `D_LATEST` ⇒ 新鲜、`D_LATEST+4` ⇒ 判过期，断言不再依赖墙钟） |
| 「产出清单」 | ✅ §4 | 拨钟实验的红名单 + 三类归因（真老化 / 仪器误差 / harness 副作用） |
| 「修复」 | ✅ 本件范围内全修 | mother 路径（写死 + 墙钟那条）**已根治**：`test_mother_contract` 在 +7 天平拨实验中从红名单消失（§3） |
| 「`report-t53.md`」 | ✅ | 本文件 |

## 2. 产品码改了什么（以及为什么必须改）

```python
# sources.py:26  block(..., max_age_days=None, today: Optional[str] = None)
#   → _is_stale(asof, max_age_days, today or date.today().isoformat())
# mother.py:387  daily_bars(..., force_local=False, today: Optional[str] = None)
#   → "stale": bool(_stale(asof, 3, today or date.today().isoformat()))
```

为什么不能用 monkeypatch 绕过：`sources.block()` 内部是**函数级**
`from datetime import date`（`sources.py:36`），替换模块属性拦不到；
`mother._stale()` 同理。要么改产品给参数，要么让测试永远赌日历——
前者是本仓已经用了三次的约定（T41/T47/T50 都是"基准日/时刻必须可注入"）。

**默认行为不变**：`test_default_today_is_unchanged` 断言"不传"与"显式传真今天"
除 `stale` 外逐字段相同（`stale` 本身就是基准日的函数，比较它对诊断无意义）。
`config.yaml` 没动，调用点一个都没改（生产路径继续读墙钟）。

## 3. 拨钟实验（这才是"根治"的验收方式）

| 实验 | 结果 |
|---|---|
| 平拨 0 天（harness 自身副作用基线） | 3 条红：`test_core::test_t1_calendar`、`test_runtime::test_tick_plan_window_is_idempotent`、`test_agent::test_plan_exists_still_snapshots_close_nav`（假 `date` 类破坏 `isinstance`/比较语义） |
| 治前 +7 天 | 21 红 ⇒ 去掉上面 3 条 = **18 条真耦合**，其中包含 `test_mother_contract::test_daily_bars_mother_first_then_local` |
| 治后 +7 天 | **17 条**（18→17，`grep -c mother` = 0）⇒ 该路径与墙钟解耦；本件新增的 7 条守卫测试**全部不红**，说明它们的断言已经不依赖钟 |

| 治后真实日历推进 | 全套 594 绿（今天 2026-10-30 跑，含昨天会红的那条） |

## 4. 清单：+7 天平拨实验里剩下的红名单与归因

**类 A·仪器误差（真实推进不会坏，因为两边同钟）**
- `tests/test_ledger_gaps_t47.py::test_apply_external_fill_writes_entry_day_and_peak`
- `tests/test_fee_single_source_t51.py::test_m3_verify_uses_the_same_formula`

**类 B·真会老化（写死日期 + 产品读墙钟），建议按同一模式续修**
| 文件 | 条数 | 涉及的写死日期 | 需要给产品补的注入点 |
|---|---|---|---|
| `tests/test_agent.py` | 7 | `2026-10-27`/`2026-10-23` | `agent/loop.py` 计划日/条件过期判定（`_plan_date`、`expiry`）——多数已有 `now=`，缺的是"按今天算 target day"的入口 |
| `tests/test_engine_cond.py` | 3 | `2026-10-22`/`2026-10-24` | 条件单 expiry 上限计算（`conditional.py`）显式 `today=` |
| `tests/test_m1_correctness.py` | 3 | `2026-10-20`/`2026-10-21` | 持仓元数据/日预算按今天：`update_peak_price`、runner 预算 |
| `tests/test_engine_switch.py` | 1 | 同上族 | 与 engine_cond 同一处 |
| `tests/test_runtime.py` | 1 | `2026-09-29/30`、`2026-10-08` | `sync` 的"保留未来、丢弃历史"窗口基准日 |

**为什么不在这件里一起改**：这 15 条分属 5 个文件、涉及 4 个产品的时钟读取点，
其中 `test_agent.py`/`test_engine_cond.py`/`test_m1_correctness.py` 是
Hermes/OpenCode 在途任务（T15b2/b3）正在动的文件；切换日在本周末，
我不在切换前 48 小时里改别人在途的测试与产品入口。建议派一件 T56（或本件续做），
按 §2 的同一约定补齐这 4 个注入点——工具已经现成：
`python spikes/clock_shift_t53.py 7` 就是它的验收命令（红名单应为空/只剩类 A）。

## 5. 守卫为什么有效（不是"永远绿的断言"）

- `test_scanner_bites_but_ignores_history`：临时造一个测试文件，植入"今天+3"的日期
  ⇒ 扫描器必须抓到，且不能误报 `2025-12-31` 这种故意历史日期（正控制）。
- `test_cured_file_has_no_near_today_literals`：`test_mother_contract.py` 里
  距今天 <45 天的绝对日期必须为 0（除白名单两处历史场景）
  ⇒ 谁再写死一个近今天的日期，这条立刻红。
- `test_fixture_dates_follow_the_clock`：fixture 基准日与今天必须相差 ≤5 天
  ⇒ "把动态日期又改回写死"会在几天内自动暴露。
- 拨钟实验本身是终极管卫：`clock_shift_t53.py` 可随时复跑。

## 6. 遗留（交给主 AI 判断）

- **Q1**：类 B 的 15 条要不要现在派一件（工具已备好，红名单为空即验收），
  还是切换后再做？我倾向切换后——它们目前**在真实日历上是绿的**，
  只有产品注入点缺失这个结构性问题。
- **Q2**：`sources.block()` 的函数内 `from datetime import date` 是本仓
  "假时钟拦不住"的根因（`sources.py:36`）。要不要立一条约定：
  **任何新鲜度判定必须收 `today`/`now` 参数，不许在函数体内 import 后直接读钟**？
  写进 AGENTS 就能让下一个任务自动遵守；不写的话这类 flaky 还会再生。
- **Q3**：`clock_shift_t53.py` 的 shift-0 副作用（假 `date` 破坏 `isinstance`）
  要不要顺手改成"注入 `today` 而不是换类"的插件式做法？现在够用，
  但它给的红名单里有 3 条噪声，已在 §3 标注。

## 7. 交付物

| 文件 | 作用 |
|---|---|
| `paper_trading/integration/sources.py` | `block(..., today=)` 注入点（默认行为不变） |
| `paper_trading/integration/mother.py` | `daily_bars(..., today=)` 注入点（stale 判定可用给定基准日） |
| `tests/test_mother_contract.py` | fixture 动态化（`_weekday`/`D_LATEST`/`D_PREV`/`D_NEXT`/`D_SEQ`），11 处日期改相对/参数绑定，新鲜度断言改为显式基准日 |
| `tests/test_date_hygiene_t53.py`（新，7 条） | 注入语义、默认不变、周末规则、老化守卫、扫描器正控制 |
| `spikes/date_flaky_scan_t53.py`（新） | AST 化扫描器：RISK-A/A′/B 分级 + `--json` 清单输出 |
| `spikes/clock_shift_t53.py`（新） | 把墙钟拨快 N 天真跑 pytest 的诊断器（"红名单"才是清单） |
