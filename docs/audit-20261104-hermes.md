# Hermes H2 交付（2026-11-04）：H-001 / risk_off 暴露类审计

> 分支：review/20261104-hermes-h2 · 范围：d6c32c2..7f8d283 · 只读。

## H2-a 审计结论

### P1（应修）

#### P1-H2-1 `_join_sector_size` 用 mutable latest snapshot（PIT 漏洞）
- 位置：`src/research/experiment.py:288-296`（`_join_sector_size`）
- 证据：`SELECT sector, market_cap FROM stock_snapshot WHERE code = ? ORDER BY snapshot_date DESC LIMIT 1`，未按 `analysis_date` 截断。
- 问题：实验分域（size/sector）对历史分析行使用**当前最新快照**的行业/市值，存在前视与幸存者偏差；size 分档在不同时间点应使用入场时点的市值，当前实现把未来信息灌进过去，且退市股快照可能缺失。
- 修法：改为 `snapshot_date <= analysis_date ORDER BY snapshot_date DESC LIMIT 1`（与 `_block_snapshot` 的 PIT 口径一致）；缺失时 size=None 并在 split_stats 中显式标 "unknown"。

#### P1-H2-2 regime 分域 silently 吞掉 None 桶
- 位置：`src/research/experiment.py:395-400`（`_tier_metrics` split_stats 构建）
- 证据：`bucket.setdefault(str(r.get(key)), ...)`；regime 缺失时 key 为 `"None"`，但报告层可能把该桶忽略。
- 问题：H-001 regime 分域结论（"≥40 档 17pp 全部来自 risk_off"）若 market_state_daily 在分析日期缺失（回填缺口），这些样本会落入 None 桶而非 risk_off，可能夸大 risk_off 集中度。应明确输出 None 桶样本数与 CI。
- 修法：split_stats 同时输出 `n` per bucket；notes 中注明 regime 覆盖率（有 regime 的行数 / 总行数）。

### P2（改进）

#### P2-H2-1 holdout 切分用字符串日期比较
- 位置：`src/research/experiment.py:383-386`
- 证据：`cutoff = (...).isoformat()`；`est_rows = [r for r in rows if r["analysis_date"] <= cutoff]`。
- 问题：analysis_date 若含时间部分或格式不一致，比较可能失真。建议统一 `date.fromisoformat` 后比较。

#### P2-H2-2 `_notes` 用全市场 instrument 数作分母
- 位置：`src/research/experiment.py:348-352`
- 证据：`SELECT COUNT(*) AS n FROM instrument`。
- 问题：分母是当前 instrument 表行数，不是分析期 universe，幸存者偏差被弱化表述。应改为"分析期 distinct stock_code 数 / instrument 当前数"。

## 验证声明

- 只读；未跑 pytest/gate。
