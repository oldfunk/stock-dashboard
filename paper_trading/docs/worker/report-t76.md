# report-t76.md — T75 复核 + 明日生产观察清单（Hermes）

## T75 复核（worker/t75-t80-wrapup，1cb2061）
- 全量：**836 passed / 11 skipped**；`wiring_audit --quiet` 断层无，exit 0。
- `test_t75_hardening.py` + `test_market_caliber_t59.py`：**29 passed**。
- ① `prev_close` 回退：`aggregate_day_from_minutes` 抽到 store，回退取上一交易日末根 close，
  band 用 `bands.limit_prices` 现算，note 写 `store.problems` + warning。方向正确。
- ② 前缀覆盖：6/0/3/688/8/92 全钉；北交所走 `unsupported` 如实剔除，不静默。

## 残留（P2，建议合并前补）
1. **正常路径仍可能交出 NaN `limit_down`**：`aggregate_day_from_minutes` 只检查
   `prev_close` 与 `limit_up` 有限；`limit_down` 直接取 `sel[0]`。若 `limit_up` 有限而
   `limit_down` NaN，仍会返回 NaN 带。T74 P2 点的是同一处，这次没补全。
   建议：`limit_down` 一并进有限性判断，缺失就走回退。
2. **OHLC 本身未做有限性检查**：`open/high/low/close` 若为 NaN 也会被交出去；
   至少 `close` 应有限（成本价/涨跌停都依赖它）。
3. **回退假设 bars 已按 datetime 排序**：`earlier[-1]` 取“最后一天”依赖顺序；
   store 当前有序，但函数没有断言。建议加 `assert` 或显式排序。

## Q6（顺延）复核
- `is_carried` 按 `created_at` 时刻 >15:00 且 `day` 是下一交易日才顺延；
  日历不可信则不过期、维持旧保守行为。逻辑自洽，只顺一天。
- `expire_stale` 改为全量扫描 pending/queued 后逐条判断，性能可接受。
- 注意：`created_at` 恰好 15:00 不顺延（`<= CLOSE_TIME`），与“收盘后”口径一致。

## 明日生产观察清单（T77 用）
### 盘前
- [ ] 已按 report-t73 §6：停 `rq tick` cron → `ledger reset-state --only rq_session_state`
      挪开 `var/rq_state/live` → 部署分支 → 恢复 cron。
- [ ] `var/rq_state/live` 不再有旧 `portfolio.state`（或已改名 `*.pre-reset-*`）。
### 首跳
- [ ] `evidence.init_align.kept` 含账本全部持仓；`dropped_silently` 为空。
- [ ] `evidence.frozen_align.applied` 含 300390 等冻结仓；`closable` 为 0。
- [ ] 无 `InitPositionError` / `T1FreezeMissing`；`m3 verify` 无新增红项。
- [ ] 002192 顺延意图按 Q6 执行（若已入队）：`is_carried` 为真，次日首根 bar 后成交/拒单有归因。
### 盘中
- [ ] 新意图全链：`rq_intents` → `engine_triggers` → `orders/fills` → `op_log` → 账本。
- [ ] 无重复 NAV（`nav_history` 当日一行）；无 `intent_ledger_conflict` 新增。
### 盘后
- [ ] `m3 verify` 全项 + `daily-check` 全绿/黄有解释；`t1_unfreeze_overdue` 不新增。
- [ ] 若出现 P0：停 cron，保留现场（state/evidence/op_log），不许直接改生产账本。
