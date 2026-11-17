# report-t74.md — T74 前置审查（Hermes · T73 未交付）

## 范围
T73 尚未交付；本节先审 11-05 已上线的 6 处 M3 P0 修复（dd0c2c4/b4c6823/1af8ce0/94dcc50/dd76601/5c1c6b7），把会再犯的点先钉住。

## 发现

### P1 · session_fingerprint 含 init_positions/init_frozen，日内续跑会被自己拒
`runner.py:191-192` 把 `base.init_positions` 与 `mod.ptpaper.init_frozen` 都放进指纹；
而 `cli.py` 每一跳都从**当前账本**重读这两个量。第一跳若有任何成交，账本 total_volume /
available_volume 立即变 ⇒ 第二跳指纹漂移 ⇒ `SessionGate.check` 抛 `SessionGateError`（provider.py:163-167），
日内 cron 直接失败。现有测试没有覆盖 init_positions/init_frozen 与 intraday_resume 的组合。
**建议**：intraday 指纹排除这两个量，或把“期初”改为仅首跳传入、续跑从 state 恢复；至少补一条
“第一跳成交后第二跳仍能续跑”的行为测试。

### P1 · init_frozen 对齐时机存疑，可能静默 warn
`mod_entry.start_up` 里 `env.portfolio.get_position(...)` 设 `_non_closable`；但 start_up 早于
Portfolio 期初持仓构建时，拿到 None 就只 `logger.warning`，T+1 冻结对齐实际没生效
（T73 正在查 Portfolio/data_source 顺序，需一起验）。当前生产无告警可观测，建议把这条
warning 升级为 m3 verify 的 T+1 对齐检查项。

### P2 · get_bar 1d 聚合的 prev_close 口径
`data_source.py` 1d 聚合取 `sel[0]["prev_close"]`；若分钟 bar 该字段为 0/NaN，
init_positions 成本价会失真。需断言 `np.isfinite(prev_close)`，否则回退到前一日日线的 prev_close。

### P2 · 代码→交易所映射只判首位
`_code[0] in '69' → XSHG`；8/4 开头（北交所）会落 XSHE。若池子含北交所标的需修正；
至少加单测覆盖 6/0/3/688/8 前缀。

## 结论
T73 未交付；现有 6 处修复中，**指纹含期初量导致日内续跑拒跑**是必须先解决的 P1，
否则 T73 把 init_positions 修通后下一跳就会撞墙。
