# 定时任务配置（Hermes 自动执行）

> 创建：2026-09-20 · 上游：`architecture.md` · 状态：设计中
> 本文件定义 Hermes 定时任务的结构化配置。Hermes 读取本文件 → 创建 kanban 任务 → 执行 → 输出报告。

## 任务列表

### 1. 每日巡检（daily-inspection）

| 字段 | 值 |
|---|---|
| **任务名称** | 每日巡检 |
| **执行频率** | 工作日 15:30（交易日后） |
| **执行内容** | 1. 检查流水线运行状态（最近 run_log）<br>2. 数据质量检查（screening_result 覆盖率、stock_snapshot 更新）<br>3. 服务健康检查（systemctl is-active、API 响应）<br>4. 异常检测（失败 run、缺失数据） |
| **输出格式** | `docs/reports/daily-YYYYMMDD.md` |
| **完成标准** | 报告文件存在，包含：运行状态、数据质量、服务健康、异常列表 |
| **kanban 任务** | 自动创建，assignee=default，priority=1 |

### 2. 每周复盘（weekly-review）

| 字段 | 值 |
|---|---|
| **任务名称** | 每周复盘 |
| **执行频率** | 周六 16:00 |
| **执行内容** | 1. 观察池复盘（WatchlistReviewer.review()）<br>2. 论点漂移检测（_detect_pool_drift）<br>3. 投资笔记撰写（ai_journal 新增条目）<br>4. 候选股表现分析（本周 vs 上周评分变化） |
| **输出格式** | `docs/reports/weekly-YYYYMMDD.md` |
| **完成标准** | 报告文件存在，包含：观察池状态、漂移检测结果、投资笔记链接、候选股表现 |
| **kanban 任务** | 自动创建，assignee=default，priority=2 |

### 3. 每月分析（monthly-analysis）

| 字段 | 值 |
|---|---|
| **任务名称** | 每月分析 |
| **执行频率** | 每月 1 号 10:00 |
| **执行内容** | 1. 候选股表现分析（本月筛选结果 vs 历史）<br>2. 策略有效性评估（多策略命中分布）<br>3. 数据源健康度（S1-S7 可用性）<br>4. 系统资源使用（DB 大小、日志量） |
| **输出格式** | `docs/reports/monthly-YYYYMM.md` |
| **完成标准** | 报告文件存在，包含：候选股表现、策略评估、数据源健康、资源使用 |
| **kanban 任务** | 自动创建，assignee=default，priority=3 |

## 报告模板

### 每日巡检报告模板

```markdown
# 每日巡检报告 — YYYY-MM-DD

> 生成时间：YYYY-MM-DD HH:MM:SS · 任务：daily-inspection

## 1. 流水线运行状态

- 最近 run_id：...
- 状态：completed/failed/running
- 开始时间：...
- 完成时间：...
- 全市场股票数：...
- 候选股数量：...

## 2. 数据质量

- screening_result 覆盖率：X/Y（%）
- stock_snapshot 最后更新：...
- 缺失财务数据的股票数：...

## 3. 服务健康

- stock-dashboard.service：active/inactive
- API 响应：/api/indices 200 OK
- 数据库大小：...

## 4. 异常列表

- [ ] 异常 1：...
- [ ] 异常 2：...

## 5. 建议

- ...
```

### 每周复盘报告模板

```markdown
# 每周复盘报告 — YYYY-MM-DD

> 生成时间：YYYY-MM-DD HH:MM:SS · 任务：weekly-review

## 1. 观察池状态

- 当前池内股票：X 只
- 本周调入：...
- 本周调出：...

## 2. 论点漂移检测

- 打脸回归：X 只
- Signal 反转：X 只
- 结论反转：X 只

## 3. 投资笔记

- 本周笔记：[标题](链接)
- 覆盖股票：X 只

## 4. 候选股表现

- 本周评分变化：...
- 新增候选：...
- 退出候选：...

## 5. 建议

- ...
```

### 每月分析报告模板

```markdown
# 每月分析报告 — YYYY-MM

> 生成时间：YYYY-MM-DD HH:MM:SS · 任务：monthly-analysis

## 1. 候选股表现

- 本月筛选次数：...
- 平均候选股数：...
- 评分分布：...

## 2. 策略有效性

- 成长策略命中：X 次
- 红利策略命中：X 次
- 反转策略命中：X 次

## 3. 数据源健康

| 数据源 | 状态 | 成功率 |
|---|---|---|
| S1 腾讯行情 | 正常 | 95% |
| S2 业绩报表 | 正常 | 90% |
| ... | ... | ... |

## 4. 系统资源

- 数据库大小：...
- 日志文件数：...
- 磁盘使用：...

## 5. 建议

- ...
```

## Hermes 执行流程

1. **读取本文件** → 解析任务配置
2. **创建 kanban 任务** → 每个任务一个卡片
3. **执行任务** → 调用对应函数/脚本
4. **生成报告** → 写入 `docs/reports/`
5. **更新 kanban** → 标记完成，附报告链接

## 约束

- 报告文件必须包含生成时间和任务名称
- 异常列表必须可勾选（`- [ ]`）
- 建议必须具体可执行
- 所有报告存储在 `docs/reports/` 目录
