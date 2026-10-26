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
- [x] **A2 修 29 只数据缺口 + 回填状态语义**（`d891848`）：`_get_json` 剥 BOM/识别 HTML + 有界重试；三源状态 ok/empty/error，失败记 `partial`（不再误记 done）；`--retry-partial`。**生产修复 30 只**：最新年 cash 缺失 10→0、有息负债 29→19（剩余上游确无）；603165 披露日补齐；600389 3.72→0.21、600350 16.53→2.50、600064/688036/600461/002404 均回到正确量级；可估值 650→659
- [x] **A3 修进度表与 batch_save**（`b6d593d`）：`backfill_progress` 新增 `covered_report_date`（新报告期自动重新入选，临时库实证）；`batch_save` 改 upsert 逐列 COALESCE（只带 roe 重存不再清空 cash/capex/notice_date，created_at 保留）。两者已同批上线
- [x] **A4 加深历史覆盖**（`dd3eabd` + 深层回填）：mainfin 加年报过滤（600519 返回 28 年）；`--task` 独立进度。**生产 `done=880 / 19250 行 / remaining=0`**；notice 6886→**19366（97.0%）**、fcf 6312→**17656（88.4%）**；2016 年 873 条年报全部有披露日
- [ ] **A5 价格域扩到全市场 + 回测有效域**（工具完成，卡在复权模型）：`--all` + 断点续跑 + `--source curl|tencent|akshare` + 限流退避/熔断已上线；回测已输出 `first_eligible_date/effective_years/cagr_pct`。**三源实测**：东财约 35 次后限流；腾讯前复权 38s/只（940 只约 10h）且高分红长历史为负价（600519 2016=-87.7）；Sina 不复权（送转假跌）。需先定"不复权 close + 后复权/因子"两列模型，再灌数并重抓 000001
- [x] **A6 估值分位去前视 + 上屏**（`9b22423`）：无披露日不参与；历史股本用 净利润/EPS 反推；详情页新增历史估值分位（生产 600398：5 年 PE 39% / PB 16% / 股息率 77%）
- [x] **A7 门禁离线化**（`619c680` + `d66a2c2`）：C2 live 用例全部打桩；AI 队列遗留任务兜底打桩。实证修前 600s 超时（真实 401 + 60s 退避），修后 144s 完成；1 次测试隔离 flaky 已记录
- [x] **A8 文档数字校正**：handoff / iteration-log / tasklist 已按实测更新（回测不再声称 +45%；价格域与 000001 事故如实记录）
- [x] **A9 UI 股息两列**（`e0db1f0` + `c8c3b35`）：候选卡/AI 观察池卡/手动钉选卡均显示股息率与连分年数；钉选卡缺失时显示 `--` 不再整卡缺项；最近一年未分红回退上一财年（300390 1.55%/8 年）。生产浏览器实测 7 张钉选卡两列可见；yield 覆盖 3789、years 4053；gate 674
- [x] **A10 UI 架构收口**（`0bb4436`…`e65a310`，gate 686）：5 个指标表面统一到 `src/web/stock_metrics.py` + `_metric_cells.html`；钉选/搜索改服务端格式化；静态资源 mtime 自动版本；详情页补齐 3 个指标；修复负 PE 标 good、首页运行源取到复盘轮。新增表面一致性测试防回潮；候选计数标注"最新筛选 20 + 历史分析 15"

## Phase 7 — 2026-10-26 用户指令（按顺序执行，一次一件）

> 用户 2026-10-26 指令：价格模型与限速、全市场财务抓取、数据意义、子项目合并、个股 UI 统一、全代码审计与文档更新。
> 规则：每项单独验证；长任务先入档再跑；不确定就暂停并记录。

- [ ] **B1 价格复权模型 + 限速抓取**（进行中）：定"不复权 raw + 后复权 adj + 展示 close（正数、锚定最新价）"三列模型；Sina 取 raw（快）、东财 fqt=2 取 adj（限速 3s/只 + 30 只批间停顿 + 失败冷却）；重抓 000001 与全部 959 只；估值分位改用 raw、回测改用 adj。
- [ ] **B2 全市场年报抓取**（待 B1 稳定）：~4500 只从未采过历史财务；复用 F10 三源 + 进度表 + 单实例锁 + 限速，预计 9–12h，夜间跑；完成后核对 5527 覆盖与 notice/fcf 比例。
- [ ] **B3 数据意义校验**（不着急但必须做）：估值输入完整性、负 FCF/金融股口径、历史股本、公告日、异常值（负价/零价/重复行）逐项设校验与报告；不达标不对外宣称。
- [ ] **B4 子项目合并**（择时）：检查 paper-upstream 最新 tag，按 `docs/subproject-protocol.md` subtree pull + 契约核对 + 门禁；域内 bug 提 Issue，不手改 `paper_trading/`。
- [ ] **B5 个股 UI 全面统一**（优先候选股总览风格）：主页 3 个视图（候选/AI 观察池/钉选）共用同一张卡模板与服务端渲染；统一头部、指标网格、按钮、徽标、空值/颜色；详情页两个模板后续同口径。
- [ ] **B6 全代码审计 + 文档更新**（谨慎、逐项）：路由/DAO/采集/调度/模板/JS 的重复与静默失败；更新 README、architecture、handoff、iteration-log；每项单独 commit，不确定处记录到本清单。

## 已完成（本文件历史）

- [x] 2026-10-23 DB 锁修复（`busy_timeout` + 非 WAL 才设置 `journal_mode`）
- [x] 2026-10-23 母项目 v2.0.0 发布
- [x] 2026-10-22 专业化三件套（outcomes / 股息 fail-closed / 新闻上下文）
- [x] 2026-10-22 流程自动化（deploy.sh / gate 基线下限 / CI）
