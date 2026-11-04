# report-t61.md — m3 verify 红绿语义审查（Hermes）

## minute_feed
- 盘中无当日分钟线 → red；非盘中 → warn。语义合理，避免夜间假红。
- 采集 cron 装上后，盘中应变绿；若 15 分钟粒度仍红，需加密采集或降低 max_lag_min。

## heartbeat
- minute 阈值仅在 minute 心跳存在时启用（“装过才追问”），防止未装 intraday cron 的部署假红。
- 应跳未跳 → red；非应跳时段 → warn（明确“未判停滞”），避免假绿。

## raw_gate
- stale/未翻转 → red，on_red=“回退”。在母项目迁移期，5 只未翻转属母库参照滞后，
  立即回退过严；建议迁移期 on_red 改为“归因（母库迁移中）”，迁移完成后恢复“回退”。
- 但用户红线“不能缺数据”，strict 门禁仍应在白名单机制下管理。

## 结论
工具语义总体合理；raw_gate 在迁移期的 on_red 严重度建议可调，不改码。
