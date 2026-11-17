# report-t79.md — 文档闭环（Hermes）

## 改动清单
1. `docs/refactor-plan-open-source.md`
   - §5 表格 M3 行：`cron 切 rqalpha 常驻（一天一进程）` →
     `cron 切 rqalpha 队列模式（engine --once 判定入队 → rq tick 每 5 分钟消费）`。
   - 新增 §5.10「M3 实现现状：队列模式」：写明偏离原因（R2 裁决 1d 不能每分钟跳、
     分钟会话常驻复杂度高）、数据流、幂等、固定 `var/rq_state/live`、验证入口与回退。
2. `AGENTS.md` Cron Integration
   - 规则引擎行注明“只判定入队，不直接成交”；新增 `rq tick` cron 行。
   - 新增「rqalpha 底座操作面（M3）」：判定/执行分离、状态目录、每日核对、回退开关。
3. `docs/migration-m3.md` Phase 2
   - 新增每日核对模板（盘前/首跳/盘中/盘后/异常/结论）与绿/黄/红判别口径；
     观察报告框架 `docs/worker/observation-m3-<date>.md`。

## 无矛盾自查
- `refactor-plan` §5.8 的 M3b“常驻分钟会话全量接管”是**历史裁决**；§5.10 明确
  实现偏离及原因，不再把“常驻”写成现状。
- `AGENTS.md` 的 cron 块与 `refactor-plan` §5.10 对“队列模式”的表述一致：
  `engine --once` 判定入队，`rq tick` 消费。
- `migration-m3.md` 的观察期模板使用 `m3 daily-check`（T80 交付物，合并后生效）；
  未把它写成当前已存在命令。
- 回退口径一致：先停 cron，再切 `executor: legacy` 或注释 `rq tick` 行；
  与 `migration-m3.md` §5.1 手工回退顺序一致。

## 残留
- T80 未合并前，`cli m3 daily-check` 尚不存在；文档已标注“T80 落地后生效”。
- T78/T80 的 CI/自动化细节由 T81 统一验收。
