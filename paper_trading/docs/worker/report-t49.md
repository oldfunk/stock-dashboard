# report-t49.md — T45 runbook + T46 工具 + T47 复核（Hermes）

## T45 runbook（docs/migration-m3.md）
- G1/G2/G3/G4 四缺口描述准确，证据链清晰；runbook 的回退顺序与 Phase 1 铁律一致。
- P0：G1/G2（新线不写账本/无 NAV）必须在 M3 前 closing；否则切换为空转。
- P1：G3 config 开关不存在——需先实现 `engine/config.py` DEFAULTS 白名单键。
- P1：G4 归档工具缺失——手工步骤风险高，建议小工具化。

## T46 工具（m3_verify.py）
1. `direction==-1` 判卖出：与 broker 约定一致（submit_order direction=-1 卖出），正确。
2. `entry_day` 盲区：新线若不写 `positions.entry_day`，T+1 检查永远查不出；
   建议 m3 verify 用 fills 推断当日建仓（fallback），或要求新线写 entry_day。
3. 母库 warn 松紧：raw_gate 在 incomplete_days 时给 warn 而非 red，符合迁移期
   现实，但用户红线“不能缺数据”下建议 M3 切换日将 warn 升级为 red。
4. 钱类项 fail-closed 良好；退出码 3 表达钱类红，cron 可区分。

## T47
- 未见 report-t47.md / 交付物；本报告仅覆盖 T45/T46。

## 结论
T45/T46 方向正确；T+1 entry_day 盲区为 P1，建议在 M3 前 closing 或加 fills fallback。
