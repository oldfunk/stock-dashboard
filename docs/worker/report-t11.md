# report-t11.md — H-5 consume-once 裁决复核（Hermes）

## 结论
**无异议，采纳 consume-once。**

## 论证
1. `_execute_plan` 在 `_execute_actions` 抛异常时 catch、落 `ai:decide` 错误审计、然后 `mark_plan_done`。
2. 反向风险（不消费 → 下 tick 重跑 → 已成交动作重复下单）比"崩溃后无法自动重跑"更严重，且重复下单直接损害账本。
3. 恢复路径清晰：人工重做计划；下一轮 tick 正常生成新计划，不自动重跑旧计划。
4. 测试 `test_plan_consumed_even_if_execution_crashes` 锁定 consume + 审计 + 不重跑。

## 残余建议（非阻塞）
- 崩溃发生在 `_execute_actions` 中途时，已成交动作不会回滚（符合 paper trading 不冲正的现有策略），但条件单（`_arm_conditions`）不会被挂；建议在 crash 分支额外记录 `conditions_not_armed`，便于人工补挂。不改变 consume-once 语义。

## 状态
T11 完成。
