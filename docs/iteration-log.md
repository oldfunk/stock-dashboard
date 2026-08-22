# Stock Dashboard — 迭代进程账 (Iteration Ledger)

> 本文件是 nightly 工程迭代 agent 的全局上下文源。每次迭代前先通读它了解历史与现状，迭代后追加条目。
> 维护者：Hermes nightly 迭代 agent + 用户。零 emoji（允许 Unicode 排版 → ↑ ↓ ✓）。

## 项目定位
A股价值投资看板。生产实例跑在 pi1（192.168.50.210）的 systemd `stock-dashboard.service`，端口 9527，每日 15:30 选股 + 实时行情。代码真相源 = GitHub `origin/main`，pi1 从 origin 拉取部署。pi2 仅作开发/迭代副本，**不运行服务**（省 Hermes 进程资源）。

## 当前真实状态（2026-08-21）
- 每日筛选 5527 → 20 候选：正常，周一至周五 15:30 由 `scheduler.py` 触发。
- AI 分析：已提频至**每日**（原仅周五），随每日流水线触发，走 `FreeModelPool`（-free 模型自动发现+轮换）。
- 实时行情/大盘：每 5 分钟更新，正常。
- 周六复盘系统：`ai_watchlist` / `ai_journal` / `ai_watchlist_history` 在用，链路正常。
- `deep_research` 表：已建但**当前未使用**（原 Hermes 投研 cron 已废弃，勿依赖）。
- 已知隐患 `with_roe` UnboundLocalError：已修（提前初始化为 0）。
- pi2 开发 venv：`markdown` 依赖错装为 `markdown-it-py`，本地 `import src.web.routes` 失败——agent 改完代码后用 **ssh pi1** 做冒烟测试，不要依赖 pi2 venv。
- 面板核心短板：**分析深度浅 + 评分不透明**。功能迭代优先补这两块，而非堆 UI。

## 工程约定
- 零 emoji（允许 Unicode 排版 → ↑ ↓ ✓）。
- 每次迭代只做一件小而实的事，不求大改大动；禁止一次性改多个无关模块。
- 改动必须冒烟测试：`ssh pi@192.168.50.210 "cd /home/pi/stock-dashboard && .venv/bin/python -c 'import 改动的模块'"` 确认无 import 错误；可跑 `python scripts/scan_emoji.py`（仓库根）确认零 emoji。
- **绝不重启生产服务** `stock-dashboard.service`；部署由用户手动 pull+restart。
- 提交信息中文，写清「改了什么 + 为什么（想法）」。
- 改完：更新本账（勾掉 backlog 项、Changelog 追加）+ 推 Discord 简报。

## 待办 backlog（细粒度，按优先级）
- [ ] 评分体系透明化（skill P1③，已拆子项，本次做最小一子项）
  - [x] 抽 `_score_breakdown()` 纯函数：把五维加权 + 一致性加分拆成可逐项解释的结构（透明化前置，不改阈值）
  - [ ] 详情页评分卡片点击展开五大维度子分 + 加权公式（依赖上一步 `_score_breakdown` 输出）
  - [x] `screening_result` 落库 `score_detail`（JSON）—— 后端半：schema + 迁移 + `score_candidates` 序列化写入（2026-08-22 完成）
  - [ ] `screening_result.score_detail` 在 routes/模板消费展示（前端半，待下次）
- [ ] `stock_analysis_history` 补 `model` 列并落库（当前 schema 缺，skill 说应有模型归属）
- [ ] AI 分析失败时前端/Discord 可见（当前 `scheduler` 仅 `logger.warning` 静默吞掉）
- [ ] AI 笔记增强：历史笔记对比 + 矛盾信号检测（skill P1②）
- [ ] 钉选股独立分析视图（skill P2⑥）
- [ ] 多策略并行（成长/红利/困境反转）配置化（skill P2⑤，较大，放后面）
- [ ] pi2 venv 修正 `markdown` 依赖（可选，便于本地测）
- [ ] 面板展示或清理 `deep_research` 废表（待定，投研 cron 已废弃）

## 变更记录（Changelog）
### 2026-08-22（nightly #2，最小纯工程项 — 落库评分拆解）
- 评分透明化（P1③）第二步：把上一步的 `_score_breakdown` 真正落库。
  - `src/models/database.py`：`screening_result` 表新增 `score_detail TEXT` 列（`CREATE TABLE` 声明 + `init_database` 迁移 `_add_column_if_not_exists` 兜底）；`ScreeningResultDAO.save_batch` 的 INSERT 增加 `score_detail` 字段。
  - `src/screener/value_screener.py`：`score_candidates` 在算分后调用 `_score_breakdown(c)` 并 `json.dumps(ensure_ascii=False)` 写进 `score_detail`；`score == breakdown.total` 数值完全不变（不变量已用单测守护）。
  - `tests/screener/test_value_screener.py`：新增 `test_score_breakdown_matches_total` 守护「总分 == `_calculate_moat_score` + 子分加和 == 总分 + 五维结构完整」这三条不变量。
- 想法/为什么：第一步只把逻辑结构化，但还没落地到数据层，详情页/Routes 仍拿不到子分。这一步**只做后端半**（落库），前端展示（routes/模板消费）拆成独立子项待下次——避免一次改多模块。数据一旦落库，下次迭代只需在 routes 读 `score_detail` 即可，无需再动评分引擎。零风险：不改变任何评分阈值，旧行 `score_detail` 为 NULL 不影响现有查询（SELECT * 兼容）。
- 冒烟：pi2 `.venv` pytest 41 passed；改动文件 `python3 -m py_compile` 通过；scp 到 pi1 远端 `.venv/bin/python -m py_compile` 通过；emoji 扫描（literal + `&#1(29|28|27)\d{3};` entity）零命中。仅改 pi2，未触碰 pi1 运行文件。

### 2026-08-22（nightly #1，最小纯工程项）
- 评分体系透明化（P1③）拆为子项，本次做最小一子项：在 `src/screener/value_screener.py` 新增纯函数 `_score_breakdown(stock)`，把原本 `_calculate_moat_score` 里内联的五维加权（ROE/PE/增长/负债/毛利）与一致性加分，拆成可逐项解释的结构（每项含 raw/sub/weight/contribution + `consistency_bonus` + `total`）。`_calculate_moat_score` 改为委托 `_score_breakdown` 返回 `total`，评分数值完全不变（已用样本股断言 `score == breakdown.total`，子分加和 == 总分，权重和 == 1.0）。
- 想法/为什么：面板核心短板是「评分不透明」，用户看得到总分却不知怎么来的。这一步**零风险**（不改任何阈值、不影响生产筛选结果），纯粹把已有逻辑结构化，为后续「详情页展开五大维度子分+加权公式」与「`screening_result` 落库 `score_detail`」铺路。属安全前置，没动生产逻辑。
- 冒烟：`python3 -m py_compile` 通过；pytest 式样本断言全过；无 emoji（仅含允许的 → 排版箭头）。改动只在 pi2，未触碰 pi1。

### 2026-08-21（人工排雷，非 nightly）
- 修 `with_roe` UnboundLocalError（`akshare_fetcher.py` 提前初始化为 0），避免 AKShare 批量接口偶发失败时整条 pipeline 崩溃、当天 0 入选。
- AI 分析提频至每日（`scheduler.py` 去掉 `weekday()==4` 限制），研报不再滞后 1-4 天。
- 已推 `origin/main` `aaa7561` 并重启 pi1 生产服务生效。
- 建立本迭代进程账 `docs/iteration-log.md`，作为 nightly 迭代 agent 的全局上下文源。
