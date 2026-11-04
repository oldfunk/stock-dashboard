# report-t24.md — L2 崩溃恢复对抗（Hermes）

## P0
1. **SIGKILL/SIGTERM 下计划残留 pending → 下 tick 重复下单**
   - `_execute_plan` 的 `mark_plan_done` 在 `finally`，但 SIGKILL 不执行 finally；
     默认 SIGTERM 也不保证 finally。进程被杀后计划仍 pending，下一轮 tick
     会再次 `_execute_plan`，已成交动作可能重跑（与 H-5 consume-once 的本意相悖）。
   - 建议：执行前把计划状态改为 `running`（或新增 `in_progress`），崩溃后不自动重跑；
     恢复由人工触发；或在启动时检测 `running` 计划并只读告警。

## P1
2. **SQLite 事务边界安全**：broker 每操作一连接 + context commit/rollback，
   SIGKILL 未提交事务回滚，库保持一致（good）。
3. **run_lock 自动接管**：PID+启动标识可识别死进程；SIGKILL 残留锁可接管（good）。
   但 `_is_stale` 在 token 为空时仅按 mtime 1800s，老格式锁可能延迟接管。
4. **cron 重叠**：run_lock 30s 超时抛 RuntimeError，cron 记录错误；无死锁。
5. **engine --once 中途被杀**：锁由 finally 释放；SIGKILL 残留可接管；账本事务安全。

## 建议
- 为计划增加 `running` 状态与崩溃检测；SIGTERM 安装 handler 调用 mark_plan_done。
- 保留 consume-once 语义，但把"消费"前移至执行开始。
