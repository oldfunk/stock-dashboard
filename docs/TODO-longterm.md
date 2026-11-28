# 长线 TODO（2026-11-28）

> 长线 backlog；当前执行队列看 `docs/iteration-plan-20261124.md`，里程碑看
> `docs/ROADMAP.md`，交付证据看 `docs/WORKER.md`。本文件只列"还没做/待决策"，
> 完成项回填到 WORKER/ROADMAP，不在这里维护重复状态。

## A. v0.12（近期，I5 收口 + I6）

| ID | 优先级 | 项 | 状态 | 依赖/验收 |
|---|---|---|---|---|
| A1 | P0 | 健康度进 AI 上下文（advisory）：plan/event prompt 带超额/回撤/换手/费用；AI 说明继续/放缓/停手理由 | ✅ 2026-11-28 | 不自动拦买；`strategy_health` 已进 `llm_context` |
| A2 | P0 | `health_policy` 配置面：`off/advisory/enforce`，默认 advisory；enforce 是用户显式策略 | ✅ 2026-11-28 | 配置三件套 + 5 条测试；拒因落 `ai:decide` |
| A3 | P0 | 多策略资金分配：`strategy.allocations` 权重、单策略/组合两级预算、统一风控入口 | ✅ 2026-11-28 | 权重由用户/AI 配置；超限 fail-closed；无隐式权重；5 条测试 |
| A4 | P1 | AI 风格审计：计划里“健康度输入 → 选择 → 理由”可回放 | ✅ 2026-11-28 | `agent replay.health_review`（demote 是否被回应）+ 4 条测试 |
| A5 | P1 | 日/周报：持仓、成交、归因、风控事件、健康度变化，CLI 可导出 | ✅ 2026-11-28 | `report daily/weekly --out`；只读；3 条测试 |
| A6 | P1 | 默认通用策略回测/前向对照（不是收益承诺） | ✅ 2026-11-28 | `strategy compare`；同窗口 MA20/60 vs MA5/20；样本内外分段 |
| A7 | P2 | 策略注册表与方案（scheme）关系文档化：内置策略 vs AI 风格 vs 母策略 | ✅ 2026-11-28 | `strategy-contract.md` §8 + 分配口径 |

## B. v0.13（M4 + M1.5）

| ID | 优先级 | 项 | 状态 | 依赖/验收 |
|---|---|---|---|---|
| B1 | P0 | 账户/风控状态备份恢复演练 + `paper_account.db` 迁移工具与版本化 | ✅ 2026-11-28 | `ledger backup/inspect/restore/migrate/drill`；副本演练 + 回滚步骤；5 条测试 |
| B2 | P0 | 任意一天“为什么买/没买/卖”审计报告 | ✅ 2026-11-28 | `audit day --date`；计划/决策/意图/成交/条件单串联；2 条测试 |
| B3 | P1 | 安全复核：面板、Key、口令攻防演练与文档更新 | ✅ 2026-11-28 | `security audit` + `docs/security-review.md`；2 条测试 |
| B4 | P2 | 分红税口径建模（1 月内 20%、1 月–1 年 10%、超 1 年免税） | ✅ 2026-11-28 | `corporate tax` 只建议；缺 entry_day 不按 0 税 |
| B5 | P2 | 自动除权除息建议（akshare 分红送配 → 调整建议） | ✅ 2026-11-28 | `corporate suggest`；母库优先，akshare 可选；不自动改账 |

## C. 阻塞/外部依赖

| ID | 项 | 阻塞 | 下一步 |
|---|---|---|---|
| C1 | P5.5 AI 成绩校准 | ~~母库 verdict 为空~~ **已解除（2026-11-28）** | ✅ 已上线 `strategy calibration`：母库 390 条可用样本（旧样本按 signal+前向收益，verdict 有 374 条）；`llm_context.ai_scorecard` 仅样本足够时注入 |
| C2 | P7.4 契约测试进母 CI | 母侧策略（vendor 失败不阻塞） | 母 `scripts/gate.sh` 已跑 `paper_trading/tests`（含 `test_mother_contract.py`）；是否把 vendor 契约失败改为阻塞仍由母侧决定 |
| C3 | 母库 `raw_gate` 参照滞后 | ~~`close_raw` 断更~~ **已解除（2026-11-28）** | ✅ 母库 `daily_close` 与 `close_raw` 69,560 行零差异，子项目用 `daily_close` 补洞；生产 `raw-coverage --gate` 0 violations（440/440 checked） |
| C4 | 母项目 :8081 vendor v0.11.0 | 母侧发布窗口 + 子项目尚未打 v0.12 tag | 子项目发 v0.12.0 后由母侧 `subtree pull`；当前母仍 v0.10.1 |
| C5 | 母项目 issue #7（第二实例 NAV 断档） | 母侧历史缺口 | 第二实例 2026-11-20..11-27 已每日 1 行；10-23..11-19 历史缺口按母侧口径不插值，issue 仍开 |

## D. 之后（v0.13+ / 未排期）

| ID | 项 | 说明 |
|---|---|---|
| D1 | 撮合细化：成交量约束、涨跌停排队近似、集合竞价口径集中文档 | **设计+回归清单已完成**（`docs/matching-refinement.md`）；代码待 v0.12 发布后单独分支，默认关闭 |
| D2 | 基准扩展：中证500/行业指数、风格暴露 | 需母库指数源；先确认 code 契约 |
| D3 | 参数研究流程固化：样本内 → 样本外 → 前向；禁止网格第一直接上生产 | ✅ 2026-11-28 `strategy research` + `docs/research-process.md`（前向仍 pending，不自动写生产） |
| D4 | 策略注册表 → 方案层映射：内置策略如何被 scheme 选择/组合 | 与 A3 一起设计 |
| D5 | 面板：策略/行业/基准更多切片（可选） | AI 成绩/资金额度/运维巡检/报告页已上（T127/T131）；更多切片待定，先保证信息架构不膨胀 |
| D6 | 安全：CODEOWNERS、签名提交、外部身份治理 | ✅ CODEOWNERS + SECURITY 治理说明；签名提交为用户本地配置，不强制 |

## E. 已完成（只做索引，状态以 WORKER 为准）

- v0.11.0：面板 P2（基准/行业/成交/契约）、第二实例修复、T101/T102、影子对账退役、仓库保护。
- I5a：策略健康度（只读）。
- I5b：默认通用策略（MA20/60 + 质量/风险 playbook，2026-11-28）。
- I5c / A3：健康度进上下文、`health_policy`、多策略资金分配。
- A4–A7：风格审计回放、日/周报、默认策略对照、四层边界文档。
- B1–B5：账本备份恢复/版本化、逐日审计、安全复核、分红税、除权除息建议。
