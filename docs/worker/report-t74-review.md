# report-t74-review.md — T73/T71 交付复核（Hermes）

## 复核方法
worktree 检出 `origin/worker/t71-t73-init-positions`（3663be7），重跑全量与关键测试，
并逐条对照 report-t73/report-t71 的声明。

## 验证结果
- 全量：**806 passed / 11 skipped**（与报告一致）。
- `wiring_audit --quiet`：断层无，exit 0。
- 关键测试：`test_decision_gate_t71.py` / `test_init_align_t73.py` /
  `test_m3_verify_t46.py` / `test_engine_behavior_t68.py` 共 **75 passed / 1 skipped**。
- T73 根因成立：`parse_init_positions` 对 dict 返回 `[]`，字符串形态才解析；
  `init_positions_arg` 已改为字符串并排序稳定。
- T74 前置 P1 已修：`session_fingerprint(intraday=True)` 豁免
  `accounts/symbols/init_positions/init_frozen`，并保留 `_intraday_exempt` 自证；
  日模式仍锁基线。测试 `test_fingerprint_intraday_survives_ledger_drift` 通过。
- T+1 对齐时机已修：`POST_BEFORE_TRADING` + `POST_SYSTEM_RESTORED` 双挂，
  只加不减，`T1FreezeMissing` 后置闸 fail-closed；四场景 e2e + 脱钩拒跑测试存在。
- 账本口径收敛：`ledger_init_state` 统一现金/持仓/冻结，`920156.BJ` 映射正确；
  `ledger reset-state` 认 `live` 与 `<日>` 两个目录。

## 残留风险 / 建议
1. **P2 · 少仓闸只查“少”不查“多”**：`_check_positions_landed` 只报框架比账本少；
   框架多出账本没有的仓（状态污染的另一半）不会响。建议补“多仓也拒开”。
2. **P2 · `_apply_t1_freeze` 未校验 `acct is None`**：当前靠 plan 空 + 后置闸兜住，
   但错误信息会变成 `T1FreezeMissing`，不够直接；建议在 `acct is None` 时直接
   `InitPositionError`。
3. **P2 · 1d 聚合只查 `prev_close/limit_up` 有限性**：`limit_down` 仍可能 NaN；
   建议一并检查，或说明为何不需要。
4. **P2 · e2e `check_scene` 仍用旧的首字符映射拼 order_book_id**：测试标的不含北交所，
   当前无影响；建议改用 `markets.order_book_id`，避免把 T74 P2 的写法又抄一遍。
5. **待拍板 Q6 未决**：盘后意图“转次日”与 `expire_stale` 按日过期冲突；
   T71 已把两端钉住，需主 AI 裁决后翻对应测试。
6. **上线动作未执行**：分支未合 main；`var/rq_state/live` 仍是旧空仓状态，
   必须按 report-t73 §6 停 cron → `ledger reset-state` → 部署 → 恢复 cron。

## 结论
T73 修复方向与落地均正确，T74 前置 P1 已闭合；剩余为 P2 加固与 Q6 裁决。
合并前建议补“多仓拒开”与 `acct is None` 直接报错。
