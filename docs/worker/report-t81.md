# report-t81.md — M4 收尾审查（Hermes）

## 审查对象
- T78（CI 永久门禁 + 生产每日实跑）：`worker/t75-t80-wrapup`，`.github/workflows/test.yml`、
  `tests/ci_gate_selfcheck.py`、`deploy/daily-check.sh`。
- T79（文档闭环）：本分支 main 上的 `refactor-plan §5.10`、`AGENTS` 操作面、
  `migration-m3` 观察期模板；审查中又修掉两处残留“常驻”表述。
- T80（`m3 daily-check`）：`paper_trading/utils/daily_check.py`、
  `tests/test_daily_check_t80.py`、`deploy/daily-check.sh`。

## 验证结果
- 全量：**836 passed / 11 skipped**；`wiring_audit --quiet` 断层无，exit 0。
- `python tests/ci_gate_selfcheck.py`：`{"items":15,"rc":0,"forced_red_rc":3,
  "rollback_required":true,"red_ids":["init_align"]}`，退出码 0（自检 4 项全过）。
- `test_daily_check_t80.py` + `ci_gate_selfcheck.py` + `test_t75_hardening.py`：27 passed。
- 文档一致性：`refactor-plan` M3 行、§5.10、`AGENTS` 操作面、`migration-m3` 标题/正文
  均已改为“队列模式”；无残留“常驻一天一进程”当作现状的表述。

## T78 结论
- CI 各步骤独立退出码，门禁自检会主动构造“缺留痕”现场并验证非零退出，符合
  “门禁真跑”要求；四场景在 CI 无 rqalpha 时 skip，改由生产 `daily-check.sh` 跑，
  理由成立（GitHub runner 无 bundle/分钟线，硬塞即假绿）。
- `daily-check.sh` 彩排抓出并修掉两个“门禁谎报”洞（JSON 解析丢结果、成功时 RC=1），
  这一层有真实证据。

## T80 结论
- `m3 daily-check` 退出码与 `m3 verify` 同尺（0/1/3），只读，含 init_align/carry_over/
  intent_chain 三项 rq 执行面；`--expect-work no` 避免空队列假红。
- 测试覆盖绿/红/skip 三态，钱类红 ⇒ `rollback_required`。

## 残留（P2，建议合并前补）
1. **`check_intent_chain` 名不副实**：docstring 说“落账笔数与 executed 数一致”，
   代码只统计 `filled` 数量，没有与 `executed` 数比较，也没有在不等时变红/黄。
   建议补一致性断言，或改掉过度承诺的注释。
2. **`check_carry_over` 对“未顺延的过期未决意图”返回绿**：它把 `is_carried=False`
   的 pending/queued 计入 `stale` 但结论仍是 green。若 `expire_stale` 漏跑，这一项
   不会响。建议：有 stale pending/queued 时至少 warn。
3. **`check_init_align` 仍只查“少”不查“多”**（T74 已提）：框架多出账本没有的仓不响。
   建议补“多仓也拒开”。
4. **`deploy/daily-check.sh` 的 `ENG` 路径硬编码** `/home/nec/venvs/rq-engine/bin/python`：
   彩排/换机时只能改 `PT_ROOT`，引擎 venv 路径不可配。建议加 `PT_ENGINE_PY`。
5. **`daily-check.sh` 用 `cp` 复制 `data.db`**：复制过程中库可能被写，四场景读到
   不一致快照。建议改用 `sqlite3 .backup` 或 `VACUUM INTO`。

## 结论
T78/T79/T80 主体达标，M4 验收 ③④② 已有可执行证据；残留为 P2 加固项，
不阻塞合并，但建议在观察期前补上 1/2/3 三条 daily-check 判别力缺口。
