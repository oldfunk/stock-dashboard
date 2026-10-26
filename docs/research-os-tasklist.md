# Research OS 任务清单

> 创建：2026-10-26 · 上游：`docs/research-os.md` · 状态：进行中
> 规则：先 R0 框架，后最小闭环；每个任务有明确验收，不跳步；完成即勾选并写验证命令/输出。
> 数据缺口（价格/年报）在后台补，不阻塞 R0 设计/骨架；但 R2 实验必须通过数据质量门。
> 2026-10-26 已拍板：SQLite（业务事实源）+ Parquet（研究快照）+ DuckDB（嵌入式分析）；单文件无服务；向量检索后期用 sqlite-vec。详见 `research-os.md` §19–20。

## R0 — 框架（先建地基，不跑策略）

- [x] **R0-1 领域对象与 ID**：`src/research/domain.py` 定义 MarketState/Hypothesis/Experiment/Result/Strategy/Deployment/Decision 的 dataclass、状态枚举、ID 规则（H/E/S/D/MS）。
  - 验收：单测覆盖状态机合法/非法迁移；ID 可读且唯一。
- [x] **R0-2 数据库表与迁移**：新增 `market`、`instrument`、`trading_calendar`、`fx_rate_daily`、`feature_definition`、`stock_feature_daily`、`universe_membership`、`data_quality_daily`、`research_hypothesis`、`research_experiment`、`research_result`、`strategy_version`、`decision_log`、`prompt_registry`、`llm_run`。
  - 验收：`init_database()` 幂等；旧库升级不破坏现有表；DAO 单测；`kline_daily` 增加 `market`/`instrument_id`（不改旧主键）；生产迁移完成：`instrument_id` 空值 0/2,150,145，`instrument` 5,527（SSE 2,633 / SZSE 2,894）。
- [ ] **R0-2a Repository 接口**：SQLite 与 DuckDB 各实现只读/只写接口，研究查询不直接写业务库。
- [ ] **R0-2b Parquet 快照与 manifest**：`data/snapshots/<snapshot_id>/` + manifest（表名/行数/最大日期/hash）；快照任务可重跑。
- [ ] **R0-2c DuckDB 视图**：DuckDB 只读 Parquet；提供研究查询函数；单测覆盖一个回测扫描。
- [x] **R0-3 注册表**：`src/research/registry.py` 统一特征/假设/策略/提示词注册与校验（必填字段、版本、hash）。
  - 验收：重复 ID/缺字段/非法状态被拒绝；注册即写 DB。
- [x] **R0-4 版本与快照**：`data_snapshot_id`（表名/行数/最大日期/hash）、`code_commit`、`config_hash`、`random_seed` 的记录与校验。
  - 验收：`src/research/snapshot.py` manifest 可校验漂移；快照缺失实验拒绝（R0-6 已接入）。
- [x] **R0-5 数据质量门**：`data_quality_daily` + `evaluate/assert_quality(dataset, date)`；覆盖率/PIT/异常/陈旧度；无记录 fail-closed。
  - 验收：不达标实验标记 `invalid`；单测覆盖 4 类失败（R0-6 已接入）。
- [x] **R0-6 实验接口**：`src/research/experiment.py` `ExperimentRunner` 抽象 + `TopKBacktestRunner` 适配器；计划/结果/结论落库。
  - 验收：固定小样本跑通并复现（两次指标一致）；快照缺失/质量 invalid 拒绝；接口不绑死单一回测器。
- [ ] **R0-7 决策账本**：`decision_log` DAO + CLI：改什么/为什么/证据 ID/预期/复核日。
  - 验收：策略/假设状态变更必须带决策记录；关闭时写结果。
- [ ] **R0-8 LLM 任务注册表**：`src/research/llm/tasks.py`（任务/模型档/工具白名单/输出 schema/token 预算）。
  - 验收：未注册任务不可调用；schema 缺失拒绝。
- [ ] **R0-9 Prompt 框架**：`research/prompts/<task>/vN.md` + `prompt_registry`（hash/版本）；统一 system/context/task/schema 结构。
  - 验收：prompt 变更生成新版本；旧版本可回放。
- [ ] **R0-10 工具网关**：只读工具（market_state/features/financials/valuation/outcomes/hypothesis/experiment）、草案写工具；白名单/超时/行数上限/审计。
  - 验收：越权工具被拒绝；每次调用写 `llm_run`；写操作需批准 token。
- [ ] **R0-11 输出校验**：JSON schema + fact ID 引用 + 数字一致性 + 缺证据拒绝。
  - 验收：编造数字/无引用/schema 错 → 拒绝；golden cases 通过。
- [ ] **R0-12 测试夹具**：fake LLM、固定时钟、最小数据集、实验 fixture；离线可跑。
  - 验收：CI/gate 不依赖外网/真实 Key；新增测试纳入基线。
- [ ] **R0-13 Universe/退市名单**：导入 AKShare SH/SZ 退市名单（161+211）、BaoStock 交叉验证；建 `universe_membership`；每日记录全市场状态/上市退市/ST。
  - 验收：退市股可查；历史成分缺失标记 `coverage=current_only`；不宣称无幸存者偏差。

## R1 — 市场状态（框架内第一个垂直切片）

- [ ] **R1-1 状态向量定义**：估值/基本面/广度/流动性/质量五块，字段/公式/来源/缺失策略注册。
- [ ] **R1-2 `market_state_daily` 快照**：`scripts/research/snapshot_market.py`，纯计算、可重跑。
  - 验收：连续 5 个交易日快照可复现；缺失字段显式 NULL/quality。
- [ ] **R1-3 Regime 规则**：趋势/估值/波动/风格，透明规则 + confidence/drivers。
  - 验收：规则单测覆盖边界；不引入黑箱模型。
- [ ] **R1-4 市场叙事任务**：`market_summary` prompt + 校验；只解释向量，不引入新数字。
  - 验收：golden case 引用覆盖率 100%；无证据时输出数据不足。
- [ ] **R1-5 数据质量门接入**：状态向量覆盖率不足时标记 `invalid`，不生成叙事。

## R2 — 假设与实验（最小闭环核心）

- [ ] **R2-1 假设卡 CLI**：注册/锁定/查看；锁定后不可改。
- [ ] **R2-2 实验计划 CLI**：绑定假设、数据快照、参数、指标、分域、费用。
- [ ] **R2-3 首个真实假设 H-001**：MOS 预测力（用现有 `analysis_outcome`）；预注册后跑。
- [ ] **R2-4 稳健性套件**：样本外/滚动、regime/行业/市值分域、bootstrap CI、参数敏感性。
- [ ] **R2-5 结论与冻结**：supported/rejected/inconclusive/invalid；写清缺什么证据。
- [ ] **R2-6 对照假设**：价值/质量/动量各一个，同一数据快照、同一费用；用于检验"价值是否只是先验"。

## R3 — 策略与归因

- [ ] **R3-1 `S-BASE-v0` 登记**：把当前 7 门规 + 五维评分写成 Strategy Spec。
- [ ] **R3-2 策略版本 CLI**：创建/晋升/退役；只能引用 supported 假设。
- [ ] **R3-3 纸盘部署记录**：`strategy_deployment` 对接 paper_trading；记录资金/起止/模式。
- [ ] **R3-4 归因报告**：收益/超额/费用/因子/行业/个股/regime；按周/月生成。
- [ ] **R3-5 漂移检测**：滚动超额/胜率/暴露/数据质量；触发写决策日志。

## R4 — LLM 研究助手

- [ ] **R4-1 模型路由**：fast/deep 档、用户 Key、预算、队列优先级；无 Key 跳过并标注。
- [ ] **R4-2 首批任务**：`market_summary`、`hypothesis_critic`、`experiment_review`、`decision_memo`。
- [ ] **R4-3 工具调用**：只读查询 + 草案写；重任务入队；全量审计。
- [ ] **R4-4 离线评估**：golden cases（schema/引用/数字/拒绝）；纳入 gate。
- [ ] **R4-5 在线评估**：人工评分/修改幅度/采纳率/成本/延迟；提示词版本对比。
- [ ] **R4-6 旧链路收敛**：`ai_analyzer` 的单体 prompt 逐步迁到任务注册表；保留兼容。
- [ ] **R4-7 Context Service**：每任务 token 预算 + 结构化事实选择（SQL 聚合/top-k）+ 引用 ID；数字表禁止文本压缩。
- [ ] **R4-8 压缩层（触发式）**：文本任务超阈值才接入 LLMLingua-2；检索压缩用 LangChain/LlamaIndex + bge-reranker；向量库 sqlite-vec/Chroma。必须过 golden cases。

## R5 — 研究界面/API

- [ ] **R5-1 `/api/research/*`**：状态/假设/实验/策略/决策只读接口 + 受控写接口。
- [ ] **R5-2 `/research` 页面**：五块：市场状态、假设板、实验记录、策略版本、决策日志。
- [ ] **R5-3 证据链跳转**：状态→假设→实验→策略→部署→归因→决策。
- [ ] **R5-4 空值/质量标识**：`--`/`数据不足`/`invalid` 统一语义。

## R6 — 最小闭环试点

- [ ] **R6-1 一条完整链**：MS-20261026 → H-001 → E-001 → S-BASE-v0 → D-001 → A-001 → DL-001。
- [ ] **R6-2 复现验收**：从 ID 重跑实验，指标一致；快照/commit/hash 可查。
- [ ] **R6-3 人工评审**：你审一遍证据链，决定是否进入下一轮。

## R7 — 治理与节奏

- [ ] **R7-1 周/月/季/年复核模板**（市场/假设/实验/策略/决策）。
- [ ] **R7-2 晋升/淘汰 checklist** 落库并在 UI 展示。
- [ ] **R7-3 假设预算与 holdout 规则** 写入 `config/research.yaml`。
- [ ] **R7-4 方法版本发布**：季度 `methodology-vN` + 变更日志。

## 当前不做（明确边界）

- 不做自动实盘、不做 LLM 自改策略、不做黑箱深度学习因子。
- 不在数据质量未达标时给"全市场结论"。
