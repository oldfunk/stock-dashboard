# v0.12.0 发布说明

> 发布：**2026-11-28**（用户决定加速；post-release 观察 11-30/12-01）。
> 发布前：全量 **1107 passed / 13 skipped**；`release-gate --skip-tests` GO；
> daily-check 10 绿 / 5 黄 / 0 红；ops 仅 qfq warn。

## 主题

把 v0.11.0 的“能看/能算”推进到“可分配、可审计、可备份、可校准”：

1. **策略组合**：健康度进 AI、`health_policy`、多策略资金额度；
2. **审计与报告**：决策回放、日/周报、逐日审计；
3. **运维**：账本备份轮转/恢复演练、`ops check`、自动备份 cron；
4. **数据校准**：AI 成绩校准、母库 `daily_close` 补 raw 参照；
5. **面板**：AI 成绩/策略额度/运维巡检三张只读卡。

## 主要变化

### 策略与 AI

- `general` 默认策略：**MA20/60 + 趋势/质量/风险**（10–30 个交易日）；
- `llm_context.strategy_health`：窗口超额/回撤/换手/费用，advisory；
- `agent.health_policy=off/advisory/enforce`（默认 advisory；enforce 才拦新买）；
- `strategy.allocations`：权重由用户/AI 配置，额度=weight×NAV；
  计划/执行两处闸，卖出/持有不受限；
- `ai_scorecard`：母库 570 个 analysis 去重后的 20 日成绩，样本足够才注入；
- `strategy calibration`：BUY/HOLD/AVOID 样本、超额、命中率；
- `strategy compare`：MA20/60 vs 旧 MA5/20，同窗口/费用/风控，样本内外分段；
- `strategy research`：样本内→样本外→前向占位，输出 candidate/overfit/reject，
  不自动写生产；
- `report --benchmark sh000922` + 行业归因；面板“报告”页可查日/周报与逐日审计。

### 审计与报告

- `agent replay` 增加 `health_review`（demote 是否被回应）与额度快照；
- `report daily|weekly --out`：NAV/成交/归因/风控/健康度变化；
- `audit day --date`：计划/决策/意图/成交/条件单按标的串联；
- `strategy-contract.md` §8：注册表/方案/AI 风格/母策略四层边界。

### 运维

- `ledger backup --keep N`：每日备份轮转，安全副本/演练副本不删；
- `ledger backup-status`：最近备份年龄/完整性；
- `ledger migrate`：`schema_meta` 版本化（生产已 0→1）；
- `ops check`：备份/心跳/raw_gate/熔断；red 退出 1；
- `deploy/install-ops-cron.sh`：21:10 备份 + 21:20 巡检；
- `deploy/release-gate.sh`：发布前一键 pytest/ops/备份/数据门禁/daily-check；
- `security audit` + `docs/security-review.md`；`.github/CODEOWNERS`；
- `corporate tax/suggest`：分红税与除权除息建议（只建议，不自动改账）。

### 数据

- `mother.raw_close_map` 用 `daily_close` 补 `kline_daily.close_raw` 的按标的滞后；
- 母库 `analysis_outcome` 旧样本无 verdict 时按 `signal + ret_20` 校准；
- 母侧 `/api/performance` 双计问题已提 issue/dashboard#12，子项目只读不改母码。

## 已知边界

- `strategy.allocations` 的已用额度是**成本口径近似**（卖出按最近买入归属回冲）；
- AI 成绩样本窗口 2026-07-08..09-29，BUY 仅 23 条，输出带警告；
- `raw_gate` 用 `daily_close` 补参照；母库 `kline_daily` 自身仍按标的滞后；
- `qfq` 巡检可能为 **warn**：本地 akshare 与母库 `close_qfq` 锚点不一致
  （2026-11-28 实测 4 只）；执行侧 `auto` 用母库，本地仅兜底，不自动互改；
- 母项目 `:8081` vendor 仍 v0.10.1，需发 tag 后由母侧 `subtree pull`；
- 第二实例 NAV 10-23..11-19 历史缺口不插值；
- 撮合细化（成交量/排队/竞价）**不在本版本**，见 `docs/matching-refinement.md`。

## 升级/回滚

- 升级：`git pull --ff-only` → `pytest -q` → `ledger backup` → `ledger migrate` →
  重启 dashboard → `ops check` → `daily-check`；
- 回滚：`git checkout v0.11.0` 并重启；账本不回滚；备份文件保留；
- 不重写已发布 tag（`v*` ruleset 禁删/禁移/禁强推）。
