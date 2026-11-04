# Hermes H3 交付（2026-11-05）：批16-a/b 审计

> 分支：review/20261105-hermes-b16 · 范围：worker/qoder-20261105-b16（b272889 + 2bb056d）· 只读。

## 结论

批16-a/b 代码审查通过，未发现阻断性缺陷；H2 四点全部落地且与审计口径一致。

### 批16-b（H2 四点）
- P1-H2-1：`_join_sector_size` 已加 `snapshot_date <= analysis_date`；`_size_bucket` 把缺市值归 None 而非 small。
- P1-H2-2：`_bucket_label` 统一 unknown 桶；split_stats 每桶 `{mean, n}`；notes 补分域覆盖率与 PIT 快照说明。
- P2-H2-1：holdout 按 date 比较；不可解析行留在估计集并计数；`_join_regime` 截日期。
- P2-H2-2：notes 分母改为"分析期出现 X 只 / instrument 当前 Y 只"，明确非覆盖率。
- 同类第 5 处：`_sole_benchmark`/`_load_outcomes`/`_notes` 统一 `SUBSTR(analysis_date,1,10)`，避免单日窗口静默丢样。

### 批16-a（增量仅缺字段才拉）
- `_em_sheets_needed` 判据合理：最近 8 期窗口、按报告期集合判定、骨架为空不拉。
- `get_deep_field_dates` 一次批量读，placeholders 安全。
- force_full 走 (True,True)，保持全量重采语义。
- COALESCE 保证跳过不抹已有值（批15 修的列保护继续成立）。

### 小建议（非阻断）
- `_em_sheets_needed` 的 `window <= 0` 分支把全部报告期纳入判定，生产上应始终用默认 8；建议调用点不传 window=0。
- `get_deep_field_dates` 仅查 interest_coverage/fcf；若未来新增深度字段，需同步扩展。

## 验证声明
- 未跑 pytest/gate（遵循 AGENTS.md）；Qoder 自报 1247 passed / 12 failed / 3 skipped 与本仓库固定项一致。
