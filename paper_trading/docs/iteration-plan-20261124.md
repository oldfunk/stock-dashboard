# 迭代计划（2026-11-24 起）

> 本文是**当前执行队列**；里程碑看 `docs/ROADMAP.md`，交付证据看 `docs/WORKER.md`，
> 长线母库事项看 `docs/TODO-p5-p7.md`。每次开工先读这三份 + 本文，完成后回填。
> 纪律：一次只动一个变量；每项带验收命令；不静默降级；发布权归子项目。

## 0. 基线（2026-11-28）

| 项 | 值 |
|---|---|
| 版本 | **v0.12.0（2026-11-28 发布；post-release 观察 11-30/12-01）** |
| 测试 | **1107 passed / 13 skipped** |
| 生产 | :8080 已部署 v0.12.0；:8081 待母侧 vendor v0.12.0 |
| 母项目契约 | issue #10（基准/行业）**已关闭**；issue #11（v0.10.1 通知）**已关闭** |
| 探针 | `data contract` 生产实测 `ok=true`（策略 20 只 / 行业 5527 行 / 指数 5 键） |
| 已知黄 | `raw_gate`（母库参照滞后）；`parity_trace` 已退役（skip，不再每日黄） |
| 已完成 | I1 基准 / I2 行业 / I3 收尾 / I4 治理（部分） |
| 下一项 | **post-release 观察 11-30/12-01 + 母侧 vendor**；撮合细化另开分支 |

## 1. 执行队列（按顺序）

### I1 — CSI300 基准曲线（P2-1，目标 v0.11.0）

- **做什么**：服务端 `/api/benchmark`；NAV 与 `sh000300` 按**首个共同日**归一化；
  日超额、相对回撤；`asof/stale/unavailable` 贯通。
- **UI**：收益曲线叠加基准 + 超额 KPI + stale 徽标；缺日虚线，不插值。
- **不做**：`sh000922` 不作主基准（母库该键可能 stale）；不接新数据源。
- **验收**：同起点同日对齐；母库缺数据显式“不可用”；单测 + 生产截图；
  `data contract` 仍 `ok=true`。
- **状态：✅ 2026-11-24 完成**。`/api/benchmark` 上线；生产实测 28 个共同交易日、
  组合 −2.29% vs 基准 −8.81%、超额 **+6.52pp**、基准 MDD 9.25%；
  全量 1009 passed / 13 skipped；页面 0 console error、800px 无横向溢出。

### I2 — 行业分布（P2-2，目标 v0.11.0）

- **做什么**：服务端 `/api/sectors` 聚合持仓行业；`stock_snapshot.sector` 缺失归“未知”；
  UI 纯 SVG 条形（权重 + 市值）。
- **验收**：各桶市值之和 = 持仓总市值；NULL 不回退上一行业；单测 + 三档宽度。
- **状态：✅ 2026-11-24 完成**。`/api/sectors` 上线；生产 6 个行业、覆盖 8/8、
  合计 90,420 = 持仓市值；全量 1013 passed / 13 skipped；800px 无页面横向溢出。

### I3 — P2 收尾 + 探针上屏（目标 v0.11.0）

- `fills` 导出按钮补齐；分时/净值口径一致性；窄屏与深浅色检查；
- 运维页“数据来源”增加 `data contract` 结果（`ok/stale/errors`），只读；
- 验收：页面门禁 + 截图；探针在面板可见。
- **状态：✅ 2026-11-24 完成**。成交记录（含 CSV）+ 运维页契约卡已上线；
  contract 3 组 OK、fills 30 笔；800px 无页面溢出、深色主题正常；全量 1016 passed。

### I4 — 可靠性与发布治理（目标 v0.11.0 / v0.11.x）

- 影子对账 cron：装上（15:45）或正式退役 `parity_trace`，二选一；
- T96 17:00 qfq sync：观察 11-24/25 后决定；验收：连续 3 日计划 `freshness` 无跳过；
- `deploy.sh` 抗中断：gate 结果落文件、可重入（2026-11-24 本地会话断开的教训）；
- 治理：`main` 分支保护 + `v*` tag 保护；母项目 `paper-upstream` 只读；
  `MERGE_GUIDE` 写死“下游不推上游/不建 tag/不发 Release”；
- 验收：保护规则生效截图；协议条款入文档。
- **状态（2026-11-24，部分完成）**：
  - 影子对账：✅ **退役**（缺留痕由 warn 改 skip；恢复需 `SHADOW_PARITY_ENABLED=True` + 15:45 cron）；
  - 分支/tag 保护：✅ `main` 禁强推/禁删（admins）、`v*` tag ruleset active；
  - 部署抗中断：⏸ 母侧 `deploy.sh` 本次不改；操作口径改为“gate 输出落远程文件可续读”；
  - 母项目上游只读 / MERGE_GUIDE 条款：**用户决定不做**。

### I5 — 多策略与组合风控（目标 v0.12.0）

- 方案层资金分配 + 组合层统一风控入口；
- 策略健康度：滚动超额、回撤、换手、费用拖累；**作为 AI 输入/建议**，
  是否降级为观察由 AI 风格或用户显式策略决定（默认 `advisory`，不自动拦买）；
- 验收：回测/实盘同口径对照；单测；不改变现有默认方案行为。
- **状态（2026-11-28，I5a 完成）**：健康度已上线（`strategy health` +
  `/api/strategy-health` + 运维页卡）；生产当前 `demote`（20 日超额 −5.14%），
  但只读、不自动改交易；11-30 计划 #32 的买单不受影响。
- **I5b 默认通用策略（2026-11-28 完成）**：旧默认 MA5/20 → **MA20/60 +
  趋势/质量/风险 playbook**；`GENERAL_RULES` 由 `SCHEME_TMPL` 注入 AI prompt；
  `general` 方案 title/desc/exits/signal 同步；详情 `docs/default-strategy.md`。
- **I5c 完成（2026-11-28）**：
  1. ✅ 健康度进 AI 上下文（advisory），AI 自己决定“继续/放缓/停手”并写理由；
  2. ✅ `health_policy` 配置面（off/advisory/enforce，默认 advisory）+ 测试；
     `enforce` 是**用户显式策略**，不是框架默认；
  3. ✅ 多策略资金分配（`strategy.allocations`；额度=weight×NAV；计划/执行两处闸；
     卖出不受限；配置后买入缺 strategy 拒单）；
  4. ✅ 统一风控入口 + 两级预算：分配层只做额度，撮合/账户级风控仍走同一
     `RiskManager` + 账本；组合上限由既有 `max_total_position_pct` 兜底；
  5. ✅ AI 风格审计：`agent replay.health_review` 展示 demote 输入、AI 选择与
     是否回应（继续/放缓/停手）；计划/决策结果持久化健康度与额度快照。

### I6 — AI 分工审计 + 报告（目标 v0.12.0）

- AI 提名 → 程序校验 → 规则择时的全链路回放（现有 `agent replay` 补齐）；
- 日/周报 CLI（持仓、成交、归因、风控事件），复用 CSV/性能数据；
- 验收：任意一天可回答“为什么买/没买/卖”；报告可导出。
- **状态：✅ 2026-11-28 完成**：
  - `agent replay` 增加 `health_review`/`strategy_allocations`/每动作 strategy；
  - `report daily|weekly --out`（NAV/成交/标的级归因/风控事件/健康度变化）；
  - `audit day --date` 串计划/决策/意图/成交/条件单，未成交列 evidence_gap；
  - 测试 `test_health_replay_t115.py`、`test_report_t116.py`、
    `test_audit_day_t119.py`。

### I7 — M4 运维与审计（目标 v0.13）

- 账户/风控状态备份恢复演练；`paper_account.db` 迁移工具与版本化；
- 审计报告（任意一天）；安全复核（面板/Key/口令）；
- 验收：演练记录 + 回滚步骤 + 审计样例。
- **状态：✅ 2026-11-28 完成**：
  - `ledger backup/inspect/restore/migrate/drill`（sqlite backup API；
    restore 默认 dry-run，`--apply` 前自动安全副本；`schema_meta` 版本化，
    未知高版本拒绝）；
  - `audit day`（B2）；`security audit` + `docs/security-review.md`（B3）；
  - 测试 `test_ledger_backup_t118.py`、`test_security_t120.py`。

### I8 — M1.5 公司行为（目标 v0.13，人工优先）

- 分红税口径建模与调整建议（只建议，人工确认后落账）；
- 自动除权除息仍不自动改账本，保持 `corporate apply` 人工口；
- 验收：建议清单 + 人工落账留痕 + 单测。
- **状态：✅ 2026-11-28 完成**：
  - `corporate tax --symbol --ex-date --cash-per-share`：≤30 天 20%、
    31–365 天 10%、>365 天 0%；缺 `entry_day` 不按 0 税；
  - `corporate suggest`：母库 `dividend_annual` 优先，母库无除权日时明确
    `needs_ex_date`；`--akshare` 才访问外网；只输出建议与 `corporate apply` 命令；
  - 测试 `test_corporate_t121.py`。

## 2. 发布节奏

| 版本 | 范围 | 前置条件 |
|---|---|---|
| v0.10.1 | 第二实例面板修复 + 探针 | **已完成**（2026-11-24） |
| v0.11.0 | I1–I3（+ I4 能做完的部分） | 3 个收盘链绿；`action_funnel` 绿；I1/I2 验收 |
| v0.12.0 | I5–I6 + A/B + C1/C3 + 运维/面板/研究 | **2026-11-28 已发布**（用户加速；post-release 观察 11-30/12-01） |
| v0.13.0 | I7–I8 + 撮合细化 | 演练/审计样例齐全；撮合改动单独观察 |

规则：tag/Release 只从子项目发；母项目只 `subtree pull <tag>`；不提前发版；
**15:30–19:00 禁止部署重启**；发版前跑全量 + 生产 gate。

## 2.1 v0.12 发布准备（2026-11-28）

- ✅ `docs/release-v0.12.0.md` + `docs/release-v0.12.0-checklist.md`（草案，未打 tag）；
- ✅ 撮合细化设计与回归清单 `docs/matching-refinement.md`（未改执行代码）；
- ⏳ 等 12-01 收盘链（新默认策略 16:30 生成计划）+ 12-02 执行证据；
- ⏳ 12-01/02 无红后 bump `0.12.0`、tag、Release，再交母侧 vendor。

## 3. 已定决策（避免反复）

1. **基准**：`sh000300` 主基准；`sh000922` 可选且必须显示 stale；同起点归一化；缺日虚线。
2. **行业**：`stock_snapshot.sector` 原文展示；NULL → “未知”；不猜测、不回退。
3. **探针**：只读形状/取值/新鲜度；形状错误 exit 3；母库不可读 `available=false`；
   接口由母项目建，子项目不改母代码。
4. **面板口令**：内置方案 `mother/general` 且不改指令 → 免口令；`custom`/改指令 → 要口令。
5. **执行顺序**：卖 → 买 → hold；`action_funnel` 对“管道丢单”报红。
6. **发布权**：子项目打 tag/发 Release；母项目仅 vendor；禁止反向 push。

## 4. 阻塞与外部依赖

| 项 | 状态 |
|---|---|
| P5.5 AI 成绩校准 | ✅ 2026-11-28：`strategy calibration` + `ai_scorecard`；母库 390 条可用样本（旧样本无 verdict 时按 signal+20日前向收益），BUY 23 条需继续观察 |
| P7.4 契约测试进母 CI | 母侧已跑 vendor 套件（含契约测试，失败不阻塞）；是否改为阻塞由母侧定 |
| 撮合细化（成交量/涨跌停排队/集合竞价） | ROADMAP M2 简化项，排在 I7 之后 |
| 母库 `raw_gate` 参照滞后 | ✅ 2026-11-28：`daily_close` 补参照后生产 `raw-coverage --gate` 0 violations |
