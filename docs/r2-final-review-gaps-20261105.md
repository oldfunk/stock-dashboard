# 批17-b（2026-11-05）：R2-4/5/6 终审 H-001 —— 已收的一格、剩下的缺口、要拍板的口径

> 派工：`docs/handoff.md` 下一步方向 #1 —— "R2-4/5/6 终审 H-001：H-002 预注册（同 regime T+60
> 净增量为主判据）→ 对照假设（risk_off 暴露）→ 多重比较框架"。
> 本批把其中**不含口径决策**的那一格（R2-5 结论与冻结落库）做完了（`fa0892b`），
> 其余三项都撞到"阈值/校正方法/判据语义"这类必须由母方拍板的东西，
> 所以本文给对账 + 证据 + 建议默认值，不擅自实现一套统计口径。
> 全程只读（`mode=ro` + `PRAGMA query_only=ON`），未跑 gate。

## 1. 已收：R2-5 的"结论与冻结"（代码侧）

产线实况（2 个实验、16 条 result，`G:/qoder/probe_r2_state2.py`）与改后：

| 事项 | 改前（产线） | 改后 |
| --- | --- | --- |
| 实验级结论 | `research_experiment` **没有** verdict 列，`derive_verdict` 的结果只存在于当时的 CLI 输出 | 新增 `verdict` 列并落库；supported/rejected/**invalid** 三态都能在冻结行里查到 |
| 诚实限定语 | 16-b 写的 notes（幸存者偏差、分域覆盖率、多重比较提示）同样不落库 | 新增 `notes` 列并落库 |
| inconclusive 归因 | 三种成因（无结果行 / 判据不在实报集合 / 阈值不可解析）在库里**长一个样** | `inconclusive_reason` 点名是哪一种，追写进 notes 的 `缺证据：…` |
| 可复现锚点 | E-H001 / E-H001b 的 `code_commit`、`config_hash` 都是空串，而它们绑定的快照 manifest 里写着 `2b2b13b` / `336312fe9eea`（nec 上 `git rev-parse` 实测可用） | `_persist` 在计划留空时**从绑定的快照继承**；显式填过的一律尊重，绝不回写 `plan_json`（预注册不可篡改） |

老库补列走 `init_database` 的 `_add_column_if_not_exists`（`col_type` 要过 `safe_col_type` 白名单，
所以不能写 `NOT NULL DEFAULT ''`）——迁移前的旧行留 **NULL**，比补一条假的"未判定"诚实。
产线那两条历史行的锚点回填属生产写，请母方执行（数据就在快照 manifest 里，一条 UPDATE）。

没动的一格：`research_result.verdict` 16 行仍是空串（runner 不给每条指标判结论）。要不要逐条填是
口径问题，见 §5-Q3。

## 2. R2-4 稳健性套件对账

tasklist 原文：**样本外/滚动、regime/行业/市值分域、bootstrap CI、参数敏感性**。

| 要求 | 代码里有什么 | 缺口 / 卡点 |
| --- | --- | --- |
| 样本外 | `plan.params.holdout_days` 尾部 N 自然日，16-b 已改成按 date 切、脏日期留在估计集并计数 | 只有**一个**分界点 |
| 滚动（walk-forward） | 无 | **缺机制**：没有多窗口滚动重放，一次实验只回答"这一个切点稳不稳" |
| regime 分域 | 有，产线可用（`market_state_daily` 84 天、780 行分析命中 776） | — |
| 行业 / 市值分域 | 代码有（16-b 的 PIT 版 + `unknown` 桶 + 每桶 `{mean,n}`） | **数据没有**：`stock_snapshot` 一码一行，PIT 命中 2/780 → 这一格在补快照历史前只能读 unknown。不是代码能解决的，见 16-b 的三选一提请 |
| bootstrap CI | 有（按股票分块 block bootstrap，缺失如实 NULL 不退化成 0） | **verdict 不看 CI**（见下面证据） |
| 参数敏感性 | 完全无实现 | `docs/research-os.md:174` 写着"邻域不稳健 → 不晋升"，`:203` 要求"至少 2 个 regime/行业稳健"——两条都还没有代码承接 |

CI 不参与判据的代价（产线实测，同一实验 8 条指标、判据只用 1 条，其余 7 条同屏报出，其中 3 条 CI 跨 0）：

```
excess_20_20~40   v= 0.146  CI=[-1.00,  2.29]     n=50
excess_60_0~20    v= 7.630  CI=[-2.61, 19.67]     n=16
excess_60_20~40   v= 4.390  CI=[-1.25, 24.06]     n=10
```

现行 `derive_verdict`（`src/research/experiment.py:128`）只做 `value` vs `threshold`：
把判据换成 `excess_60_0~20` 的话，代码照样给 **supported**，而 CI 明说不可判。
这不是假设情景——H-001 的中间两档反向（-6.41 / -9.65pp，见批15-e）就是靠 CI 才读成"不能反证"。

## 3. R2-6 对照假设对账

- **结构没有承载**：`research_hypothesis` 的列里只有 `supersedes`（版本取代），没有"对照组/配对假设"
  关系，也没有指标族大小或校正方法字段 → 现在无法表达"同一数据快照、同一费用下的对照组"。
- **数据面只有一个因子**：`feature_definition` / `stock_feature_daily` / `universe_membership`
  三表 **0 行**（R1 遗留，从未启用）。"价值 / 质量 / 动量各一个"目前只有价值那侧有现成字段
  （`analysis_outcome.mos_at_entry`）；质量可用 `financial_summary`（5217 行，但它是**当前**汇总，
  无报告期 ⇒ 不是 PIT）；动量只能自己从 `kline_daily` 现算，而产线 kline 覆盖 959/5527。
- **多重比较**：notes 里那句"结论需按多重比较（R2-6 对照假设）解读"目前是纯文字；
  `research_result.p_value` 16 行全 NULL，没有任何校正实现。

## 4. 我建议的最小落地路径（等口径拍板后就能做，不需要新数据）

1. `ci_rule` 预注册字段（实验计划 + 假设卡）→ `derive_verdict` 按它决定"CI 跨 0 算不算不支持"。
2. 指标族大小 `m` 写进 notes（`m = len(horizons) × len(tiers)`，产线现值 8），
   并按选定的校正方法给每条 result 记 `p_value`（block bootstrap 已有重采样，
   经验双侧 p 可以直接从 bootstrap 分布算，不用额外一轮重采样）。
3. 参数敏感性：三条固定邻域各建**一个新的预注册计划**（tier 边界 ±10pp、horizon ±10 天、
   benchmark 换 `sh000922`），不改动已完成实验的记录；先只读复算也可以（`scripts/h001_regime_check.py`
   那个形状）。
4. 对照假设：`research_hypothesis` 增 `control_for`（或 `comparison_group_json`）一列 + CLI 校验
   "对照必须同 `data_snapshot_id` 同 `costs`"。动量/质量因子先别建表，用现有列凑最小对照即可，
   等 R1 的 feature 层真要用了再迁。

## 5. 要母方拍板的五个口径（都给了建议默认，勾一下就行）

| # | 问题 | 我的建议默认 |
| --- | --- | --- |
| Q1 | CI 要不要进判据 | 计划字段 `ci_rule ∈ {none, exclude_zero}`，**默认 none**（不改任何既有语义）；H-002 显式用 `exclude_zero`，与母方 H-001 报告里"CI 不覆盖 0"的实际口径一致。CI 为 NULL（没跑 bootstrap）一律 fail-closed → inconclusive |
| Q2 | 多重比较校正方法 | 只在**同一实验的指标族内**做（m=8）；二选一：Bonferroni（α/m，保守、可解释）或 BH-fdr；跨实验不合并——E-H001 与 E-H001b 是同数据同判据的复跑，不该被当两次独立证据 |
| Q3 | `research_result.verdict` 要不要逐条填 | 只给"计划判据那一条"填，其余留空并在 notes 注明"未预注册为判据"。8 条各带一个结论会被读成 8 个独立发现，正是 garden of forking paths |
| Q4 | 参数敏感性邻域定义 | tier 边界 ±10pp、horizon ±10 天、benchmark 换 sh000922；主判据符号翻转即"不晋升"（不是"结论作废"） |
| Q5 | H-002 先跑哪个对照 | 在快照历史补出来之前，行业/市值稳健这一格做不了。可选：(a) 同 regime T+60 净增量（+3.01pp [+0.67,+5.36]，批15-e 算过，样本仍全在 risk_off）；(b) neutral 环境 T+20（+4.98pp，14 行/3 只，只能看方向）；(c) 先等 10—11 月 neutral 期分析自然成熟再谈。我建议 (c) 之前先做 (a)，因为它已有一格真对照 |

## 6. 顺带发现的两个产线缺陷（要母方的写操作，我没碰）

1. **H-001 假设卡的中文在库里是 `?`**：`statement` 39 字符里 26 个是 0x3F
   （`hex(substr(statement,1,12)) = 3F3F3F3F3F3F3F3F4D4F533F`），`kill_criteria_json` 同样
   （`["??<30?", "??MOS?<10?", "CI??0"]`）。也就是说这张**已锁定**的预注册卡，判据与终止条件
   在权威库里读不出来。成因是文案经过某个 ASCII 通道（errors=replace）；应用逻辑没错，
   缺的是锁定前的完整性检查——批17-c（`4e80078`）已补上，下次这种卡锁不上。
   现存这一行要重写：建议先备份，按 UTF-8 通道 UPDATE `statement` / `kill_criteria_json` 两列，
   `status`/`locked_at`/`created_at` 不动（锁定的事实与文案损坏是两件事，别顺手改状态）。
2. **E-H001/E-H001b 的锚点空串**：代码侧已修（从快照继承），历史两行建议按快照 manifest
   回填 `code_commit='2b2b13b'`、`config_hash='336312fe9eea'`。

## 7. 验证声明

- 代码：`fa0892b`（R2-5 + 锚点）、`4e80078`（文案完整性闸）、`7edd3fa`（H3 两条建议）。
- 用例：本批新增 `tests/research/test_experiment_verdict_frozen_20261105.py` 14 条、
  `tests/research/test_hypothesis_text_integrity_20261105.py` 8 条、
  `tests/collector/test_em_incremental_gate_20261105.py` +6 条。
- 判别性变异：b17a 5/5、b17b 7/7、b17c 6/6、b17d 2/2 全被抓住（含反方向：规则过紧、
  无条件覆盖显式预注册值），脚本在 `G:/qoder/mut_b17{a,b,c,d}.py`，按字节备份还原。
- 本机全库套件：`PYTHONPATH=G:/qoder/local_stubs python -m pytest tests -q --tb=no` →
  **1275 passed / 12 failed / 3 skipped（100.80s）**。12 条失败与 3 skip 全是本机固定项
  （11 条 `tests/collector/test_kline_fetcher.py` + 1 条财新新闻用例，都撞在本地 akshare
  自检桩上；skip 为缺 duckdb 2 条 + `fcntl` 仅 POSIX 1 条），与批14/15/16 同一组。
  对账：批16 交付时本机 1247 passed → 本批 +28 = 17-a 6 条 + 17-c 8 条 + 17-b/d 14 条。
- 只读边界：取证脚本 `G:/qoder/probe_r2_state.py`、`probe_r2_state2.py`、
  `probe_commit_anchor.py`；未调用过 `init_database()` 于产线库；未跑 `scripts/gate.sh`（母方执行）。
