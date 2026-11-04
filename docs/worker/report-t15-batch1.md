# report-t15-batch1.md — L2 对抗性狩猎 批次一

## 范围
`agent/loop.py`：`_plan` / `_execute_plan` / `_execute_actions` / cap 边界 / dry-run 分叉。

## 已查
- cap 边界：n 与 n+1（测试锁定，n=2 时第 3 单被 `超日内笔数上限` 拒，持仓只 200 股）。
- dry-run：不写 ledger、不落订单（测试锁定）。
- 计划消费：崩溃也消费（T16-6 锁定）。
- 尺度门禁：无基准 fail-closed（T16-3）；计划基准 vs 执行价偏离拒单（T16-1）。

## 阴性结果
- 未发现新的未报告缺陷；cap 逻辑与 dry-run 分叉与文档一致。
- `_execute_plan` 在 `_execute_actions` 抛异常时 catch 并消费计划，符合 consume-once 裁决。

## 后续批次
- 批次二：`integration/mother.py` 降级矩阵。
- 批次三：`cli.py` 编排。

## 测试
- `tests/test_t15_batch1.py` 3 passed。
