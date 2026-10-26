# Research OS — 投资研究操作系统设计

> 创建：2026-10-26 · 上游：用户"市场总结 → 大数据分析 → 方法内化 → 可进化"需求 · 状态：设计稿（未实现）
> 关联：`docs/architecture.md`（系统架构）、`docs/audit-20261026.md`（数据/代码审计）、`docs/research-os-tasklist.md`（任务跟踪）

## 0. 一句话定位

把项目从"一套固定选股策略 + 看板"升级为：

> **市场状态可观测 → 投资理念写成可证伪假设 → 假设跑成可复现实验 → 胜出部分固化为版本化策略 → 纸盘部署与归因 → 反哺下一轮研究。**

价值投资是**初始先验**，不是教义；任何先验都要被证据更新或淘汰。

## 1. 目标与非目标

### 目标

1. 每天/每周用**结构化状态向量**总结市场，而不是靠叙事。
2. 把"我们相信什么"写成**假设卡**（范围/周期/指标/失效条件），可预注册、可复现。
3. 用**实验引擎**检验假设：扣费、PIT、样本外、分域、稳健性、多重检验。
4. 把胜出假设固化为**策略版本**（YAML + git），可纸盘部署、可归因。
5. 所有变更进入**决策账本**：改什么/为什么/证据/预期/复核日。
6. 系统可手动进化：人提出、人批准；系统负责记录、复现、对照、提醒。
7. LLM 只做**研究助手**（总结/批评/提取/写备忘录），不做计算与最终决策。

### 非目标

- 不做"AI 自进化自动改策略"。
- 不做实盘自动交易（纸盘是独立子项目，实盘不在本设计范围）。
- 不用 LLM 替代回测、估值、统计检验。
- 不追求"永远满仓"或"每天有信号"。

## 2. 设计原则

1. **PIT 优先**：任何特征/财报必须按 `notice_date` 可见；禁止用未来数据回填。
2. **预注册**：假设与实验计划先锁定，再跑；跑完不改口径。
3. **可复现**：实验必须能由"数据快照 + 代码 commit + 配置 hash + 随机种子"重跑。
4. **证据优先于叙事**：LLM 输出必须引用 fact ID；无证据写"数据不足"。
5. **版本化**：假设、实验、策略、提示词、特征定义都有版本。
6. **人类在环**：晋升/降级/改策略必须人工批准。
7. **先框架后 MVP**：先建对象、表、接口、质量门、LLM 契约，再跑最小闭环。
8. **价值是先验**：允许质量/红利/动量/反转/低波等假设与价值并列竞争。

## 3. 总体架构

```
L6 研究界面/API     /research：市场状态 | 假设板 | 实验 | 策略版本 | 决策日志
L5 归因与学习       归因报告 | 漂移检测 | 复核节奏 | 晋升/淘汰
L4 策略与部署       Strategy Spec (YAML) | 纸盘部署 | 资金/风控
L3 假设与实验       Hypothesis Card | Experiment Plan | Result/Verdict
L2 特征与市场状态   Feature Registry | market_state_daily | regime
L1 数据与 PIT       financial_history | kline_daily | universe | data_quality
L0 运行基座         SQLite/WAL | 任务队列 | 锁 | 日志 | 版本/审计
```

横切：身份与版本、审计账本、质量门、LLM 任务网关。

## 4. 领域对象与生命周期

| 对象 | ID 示例 | 关键字段 | 状态 |
|---|---|---|---|
| 市场状态快照 | `MS-20261026` | 日期、状态向量、regime、drivers、quality | 生成即冻结 |
| 特征定义 | `F-ROE-5Y-v1` | 公式、单位、方向、缺失策略、版本 | draft/active/retired |
| 假设 | `H-20261026-001` | 先验、陈述、范围、周期、基准、指标、阈值、失效条件 | draft/locked/testing/supported/rejected/inconclusive/retired |
| 实验 | `E-20261026-001` | 假设 ID、计划、数据快照、代码 commit、配置 hash | planned/running/done/failed/invalid |
| 实验结果 | 绑定实验 | 指标、CI、样本、分域、稳健性、结论 | 冻结 |
| 策略版本 | `S-VALUE-QUALITY-v1` | 假设集合、universe、排序、持仓、风控、执行 | draft/paper/live?/retired |
| 部署 | `D-20261101-01` | 策略版本、模式、资金、起止 | running/stopped |
| 归因 | `A-20261130-01` | 收益/超额/因子/行业/个股/regime | 冻结 |
| 决策 | `DL-20261130-01` | 类型、对象、变更、理由、证据、预期、复核日 | open/reviewed/closed |

生命周期规则：

- 假设 `locked` 后不可改；修改 = 新版本 + 新 ID。
- 实验必须绑定已 `locked` 的假设；一个假设可多次实验，但每次都是新实验 ID。
- 策略版本只能引用 `supported` 假设；晋升需人工批准。
- 决策日志关闭时必须写"结果 vs 预期"。

## 5. 数据与特征层

### 5.1 PIT 与数据质量

- 财报可见日：`notice_date`；缺失不参与（不回退 `report_date`）。
- 复权：三列价格（`close_raw`/`close_adj`/`close`），估值用 raw、回测用 adj。
- 退市/暂停/ST/次新：`universe_membership` 按日记录，回测禁止只用当前存活股票。
- 公司行动：拆股/送转/分红，回测用 adj，估值用 raw。
- 数据质量门：覆盖率、缺失率、异常值、PIT 违规、陈旧度；不达标 → 实验 `invalid`，不出结论。

### 5.2 特征注册表

每个特征必须有：

```
feature_id, version, name, formula, unit, direction,
missing_policy, lookback, source, owner, created_at, status
```

禁止在模板/脚本里重复实现同一特征；新增特征先注册，再写代码，再补契约测试。

### 5.3 存储策略

- 阶段 1（SQLite）：`stock_feature_daily` 宽表（code, trade_date, 核心 30–50 列）+ `market_state_daily`。
- 阶段 2（研究量上来后）：Parquet/DuckDB 做离线实验，SQLite 只服务面板；接口不变。
- 所有表带 `asof`/`data_snapshot_id`，实验只读快照。

### 5.4 当前缺口（不阻塞框架，阻塞结论）

- 年报覆盖 959 → 5527（进行中）；价格 959 只（进行中）。
- 退市股、历史指数成分、上市/退市日期。
- 市场级数据：指数估值、广度、两融、ETF、行业聚合。
- 财报重述/公告日 PIT 校验。

## 6. 市场状态（Market State）

### 6.1 状态向量（每日）

| 块 | 指标（示例） |
|---|---|
| 估值 | 主要指数 PE/PB 5/10 年分位；盈利收益率 − 10Y 国债；行业估值离散度 |
| 基本面 | 全市场/行业 ROE、净利增速、盈利修正广度、FCF 收益率、杠杆 |
| 广度/趋势 | 站上 MA200 比例、涨跌家数、新高/新低、成交额、涨跌停家数、20/60 日波动 |
| 流动性/资金 | 两融余额及变化、ETF 申赎、IPO/再融资、北向（若可得）、汇率/利率 |
| 质量 | 每个指标的覆盖、缺失、异常标记 |

### 6.2 Regime 规则（透明，非黑箱）

- 趋势：指数 > MA200 且广度 > 50% → up；反之 down；中间 → range。
- 估值：分位 <30% / 30–70% / >70% → cheap/mid/expensive。
- 波动：20 日已实现波动分位 <30% / 30–70% / >70% → low/mid/high。
- 风格：价值/成长相对强弱（指数或行业聚合）→ value/growth/neutral。
- 输出：`regime = trend × valuation × vol × style`，附 `confidence`（一致指标占比）与 `drivers`。

### 6.3 叙事

LLM 只把状态向量翻译成人话：结论、驱动、风险、数据缺口；**不得引入向量外的数字**。每周/每月生成一次复盘，带 `snapshot_id`。

## 7. 假设与实验

### 7.1 假设卡

```yaml
id: H-20261026-001
prior: value            # value/quality/dividend/momentum/reversal/low_vol/regime
statement: "非金融、FCF>0、ROE>15% 的股票中，按 MOS 排序前 20，持有 6 个月，扣费后跑赢沪深300"
universe: non_financial
horizon_days: 126
benchmark: sh000300
metric: excess_return
threshold: "> 0 after costs"
regimes: [all]
costs: {commission: 0.00025, stamp: 0.0005, slippage_bps: 10}
kill_criteria: ["连续 2 年超额<0", "最大回撤>25%", "数据质量不达标"]
status: draft
owner: human
review_date: 2027-01-31
```

### 7.2 实验协议

1. 预注册：锁定假设 + 计划（数据快照、universe、参数、指标、分域）。
2. 运行：事件驱动回测；T+1、涨跌停、停牌、费用、容量。
3. 验证：样本外/滚动、regime/行业/市值分域、bootstrap CI、参数敏感性、多重检验校正。
4. 结论：`supported / rejected / inconclusive / invalid`；`inconclusive` 必须写"缺什么证据"。
5. 冻结：结果不可改；新想法 = 新实验。

### 7.3 反 p-hacking

- 每个研究周期限制"新假设预算"（如每月 ≤3 个）。
- 保留最终 2 年 holdout，只在年度审计用。
- 记录测试家族，做 FDR/Deflated Sharpe。
- 参数敏感性：邻域不稳健 → 不晋升。

### 7.4 实验执行器

- 第一版复用 `scripts/backtest_topk.py`，抽象为 `ExperimentRunner` 接口。
- 后续支持多因子/组合/事件驱动；接口不变。
- 现有 `analysis_outcome`（MOS 前向收益）作为"已发生实验"导入。

## 8. 策略与组合

### 8.1 Strategy Spec

```yaml
id: S-VALUE-QUALITY-v1
parent: S-BASE-v0
hypotheses: [H-20261026-001, H-20261026-003]
universe: {exclude_st: true, min_list_days: 250, exclude_financial: true}
ranking: {signal: mos, secondary: roe_5y_avg, topk: 20}
portfolio: {weighting: equal, rebalance: monthly, max_position: 0.08}
risk: {max_sector: 0.30, stop_loss: -0.20, max_drawdown: 0.25}
execution: {t_plus: 1, commission: 0.00025, stamp: 0.0005, slippage_bps: 10}
provenance: {data_snapshot: DS-20261026, experiments: [E-...], approved_by: human}
```

### 8.2 晋升/降级

晋升门槛（全部满足）：

- 样本外扣费超额 > 0；
- 至少 2 个 regime/行业稳健；
- 样本量足够（如 ≥200 个独立持有期）；
- 不依赖单票/单行业；
- 纸盘 ≥3 个月与回测方向一致；
- 数据质量达标。

降级触发：

- 滚动 12 个月超额 < 0；
- 回撤超限；
- 假设前提失效（如政策/制度变化）；
- 数据质量失败；
- 容量不足。

### 8.3 当前策略

当前 7 门规 + 五维评分登记为 `S-BASE-v0`，作为基准；不直接视为"正确方法"。

## 9. 归因与进化

### 9.1 归因维度

- 收益：总收益/超额/费用/滑点；
- 因子：价值/质量/动量/规模/波动；
- 行业/个股：贡献与集中度；
- Regime：不同状态下的表现；
- 持有期：20/60/120 日；
- 决策：哪些假设贡献/拖累。

### 9.2 漂移检测

- 超额滚动均值/胜率下降；
- 因子暴露漂移；
- 数据质量/覆盖率变化；
- Regime 迁移；
- 触发 → 决策日志 + 复核。

### 9.3 节奏

- 每周：市场状态 + 纸盘归因；不改策略（除非 kill）。
- 每月：假设评审、实验、晋升/降级。
- 每季度：方法评审、regime 复盘、退役。
- 每年：holdout 审计、方法版本发布。

## 10. LLM 研究助手（角色/提示词/工具/路由）

### 10.1 角色边界

| 层 | 负责 | 不负责 |
|---|---|---|
| 确定性代码 | 数据、特征、筛选、估值、回测、归因 | 叙事、假设生成 |
| LLM | 总结、批评、提取、写备忘录、提出假设草案 | 计算、回测、最终决策、自动改策略 |
| 人 | 锁定假设、批准晋升、改策略 | 手工算指标 |

### 10.2 任务注册表

| 任务 | 模型档 | 工具（只读） | 输出 | 人工批准 |
|---|---|---|---|---|
| `market_summary` | fast/medium | `get_market_state`, `get_news` | JSON+Markdown | 否 |
| `stock_thesis` | medium | `get_features`, `get_financials`, `get_valuation`, `get_outcomes` | JSON | 否（写笔记） |
| `hypothesis_critic` | deep | `get_hypothesis`, `get_experiments` | JSON | 人决定 |
| `experiment_review` | deep | `get_experiment`, `get_result` | JSON | 人决定 |
| `attribution_review` | medium/deep | `get_attribution`, `get_market_state` | JSON | 人决定 |
| `decision_memo` | deep | 上述只读工具 | JSON | **必须** |
| `extract_facts` | fast | 无/文本 | JSON | 自动（校验后） |

### 10.3 Prompt 结构（统一）

```
[system]
角色 + 证据规则 + 引用格式 + 输出 schema + 拒绝规则
（无证据写 insufficient_evidence；禁止编造数字；区分事实/解释/不确定性）

[context]
FACT 块：fact_id | asof | source | quality | value
（只给结构化事实，不倾倒原始表；按 token 预算裁剪）

[task]
具体问题 + 输出要求

[output_schema]
JSON Schema（必填字段、枚举、最大长度）
```

规则：

- 温度 0–0.2；每任务 max_tokens 预算。
- 每个数字必须引用 `fact_id`；引用覆盖率 < 100% → 校验失败。
- 数据缺口必须显式列出。
- 不得给"保证收益/荐股"表述。

### 10.4 工具调用

- 只读工具：类型化参数、`asof` 必填、行数上限、超时。
- 写工具只允许"草案"：`draft_hypothesis`、`propose_strategy_change`、`write_note`；提交需人工批准 token。
- 重工具（回测/实验）只入队，返回 job_id，LLM 不阻塞等待。
- 每次调用记 `llm_run`：prompt_id/version、输入 hash、工具序列、tokens、latency、schema/citation 校验、人工反馈。
- 工具白名单按任务配置；禁止任意 URL/网络。

### 10.5 模型路由与成本

- `research.fast`：摘要/提取/格式化；`research.deep`：假设批评/实验评审/决策备忘录。
- 用户 `/llm` 配置；无 Key 时任务跳过并标注，不伪造。
- 队列优先级：研究任务低于/高于股票分析可配；Pi 并发 1–2。
- 预算：每任务 token 上限；每月成本上限；超限降级或排队。

### 10.6 提示词与评估

- 提示词放 `research/prompts/<task>/vN.md`，git 版本化；DB 存 hash。
- 离线评估：golden cases（20–30 个），校验 schema、引用、数字一致性、缺数据拒绝。
- 在线评估：人工评分、修改幅度、决策采纳率、成本/延迟。
- 提示词改动必须跑离线评估；不达标不上线。
- 禁止"提示词里写死结论"；只写规则与格式。

### 10.7 记忆

- 不用聊天历史当记忆；检索研究账本（假设/实验/决策/笔记）。
- 每次任务注入相关对象的摘要 + ID，便于引用与追溯。
- LLM 输出写回时保留 `source_run_id`，可回溯。

## 11. 技术架构与目录

```
src/research/
  domain.py          # dataclass/enum/ID
  registry.py        # feature/hypothesis/strategy/prompt registry
  market_state.py    # 状态向量/regime
  features.py        # 特征计算（PIT）
  hypothesis.py      # 假设卡/状态机
  experiment.py      # 实验计划/执行/结果
  strategy.py        # 策略 spec/晋升
  attribution.py     # 归因/漂移
  governance.py      # 决策日志/复核
  llm/
    tasks.py         # 任务注册表
    prompts/         # 版本化模板
    schemas.py       # JSON Schema
    gateway.py       # 工具白名单/执行/审计
    validator.py     # schema/引用/数字一致性
    router.py        # 模型档/预算
  api.py             # /api/research/*
scripts/research/
  snapshot_market.py
  register_hypothesis.py
  run_experiment.py
  promote_strategy.py
  weekly_review.py
config/research.yaml
research/
  hypotheses/H-*.yaml
  strategies/S-*.yaml
  prompts/<task>/vN.md
docs/research-os.md
docs/research-os-tasklist.md
```

模块边界：`research/` 只读 `models/collector` 的数据，不反向写采集；策略部署通过 paper_trading 接口，不直接下单。

## 12. 数据表（框架层）

| 表 | 作用 | 阶段 |
|---|---|---|
| `market_state_daily` | 每日状态向量/regime/drivers | R1 |
| `feature_definition` | 特征注册表 | R0 |
| `stock_feature_daily` | 股票-日核心特征宽表 | R0/R1 |
| `universe_membership` | 每日 universe（含退市） | R0 |
| `data_quality_daily` | 覆盖率/PIT/异常 | R0 |
| `research_hypothesis` | 假设卡/状态机 | R0 |
| `research_experiment` | 实验计划/快照/代码版本 | R0 |
| `research_result` | 指标/CI/分域/结论 | R0 |
| `strategy_version` | 策略 spec/版本 | R0 |
| `strategy_deployment` | 纸盘部署 | R3 |
| `strategy_attribution` | 归因 | R3 |
| `decision_log` | 决策/复核 | R0 |
| `prompt_registry` | 提示词版本/schema | R0 |
| `llm_run` | LLM 调用审计 | R4 |

## 13. 版本与可复现

- 代码/配置/提示词/假设/策略：git 版本。
- 运行时对象：DB + content hash。
- 实验记录必须含：`data_snapshot_id`、`code_commit`、`config_hash`、`random_seed`、`universe_id`、`feature_versions`。
- 数据快照：表名 + 行数 + 最大日期 + 文件 hash（或 DB backup）。
- 复现测试：同一实验 ID 重跑，指标在容差内一致；否则 `invalid`。
- 策略版本发布：spec hash + 依赖假设/实验 ID 列表。

## 14. 治理与节奏

- 假设预算：每月新假设 ≤3；活跃假设 ≤10。
- 预注册锁：假设/计划锁定后才可跑。
- 晋升委员会：人（你）批准；系统给 checklist 与证据包。
- 决策日志：任何策略/假设状态变更必须写；关闭时写结果 vs 预期。
- 复核：周/月/季/年（见 §9.3）。
- 退役：过期假设/策略标记 retired，保留历史。

## 15. 最小闭环（在成熟框架内）

不做"先跑通再补结构"。顺序：

1. **R0 框架**：表、注册表、ID/版本、质量门、实验接口、LLM 任务/工具/校验骨架、测试夹具。
2. **R1 市场状态**：`market_state_daily` + 每日快照（纯计算）。
3. **R2 假设/实验**：注册 H-001（MOS 预测力），用现有 outcomes 跑一次；结果入账。
4. **R3 策略**：登记 `S-BASE-v0`，接纸盘部署 + 归因。
5. **R4 LLM**：先做 `market_summary` 与 `hypothesis_critic` 两个任务，离线评估通过后再接 UI。
6. **R5 界面**：`/research` 只读页：状态/假设/实验/策略/决策。
7. **R6 试点**：价值/质量/动量三个对照假设，跑一个季度，形成第一份方法评审。

验收：能从 `/research` 点开一条链：`市场状态 → 假设 → 实验 → 结果 → 策略版本 → 部署 → 归因 → 决策`，且实验可复现。

## 16. 风险与防退化

| 风险 | 防线 |
|---|---|
| 前视/幸存者偏差 | PIT + 退市 universe + 公告日 |
| 过拟合/p-hacking | 预注册 + holdout + 多重检验 + 参数敏感性 |
| 数据缺口伪装成结论 | 质量门 + `invalid` + 显式数据不足 |
| LLM 编造 | fact ID 引用 + schema/数字校验 + 缺证据拒绝 |
| 提示词漂移 | 版本化 + golden cases + 变更评审 |
| 工具滥用 | 白名单 + 只读默认 + 写操作人工批准 |
| 策略衰减 | 漂移检测 + kill criteria + 定期退役 |
| 容量/成本 | 回测含费用/滑点/容量；纸盘验证 |
| 框架腐化 | 单一事实源 + 契约测试 + 审计清单 |

## 17. 与现有系统的映射

| 现有 | 新定位 |
|---|---|
| `financial_history` / `kline_daily` | L1 数据层（三列价格/PIT） |
| `screening_result` / 7 门规 | `S-BASE-v0` 策略版本 |
| `backtest_topk.py` | 第一版 `ExperimentRunner` |
| `analysis_outcome` / `mos_at_entry` | 已发生实验，导入 R2 |
| `paper_trading/` | L4 部署与执行 |
| `ai_analyzer.py` / `ai_queue.py` | L6 LLM 助手（需按 §10 重构任务/工具/校验） |
| `ai_journal` | 叙事层；新增结构化 `decision_log` |
| `docs/audit-20261026.md` | 数据/代码质量门输入 |

## 18. 设计验收（框架完成的标准）

- [ ] 能注册特征/假设/实验/策略/提示词，字段校验失败即拒绝。
- [ ] 任一实验可由 ID 复现（数据快照 + commit + config + seed）。
- [ ] 任一策略版本可追溯到他引用的假设与实验。
- [ ] 数据质量不达标时实验自动 `invalid`，不出结论。
- [ ] LLM 任务离线可跑（mock 模型），schema/引用校验失败即拒绝；线上调用全量审计。
- [ ] `/research` 能展示完整证据链。
- [ ] 所有变更进入 `decision_log`。
