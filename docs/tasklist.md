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
- [ ] P0-6b AI 分工调整：LLM 从"给估值"改为"校验 + 解释偏离"
- [x] P0-7 同业/行业估值分位（`get_sector_averages` 均值+中位数 pe/pb/roe；`get_sector_percentile` 个股行业内分位；详情页/候选卡展示）→ 生产实测行业均值+中位数、个股分位（电子信息 PE20→10.3%）；gate 840

## Phase 3 — 验证闭环

- [ ] P0-8 look-ahead-safe 回测（修 `backtest_topk.py`）
- [ ] P0-9 买入 MOS 记录 + 安全边际预测力分层验证

## Phase 4 — 体验与协同（择要）

- [ ] E1 财务指标同环比高亮（详情页）
- [ ] E2 搜索结果按 PE/ROE/市值排序
- [ ] C1 对齐子项目 M2（量化可信度）路线图
- [ ] C2 观察 v0.6.5 固定交易时段 runtime tick 日志

## 已完成（本文件历史）

- [x] 2026-10-23 DB 锁修复（`busy_timeout` + 非 WAL 才设置 `journal_mode`）
- [x] 2026-10-23 母项目 v2.0.0 发布
- [x] 2026-10-22 专业化三件套（outcomes / 股息 fail-closed / 新闻上下文）
- [x] 2026-10-22 流程自动化（deploy.sh / gate 基线下限 / CI）
