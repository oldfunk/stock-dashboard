# Hermes T8 复核报告（2026-10-27）

## 复核对象
- `caa7371` H-series 修复 + 锁定测试
- `ca6d503` M-series 修复

## 验证
- `pytest -q`：316 passed, 1 skipped（全绿）
- `tests/test_agent.py` + `tests/test_mother_contract.py`：69 passed

## H-series 结论
- H-1：已修，fail-closed 无基准拒单；测试锁定。
- H-2：已修，超切片条目入审计；测试锁定。
- H-3：锁定测试通过（计划加仓不被误拒）。
- H-4：已修，prices 提供时 fail-closed；空 dict 与 None 语义区分正确。
- H-5：**仍未修**。`loop.py:977` 仍无条件 `mark_plan_done`，半失败计划无法重跑。建议：仅 `_execute_actions` 返回 `ok=True` 时 mark_done；失败保留 pending 并落拒因。
- H-6：已修，本地回退排序 + asof 最新日；测试锁定。
- H-7：已修，绝对量级 sanity + 单测锁定。
- H-8：残余接受（TTL 兜底），与 WORKER 记录一致。

## M-series 结论
- M-1 mother-only 真开关：`source.py` mother 模式不再回退本地，fail-closed；`source_of` 对应返回 unavailable。符合 config 语义。
- M-2 质量 oplog 日内去重：需确认 `has_operation` 业务日判重路径；代码已按 result.date 判重。
- M-3 新股宽限 10 天：`quality.py` 使用 `first_d` + `(d-first_d).days > 10`；合理。

## 建议
1. H-5 开 `worker/h-5` 修 mark_plan_done 条件化，补失败重跑测试。
2. M-1 增加 `mother` 模式下 `source_of` 的单测（当前仅功能代码）。
3. 本报告已 push。
