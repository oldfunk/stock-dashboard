# Hermes H1 交付（2026-11-03）

> 分支：review/20261103-hermes-h1 · 审查范围：9c545d1..d6c32c2（批9–批13-d/e + F2 覆盖层 + P2-3 重构）· 只读，未改生产。

## H1-a 审计范围更新（覆盖率/样本量假象 + 新脚本族）

1. **sector 覆盖率口径**：`backfill_sectors_sw.py` 报告 `94.9%` 是「map 里有映射的 code 数 / stock_snapshot 总行数」，不是「DB 里 sector 非空行数」；新浪回退桶（`sina_only_codes`）会继续挂着新浪行业名，两套词汇并存。验收时应以 `SELECT COUNT(*) ... WHERE sector IS NOT NULL` 为真值，脚本数字仅作预测。
2. **深度回填进度**：`backfill_price_depth.py` 的 `done/no_data` 计数按 **code**，不是按行；`--status` 显示的「处理只数」不能直接换算成「新增行数」，必须以 `SELECT COUNT(*) FROM kline_daily` 环比为准（handoff 已要求 B3 复检）。
3. **notice 回填 covered_report_date**：存的是该股**最新季报报告期**，新季报到来后自动重新入选，不会被进度表永久锁死——这是正确设计，但验收脚本若只看 `status='done'` 会低估真实缺口，应按 `notice_date IS NULL` 真值复核。
4. **F2 覆盖层**：`daily_close` 是当日覆盖层，`market_state_daily` 的 breadth_n/coverage 依赖它；若当日 EM 断连，coverage=1.0 仍可能代表「覆盖层撑住」而非「K 线完整」，验收时必须同时看 `kline_daily` 当日增量行数，不能只看 coverage。

## H1-b N22 历史 eps 重抓方案（只设计 + 脚本，生产写由母方执行）

- 根因：`financial_history.eps` 历史入库值被两位小数截断（N22 已修写路径），存量值仍被污染，估值分位的股本反推因此失真。
- 方案：按 code 重抓 `eps`（与 `net_profit/total_shares` 一致的未截断值），只更新 `financial_history.eps`，不动其他列；失败 fail-closed（空/异常整只跳过，不写半套）；进度复用 `backfill_progress` 任务名 `eps_regrab`，`covered_report_date` 存最新年报报告期，新年报到来自动重新入选。
- 脚本：`scripts/regrab_eps.py`（dry-run 默认；`--codes`/`--limit`/`--force`；`--max-consecutive-failures` 熔断）；生产写入由母方在 nec 执行，Hermes 仅交付代码+单测。

## H1-c 数据健康巡检脚本

- 路径：`scripts/health_check.py`（只读，`--json` 机器可读）。
- 检查项：`kline_daily` 总行/`close_qfq` NULL/零价行；当日 `daily_close` 行数；当日 `market_state_daily` 状态/coverage/breadth_n；sector 覆盖率；`financial_history` 季报 notice_date NULL 数。
- 每日可跑，作为听诊器，不写库、不调 `init_database()`。

## 交付物

- `docs/audit-20261103-hermes.md`（本文件）
- `scripts/health_check.py`
- `scripts/regrab_eps.py`（设计+脚本，生产写由母方）

## 验证声明

- 未修改生产数据；脚本仅只读（health_check）或 dry-run 默认（regrab_eps）。
- 未跑 pytest/gate（遵循 AGENTS.md）。
