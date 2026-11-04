# report-t33.md — T13 §5.4 拍板与原型双重审查（Hermes）

## 9 条 G-T13 门禁可测性
1. 口径标签：可测（适配器启动时 assert caliber=="raw"，否则拒绝）。
2. 因子新鲜度：可测（factor 末日 < 今日 ⇒ 拒跑）。
3. band 完整：可测（bar 缺 limit_up/limit_down ⇒ 拒跑，T13-S7 已证 fail-open）。
4. provider 单测：可测（重启逐字段相等）。
5. 幂等：可测（同日重跑拒）。
6. 结算唯一：可测（SETTLEMENT 计数 =1，跨进程续跑需避免重复结算）。
7. 空转断言：可测（handler_dates 非空 + 正向对照）。
8. 拒单可审计：可测（拒单原因落 Order/日志）。
9. 一天一进程：可测（进程生命周期）。

## 第三条 P0：close_raw 新鲜度/一致性
- close_raw 来自 bundle/母库；与 qfq 的一致性校验缺位。建议适配层在启动时
  对最近 N 日 raw vs qfq 做尺度 sanity（[0.9,1.1]），因子外推段拒绝。

## FilePersistProvider 审查
- 原子写用 os.replace（同文件系统原子），但无 fsync；崩溃可能留下部分写入的 tmp。
  建议：write → fsync → os.replace → fsync dir；并加 checksum 校验。
- 指纹闸 fail-closed 良好；should_resume/should_run_init 恒 True（死接口），
  不应作为恢复判定依据。

## SqliteDayBarStore band 合规
- round2（floor(x*100+0.5)/100）正确。
- band_of：ST 返回 0.05 **不符合 AGENTS 2026-07 新规（ST 主板 10%）**，需修正为
  ST 也按板块幅（主板 10%），仅北交/创业/科创按原规则。
- 688/300/8/4/6/0 前缀覆盖基本正确，但 ST 特判错误是合规缺陷。

## 结论
T13 原型可用，但 ST band 与 fsync/checksum 需在产品化前修正；close_raw 新鲜度
作为第三门禁。M2 动工前清零 P0。
