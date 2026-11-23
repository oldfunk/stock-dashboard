# 文档地图（先读这一页）

> 创建：2026-11-19 · 目的：这个仓有 30+ 份文档，真相源、规程、历史报告混在一起。
> **判断一条信息是否可信，先看它属于哪一类**：真相源随代码维护；快照/报告只在当天成立。

## 1. 真相源（与代码同步维护，冲突时以这里为准）

| 文件 | 回答什么问题 |
|---|---|
| `architecture.md` | 系统长什么样：流水线步骤、模块边界、48 张表、数据源注册表、子项目契约与运行契约（§9.7） |
| `iteration-log.md` | 怎么走到今天的：每日改了什么、为什么、验证证据、门禁基线变化 |
| `handoff.md` | 下一班怎么接手：最后状态 / 下一步 / 已知隐患（每班重写三段） |
| `roadmap.md` | 往哪走：M1–M6、价值方法论 Phase、P1/P2 的完成与冻结状态 |

## 2. 规程与契约（改动前后都要遵守）

| 文件 | 回答什么问题 |
|---|---|
| `subproject-protocol.md` | 与 paper-trading 的单向 tag 流、bug 分诊、vendor 门禁、hotfix 例外 |
| `development-workflow.md` | 开发期怎么验证：生产 worktree gate / 合成环境 / 发布才走 deploy |
| `agent-api.md` | 外部程序怎么调这个系统（只读数据 API + 触发） |
| `value-investing-methodology.md` | 价值投资方法论的完整方案（P0-1…P0-10 的目标/数据源/验收） |
| `tasklist.md` | 上述方案的进度勾选（含 Research OS 的 R0–R6 状态） |
| `scheduled-tasks.md` | 母亲自运维的参考设计（巡检/周复盘/月分析）；**参考而非已安装** |

## 2.5 进行中的工序

| 文件 | 回答什么问题 |
|---|---|
| `remediation-20261121.md` | **修复工序**：A 账本/数据正确性 → B 纸盘可度量运行 → C 盘中纪律 → D 研究闭环与接管门槛；含验收、回滚、节奏与"明确不做" |

## 3. 设计文档（方案本身，随实现演进）

| 文件 | 回答什么问题 |
|---|---|
| `paper-bridge.md` | **子项目↔母项目只读桥**：为什么同机 SQLite 直连、契约 v1（账户/持仓/计划/成交/NAV/分钟线）、实例路径与失败降级、E0–E4 落地 |
| `close-review-design.md` | **收盘检阅与投资笔记融合（v2）**：AI 自主发布（无人工闸）、新闻不进交易、纸盘只读转接口、周频交易、盘中仅 P0/P1 重大事件；F0–F2 与验收 |
| `research-os.md` | 研究 OS 的分层与判据链设计（预注册 → CI → Bonferroni → 敏感性） |
| `research-os-tasklist.md` | 研究 OS 任务清单；**未勾选 ≠ 未实现**，勾选前按验收命令复核 |
| `ai-model-glm47-flash.md` | 当前模型通道的参数与实测（thinking / json_mode / max_tokens） |
| `strategies-dry-run.md` | 策略 dry-run 的记录（历史，结论以 iteration-log 为准） |
| `paper-trading.md` | M4a 自研虚拟盘的历史选型（**已废**，现行是 M6 全量合并） |

## 4. 专题报告与取证（快照，结论只在报告当天成立）

| 文件 | 主题 |
|---|---|
| `no-data-inventory.md` | no_data 归因清单（10-30 快照 + 11-19 复核节） |
| `field-coverage.md` | 字段覆盖率（10-30 快照） |
| `automation-audit.md` | 自动化链路审计（10-26） |
| `audit-20261026.md` / `audit-20261027-hermes.md` / `audit-20261103-hermes.md` / `audit-20261104-hermes.md` | 各轮代码/交付审计 |
| `u-review-20261103.md` | U 系列验收复核 |
| `em-deep-field-gap-20261112.md` | EM 深层字段缺口的取证 |
| `em-incremental-gate-20261105.md` | EM 增量省量闸 |
| `h001-riskoff-exposure-20261104.md` / `h2-fixes-20261105.md` / `r2-final-review-gaps-20261105.md` | 研究 OS 的假设复判与修复记录 |

**读法**：报告里的数字（覆盖率、行数、只数）是当天的；要今天的数就重跑对应脚本
（例如 `python scripts/audit_no_data.py`、`python scripts/report_field_coverage.py`），
不要引用报告表格当现状。

## 5. 维护规则

1. 新文档先想清楚它属于哪一类，并在本页登记；否则三个月后没人知道它是否还成立。
2. 快照类文档**只追加"复核"节**，不改历史结论；真相源类文档必须随代码同 commit 更新。
3. 同一信息只写一处：表结构写在 `architecture.md`，别再在报告里复制一份。
