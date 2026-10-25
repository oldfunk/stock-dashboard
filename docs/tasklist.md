# 长线开发 TODO List

> 创建：2026-10-23 · 上游：`docs/value-investing-methodology.md`（方案）+ `docs/roadmap.md`（路线）· 状态：进行中
> 规则：每次只做一件小而实的事；完成即勾选并记录验证（命令 + 输出）；全仓 pytest 只升不降。

## Phase 0 — 子项目 v0.6.7 合并（前置）✅ 2026-10-23

- [x] P0-0 重新 `subtree pull v0.6.7`（tag 重指 `a0dd2ec` 修复版；因先前 revert 冲突，改为 `read-tree --prefix=paper_trading/` 整树重读，版本 0.6.7）
- [x] P0-0b 契约核对：`integration/` 零写母库、只读表在契约内、CLI 参数兼容、`engine.enabled=false`、无新表（新增 `agent.news_timeout_sec`）
- [x] P0-0c 部署 + 双服务健康检查：gate **810 passed**（785 +25 vendor）；`NEED_PARENT=1` 正确重启 `paper-trading-parent.service`；`:9527`/`:8081` 均 200

## Phase 1 — 数据底座（P0）

- [x] P0-1 股息率/分红率回填（`scripts/backfill_dividend_yield.py` + `backfill_snapshot_dividends` + 挂进每日流水线）→ **生产实测 783 → 3384（61%）**，分红率 3520，yield≥4 有 187 只；gate 814
- [x] P0-2 分红历史（`refresh_dividend_history` + `compute_dividend_years` + `backfill_dividend_years` + 详情页"连分年数"）→ **生产实测 10 年 31513 条，3620 只有连续分红年数**（10 年 1003 只 / ≥5 年 2217 只）；gate 819
- [x] P0-3 资产负债表关键字段（`src/collector/balance_sheet_fetcher.py` 直连东财 F10 + `financial_history` 新列 cash/interest_bearing_debt/goodwill/total_assets/total_equity + 采集合并）→ **生产实测茅台现金 516.9 亿 / 平安总资产 59257.8 亿等正确**；gate 824
- [x] P0-4 现金流关键字段（`src/collector/cashflow_fetcher.py` 直连东财 F10 现金流量表 + `capex`/`net_operate_cashflow` 列 + 采集合并）→ 生产实测茅台 capex 31.3 亿 / 经营现金流 615.2 亿；gate 828
- [x] P0-5 历史价格回填（`scripts/backfill_price_history.py`，10 年前复权，候选+观察池+钉选）+ 历史 PE/PB/股息率分位（`src/analyzer/valuation_percentile.py`，`notice_date` 防前视）→ **生产实测 24 只 59073 条（2016–2026）；海澜之家 5 年 PE 分位 11.8% / 10 年 6.0%**；gate 831
- [x] P0-10 `financial_history` 披露日期（东财主财务兜底返回 `notice_date`）→ 生产实测茅台 2026-04-17 / 平安银行 2026-03-21

## Phase 2 — 估值模型（核心）

- [x] P0-6 可复现估值模型（`src/analyzer/valuation.py` FCFF 两阶段折现 + 程序化安全边际）+ P0-6b 接入 AI prompt（LLM 改为校验/解释偏离）→ **生产实测茅台 保守 650/基准 968/乐观 1524，安全边际 -29.6%**；gate 837
- [x] P0-6b AI 分工调整：LLM 从"给估值"改为"校验 + 解释偏离"（prompt 规则 4 已改写 + `_model_valuation_line` 注入；2026-10-24 复核：生产 `_model_valuation_line('600519')` 返回完整估值锚）
- [x] P0-7 同业/行业估值分位（`get_sector_averages` 均值+中位数 pe/pb/roe；`get_sector_percentile` 个股行业内分位；详情页/候选卡展示）→ 生产实测行业均值+中位数、个股分位（电子信息 PE20→10.3%）；gate 840

## Phase 3 — 验证闭环

- [x] P0-8 look-ahead-safe 回测（重写 `scripts/backtest_topk.py`：`financial_history.notice_date` 防前视 + 费用口径 + 买入不超现金）→ 初版 2024–2026：-36.79%；**2026-10-24 改 fail-closed（无披露日不入选，不回退 report_date）后：-16.66%**（域仅 37 只有披露日 / 35 只有价格——旧数字含约 3 个月前视泄漏，且域更小）；gate 630
- [x] P0-9 买入 MOS 记录 + 预测力验证（`analysis_outcome.mos_at_entry` + `summarize_by_mos`）→ **深折让档（MOS ≥40%）60 日胜率 80%、超额 +8.9pp**，显著高于中档（20–40%，12.5%）；**2026-10-24 修分层静默丢弃**（旧区间 ±100 丢 24% 样本）后覆盖 750/750，结论不变；gate 630

## Phase 4 — 体验与协同（择要）

- [ ] E1 财务指标同环比高亮（详情页）
- [ ] E2 搜索结果按 PE/ROE/市值排序
- [ ] C1 对齐子项目 M2（量化可信度）路线图
- [ ] C2 观察 v0.6.5 固定交易时段 runtime tick 日志

## Phase 5 — 可信度收口（2026-10-24 排查后）✅

- [x] 修 `summarize_by_mos` 分层静默丢弃：默认覆盖全区间（≤0/0~20/20~40/≥40），区间外显式列"未分层" → 生产实测覆盖 **750/750**（旧 ±100 区间丢 180 行/24%）；gate 630
- [x] 门禁超时保护：GNU `timeout` 包裹 pytest（母 900s / vendor 600s）+ 通过数无法解析即 fail-closed → 强制 2s 超时实测 `exit 124`
- [x] 详情页展示系统模型估值（可复现 FCFF 三档 + 当前价 + 安全边际 + 假设 + 数据来源）；无数据 fail-closed 显示"数据不足" → 生产 600519 = 650.39 / 967.80 / 1523.60、安全边际 -29.6%；605099 fail-closed
- [x] 回测 fail-closed（无 `notice_date` 不入选）+ 可用域透明化（`fundamental_codes`/`eligible_max`）
- [x] **P5-1 补充字段全量回填（2026-10-25 完成）**：`scripts/backfill_financial_extra.py`（东财 F10 资产负债表/现金流 + 主要财务指标直连；限速/限量/软预算/进度表断点续跑/单实例锁）。**生产实测 `done=959 / no_data=0 / updated_rows=19771 / remaining=0`**；覆盖 cash 18471、capex 18228、notice_date 6886、fcf 6312；可估值股票 36→**650（新 fail-closed 口径，67.8%）**。**但审计发现 29 只标 done 仍有字段缺口、进度表锁死未来年报（见 Phase 6 A2/A3）**
- [ ] **P5-2 年报历史扩到全市场**：`financial_history` 目前仅 959 只有年报（其余 ~4500 只从未采集历史财务）——需要另一条更重的慢速回填（akshare 同花顺摘要 + F10），待 A2–A4 稳定后再评估

## Phase 6 — 审计整改（2026-10-25 全量审计后）

> 审计结论：功能层齐、数据层"表面齐"，但估值链路上有 4 项正在输出错误数字、3 项会在下个年报季静默失效。逐项修，一次一步。

- [x] **A1 估值 fail-closed**（`367a75a`，gate 644→650）：`net_debt` 任一侧缺失返回 None（不再当 0）；`equity_per_share` 缺净负债返回 None；`sufficient` 以 `base_case` 非空为准；返回 `insufficient_reason/insufficient_text/inputs`；详情页显示具体原因（FCF 为负 / 缺净负债 / 缺股本），AI prompt 明确"不可用+原因+禁止编造 DCF"。**生产实测**：001332 由整表 `--` 改为"自由现金流为负"；600389 不再显示错误基准 3.72；600519 仍为 650.39/967.80/1523.60；959 只中可估值 650、负 FCF 201、缺 FCF 84、缺净负债 23、缺股本 1
- [ ] **A2 修 29 只数据缺口 + 回填状态语义**：按上游补 29 只（现金/有息负债）与 `603165` 披露日；回填在任一源失败或关键字段仍 NULL 时记 `partial`，加 `--retry-partial`（现在全标 `done`，`--retry-no-data` 不会重访）。影响 6 只估值偏差 ≥5%（600389 −94.5%、600350 −84.8%、600064 −22.9%、688036 +30.4%、600461 −15.1%、002404 +9.4%）
- [ ] **A3 修进度表与 batch_save**：`backfill_progress` 改按 `(task, code, report_date)`（临时库实证：标记 done 后插入 2026 年报仍 `remaining=[]`）；`batch_save` 改 upsert 保留扩展字段（临时库实证：只带 `roe` 重存会清空 `cash/capex/notice_date`）。两者必须同批上线
- [ ] **A4 加深历史覆盖**：mainfin 加 `REPORT_TYPE="年报"` 过滤（`ps=30` 混季报只有 7 年；加过滤 600519 返回 28 年 1998–2025），深回填 2016–2018（现在 notice 仅 43/2714）
- [ ] **A5 价格域扩到全市场 + 回测有效域**：`kline_daily` 现仅 49 只（2026-10-23 有价 24 只），2016–2019 可选域 = 0，2016/2020 起跑均 −6.5%；`backfill_price_history.target_codes()` 需去掉"只取 25 候选+观察池+钉选"；回测输出 `first_eligible_date`/`effective_years`/CAGR
- [ ] **A6 估值分位去前视**：`valuation_percentile._report_at` 去掉 `notice_date → report_date` 回退；历史 BVPS 用当年股本；接入 UI 或从 P0-5 交付声明中撤回
- [ ] **A7 门禁离线化**：`tests/collector/test_akshare_fetcher.py` 3 个 live 网络用例打桩，另设 opt-in 集成检查
- [ ] **A8 文档数字校正**：P0-5 "已交付"、回测 "+45%"、"全市场估值"等表述逐条对齐实测

## 已完成（本文件历史）

- [x] 2026-10-23 DB 锁修复（`busy_timeout` + 非 WAL 才设置 `journal_mode`）
- [x] 2026-10-23 母项目 v2.0.0 发布
- [x] 2026-10-22 专业化三件套（outcomes / 股息 fail-closed / 新闻上下文）
- [x] 2026-10-22 流程自动化（deploy.sh / gate 基线下限 / CI）
