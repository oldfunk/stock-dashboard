# 文档地图

> 开工顺序：`iteration-plan-20261124.md`（当前队列）→ `ROADMAP.md`（里程碑）→
> `WORKER.md`（交付证据）→ 按需查契约/事故。完成后回填队列与 WORKER，不在多处重复写结论。

## 1. 当前执行

| 文件 | 作用 |
|---|---|
| `iteration-plan-20261124.md` | **当前迭代队列**（I1 基准 → I2 行业 → I3 收尾 → I4 治理 → I5 策略组合 → I6 审计/报告 → I7 运维/安全 → I8 公司行为） |
| `ROADMAP.md` | 里程碑与进度真相源（M1/M2/M3/M4 + 进度记录） |
| `WORKER.md` | 任务箱与交付证据（T 编号、验收、测试数） |
| `TODO-p5-p7.md` | 母库长线与 P5/P6/P7 状态 |
| `TODO-longterm.md` | 长线 backlog（v0.12/v0.13/阻塞/未排期） |
| `TODO-mother-integration.md` | 母库接入细项（多数已结，保留历史） |
| `release-v0.11.0-checklist.md` | v0.11.0 发布检查单（前置 3 个干净收盘链、证据表、回滚） |

## 2. 契约与设计

| 文件 | 作用 |
|---|---|
| `CONTRACT.md` | 子项目三表契约（拥有/只读/永不碰）+ 接缝清单（vendor 门禁用） |
| `mother-interface.md` | 母库只读接口、降级、来源标注、**价值策略位置/格式**、接口探针 |
| `strategy-contract.md` | 子项目自己的策略注册表/参数/自定义/离线运行 + 四层边界与资金分配 |
| `default-strategy.md` | general 默认通用中线策略（MA20/60 + 趋势/质量/风险） |
| `ai-autonomy.md` | AI 自主权边界：框架管安全，AI 管风格；健康度默认 advisory |
| `security-review.md` | 安全复核清单与攻防演练（无口令写入/CSRF/跨实例/Key 泄露） |
| `ledger-backup.md` | 账本备份/恢复/版本化与回滚步骤（B1） |
| `ops-runbook.md` | 每日自动备份/巡检 cron、告警处理与回滚（M4） |
| `matching-refinement.md` | 撮合细化设计（成交量/涨跌停排队/竞价）与回归清单（未改代码） |
| `research-process.md` | 参数研究流程（样本内→样本外→前向；不自动上生产） |
| `release-v0.12.0.md` | v0.12.0 发布说明草案（未打 tag） |
| `release-v0.12.0-checklist.md` | v0.12.0 发布检查单（等 12-01 收盘链） |
| `panel-information-architecture.md` | 面板信息架构与取舍（重要/可删/后置） |
| `ui-iteration-20261119.md` | 面板 P0/P1/P2 的实现契约与变更记录 |
| `architecture-map.md` | 模块依赖快照（2026-10-29 基线 + 11-24 复核计数） |

## 3. 事故与复盘

| 文件 | 内容 |
|---|---|
| `incident-2026-10-27.md` | 价格尺度事故（母库后复权混入执行价） |
| `incident-2026-11-18.md` | `rq:fill` 幂等键缺业务日 + tick 写回只 mark |
| `incident-2026-11-23.md` | 计划 #28 卖单被单轮上限丢弃 + 条件冲突；修复与引擎沙箱复验 |
| `worker/report-qoder-provenance-20261124.md` | Qoder 贡献溯源审计（作者身份 ≠ 推送者；无外部写权限证据） |

## 4. 历史/归档（不改写，仅追溯）

`m1-data-adapter.md`、`m1-signoff.md`、`m3-checklist.md`、`m3-observation-report.md`、
`migration-m3.md`、`migration-nec.md`、`refactor-plan-open-source.md`、`spike-m0-pi.md`、
`TODO-code-review-20261027.md`、`automation-audit.md`、`worker/` 下的任务报告。

## 5. 维护规则

1. **单一事实源**：里程碑只写 ROADMAP，交付只写 WORKER，当前队列只写 iteration-plan。
2. **不重复**：契约细节只写 mother-interface / strategy-contract / CONTRACT；其他文档引用。
3. **日期与版本**：状态变化时更新头部（当前版本、测试数、生产状态），历史记录追加不改。
4. **收尾**：完成一项后回填 WORKER 复选框 + 在 ROADMAP 进度记录加一行。
