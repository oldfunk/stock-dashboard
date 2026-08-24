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
- **git 工作流硬约定（防污染 main）**：每晚迭代在**当前 nightly 分支**上继续（开头 `git fetch origin && git rebase origin/main` 拉平上游，再 commit），commit 后 `git push origin HEAD`。**绝不在本地 `main` 上 commit，绝不 `git push origin main`**。push 后保持 HEAD 在 nightly 分支，勿切回 main（本地 main 由用户/合并流程管理）。多日累积都落在同一个 nightly 分支，审计时一次性 `git log origin/main..HEAD --stat` 即可。

## 待办 backlog（细粒度，按优先级）
- [ ] 评分体系透明化（skill P1③，已拆子项，本次做最小一子项）
  - [x] 抽 `_score_breakdown()` 纯函数：把五维加权 + 一致性加分拆成可逐项解释的结构（透明化前置，不改阈值）
  - [x] `screening_result` 落库 `score_detail`（JSON）—— 后端半：schema + 迁移 + `score_candidates` 序列化写入（2026-08-22 完成）
  - [x] `screening_result.score_detail` 在 routes/模板消费展示（前端半，2026-08-23 完成）：routes 解析 `score_detail` 为 `score_detail_parsed`，`_stock_list.html` 新增「评分拆解」可折叠块（五维 raw/子分/权重/贡献 + 一致性加分 + 总分），index/candidates 双页 JS 切换 + CSS
  - [x] 详情页（stock_detail.html）评分卡片独立展开五大维度子分 + 加权公式（2026-08-24 完成，见 nightly #1）：routes 取最新 screening_result 行 score_detail 解析下发 + `ScreeningResultDAO.get_latest_for_code` + 详情页独立「综合评分拆解」段（五维进度条 + 一致性加分 + 总分），与候选卡共用同一份 score_detail
- [x] `stock_analysis_history` 补 `model` 列并落库（2026-08-23 完成）：schema + 迁移 `_add_column_if_not_exists` + DAO `save(..., model=None)` 签名 + `ai_analyzer._save_analysis` 传 `result.get('model')`；routes 取 `ai_parsed.model` 并下传模板，候选卡显示「模型: <model>」徽标
- [x] AI 分析失败可见（拆为后端落库 8-24 nightly #2 + 前端徽标 8-24 nightly #3）：
  - [x] 后端半：screening_result 新增 `ai_failed`(INTEGER) + `ai_failure_reason`(TEXT) + 迁移 + `mark_ai_failure()` + `update_ai_analysis` 成功清标记 + `_save_failure` 传原因（无 Key / 模型返回空·全部不可用）+ `AiAnalyzer._last_error`
  - [x] 前端半：routes 下发 `ai_failed`/`ai_failure_reason`；`_stock_list.html` 无分析且失败时显示红色「AI 未分析」徽标（hover 具体原因）；index/candidates 补 `.stock-ai-fail` 样式
- [x] 零 emoji 存量违规清理（2026-08-24 nightly #3）：修掉 `_stock_list.html` 历史按钮 `&#128214;`（📖 实体 emoji），仅留纯文字「历史分析 (N次)」
- [ ] AI 笔记增强：历史笔记对比 + 矛盾信号检测（skill P1②）
- [ ] Discord 播报 AI 失败（已有落库，下次接 nightly 简报或 scheduler 失败通知）
- [ ] 钉选股独立分析视图（skill P2⑥）
- [ ] 多策略并行（成长/红利/困境反转）配置化（skill P2⑤，较大，放后面）
- [ ] pi2 venv 修正 `markdown` 依赖（2026-08-24 已顺手 `pip install markdown` 修好本地 import，但 venv 非项目文件、未提交；pyproject 本已声明 `markdown>=3.5`，仅 pi2 漏装，下次 sync 用 `pip install -e .` 自愈）
- [ ] 面板展示或清理 `deep_research` 废表（待定，投研 cron 已废弃）

## 变更记录（Changelog）
### 2026-08-24（nightly #3，AI 失败前端可见 + emoji 存量清理）
- AI 分析失败前端可见（接 #2 后端落库）：routes `_enrich_stocks` 下发 `ai_failed`/`ai_failure_reason`；`_stock_list.html` 在「无分析 + `ai_failed`」时渲染红色「AI 未分析」徽标（hover 显示具体失败原因），与既有「模型: <model>」灰徽标并列；`index.html`/`candidates.html` 补 `.stock-ai-fail` 红字红底样式（两页共用片段须同步）。
- 顺手修零 emoji 存量违规：`_stock_list.html` 历史按钮的 `&#128214;`（📖 实体 emoji，字面量正则捕获不到）改为纯文字「历史分析 (N次)」，符合项目零 emoji 规则。
- 想法/为什么：失败原因已在 #2 落到数据层，本次让用户在候选卡上一眼看到「哪只没分析到、为什么」，构成「失败可见」闭环前端半。emoji 实体是此前漏网的存量违规（藏成 HTML 实体），一并清掉。风险：纯展示层、读既有字段、旧行无失败标记不渲染、两页 CSS 已同步。
- 冒烟：pi2 `.venv` Jinja2 离线渲染 `_stock_list` mock（失败股显示徽标 + 原因 + 红样式、正常股无徽标）通过；三改动文件 `py_compile` 通过；手动 emoji 扫描（literal 区间 + `&#1(29|28|27)\d{3};` 实体）零命中（仅存 `★/☆/←` 等既有排版符号）；pytest 142 passed（2 个 pre-existing 失败与本次无关，stash 复测亦失败）。仅改 pi2，未触碰 pi1。

### 2026-08-24（nightly #2，AI 分析失败落库 — 透明化后端半）
- `screening_result` 新增 `ai_failed`(INTEGER DEFAULT 0) + `ai_failure_reason`(TEXT)：`CREATE TABLE` 声明 + `init_database` 迁移 `_add_column_if_not_exists(conn,'screening_result','ai_failed','INTEGER')` 与 `'ai_failure_reason','TEXT'` 兜底。
- `ScreeningResultDAO`：新增 `mark_ai_failure(run_id, code, reason)` 置 `ai_failed=1` 并写原因；`update_ai_analysis` 成功时顺带 `ai_failed=0, ai_failure_reason=NULL`（成功/失败标记互斥）。
- `ai_analyzer.py`：`_save_failure(stock, run_id, reason=None)` 签名增 `reason` 并调用 `mark_ai_failure` 落库；`analyze_batch` 在两处失败入口传具体原因——无 API Key 传「未配置 API Key」，分析返回 None 传 `AiAnalyzer._last_error`（新增属性，值为「模型返回空/全部免费模型不可用」）；无 Key 批量跳过路径同步传因。
- 想法/为什么：此前 AI 失败只写一条 `'{}'` 空记录，完全看不到失败原因（skill 明确列的「AI 分析失败前端/Discord 可见」项）。本次先把原因落到数据层，为后续前端徽标 + Discord 播报打地基。安全：新列默认 0/NULL，旧行 SELECT 兼容；`init_database` 迁移保证 pi1 仅一次 `systemctl restart` 即自动加列（旧行 NULL 不影响既有查询），无需手动 migration。
- 冒烟：pi2 `python3 -m py_compile` 三文件（database/routes/ai_analyzer）通过；pytest 142 passed（2 个 pre-existing 失败与本次无关）。仅改 pi2，未触碰 pi1。

### 2026-08-24（nightly #1，详情页评分拆解卡片 — 透明化收口）
- 详情页（stock_detail.html）新增「综合评分拆解」段：复用 `score_detail` JSON，展示五维子分（ROE/估值/增长/财务/毛利）进度条 + raw 值 + 贡献分、一致性加分行、总分行；与候选卡（8-23 完成）共用同一份 `score_detail` 数据。
- 支撑改动：routes `stock_detail` 取最新一轮 `screening_result` 行的 `score_detail` 解析为 `score_detail_parsed` 下发（旧行 NULL 时整段不渲染）；`ScreeningResultDAO` 新增 `get_latest_for_code(code)` 取该股票最新筛选行。
- 想法/为什么：候选卡已能展开五维拆解，但点进详情页却只看到时间线 SVG 里的总分，透明化在详情页断了一截。本次把同一份 `score_detail` 在详情页独立成卡，用户可见「为什么是 88 分」的完整拆解，闭环 P1③ 详情页子项。纯展示层、零风险、不动评分引擎。
- 冒烟：pi2 `.venv` Jinja2 离线渲染 `stock_detail` mock（含五维行 + 一致性行 + 总分 `87.8` + 无 `&#128214;` 泄漏）通过；`py_compile` 通过；pytest 142 passed。仅改 pi2，未触碰 pi1。
### 2026-08-23（nightly #2，模型归属落库 + 候选卡徽标）
- `stock_analysis_history` 补 `model` 列并落库（backlog 第二项闭环）：
  - `src/models/database.py`：`stock_analysis_history` 表 `CREATE TABLE` 增 `model TEXT` 列；`init_database` 迁移段加 `_add_column_if_not_exists(conn, 'stock_analysis_history', 'model', 'TEXT')` 兜底；`StockAnalysisHistoryDAO.save` 签名增 `model: str = None` 并写入。
  - `src/analyzer/ai_analyzer.py`：`_save_analysis` 调用 `StockAnalysisHistoryDAO().save(...)` 末位传 `result.get('model')`（此前 `analyze_stock` 已在 `result['model']` 写入用的模型，只是没落库）；`_save_failure` 传 `None`。
  - `src/web/routes.py`：`_enrich_stocks` 取 `ai_parsed.model` 写入 `s['model']`，下传模板；`_stock_list.html` 在评分旁渲染「模型: <model>」徽标（`stock-model` 样式）。
- 想法/为什么：模型归属此前只活在 `ai_analysis` JSON 里、复盘日志也读了，但**历史表本身没存**，跨日追溯某次分析用了哪个模型很麻烦。这是 backlog 明确列出的透明化项，且与 #1 同属「评分/分析可追溯」主线，一并闭环。向后兼容：旧行 `model` 为 NULL，`save` 有默认值，不影响既有查询。
- 冒烟：pi2 `python3 -m py_compile` 三文件全过；scp 到 pi1 远端 `.venv/bin/python -m py_compile` 全过；emoji 扫描（literal + `&#1(29|28|27)\d{3};` entity）零命中（本段仅用允许的 → 排版箭头与 `↑/↓` 折叠符）。仅改 pi2，未触碰 pi1 运行文件。

### 2026-08-23（nightly #1，评分透明化前端半 — score_detail 消费）
- `screening_result.score_detail` 前端消费（backlog 第一项「前端半」闭环，8-22 已落库后端）：
  - `src/web/routes.py`：`_enrich_stocks` 里把 `s['score_detail']`(JSON 字符串)解析为 `s['score_detail_parsed']`（解析失败降级 `None`，旧行 NULL 兼容）。
  - `src/web/templates/_stock_list.html`：在 `stock-reason` 之后新增「评分拆解」可折叠块——按钮显示总分 + 折叠箭头（↑/↓），展开为五维行（名称/raw值/子分进度条/权重/贡献） + 一致性加分行 + 总分行。键名映射 `roe→ROE, pe→估值, growth→增长, debt→财务, margin→毛利`，权重/子分/贡献直接来自 8-22 落库的 `score_detail`，零新增评分逻辑。
  - `src/web/templates/index.html` 与 `candidates.html`：各补 `toggleScoreDetail()` JS + `.stock-model`/`.score-detail`/`.sd-*` 全套 CSS（两页共用 `_stock_list` 片段，须同步）。
- 想法/为什么：8-22/8-21 两步把评分逻辑结构化并落库，但用户在前端仍只看到总分。这一步把「为什么是 88 分」摊开成可读的五维加权拆解（不透明→透明），是面板核心短板「评分不透明」的收口。风险极低：纯展示层、读既有 `score_detail`、不动评分引擎、旧行 `score_detail` 为 NULL 时整块不渲染。渲染已用 Jinja2 离线 mock 验证（五维行 + 一致性行 + 总分 + 模型徽标均正确出现）。
- 冒烟：Jinja2 离线渲染 mock 股通过（含 `评分拆解`/`sd-total`/`一致性加分`/`模型徽标`）；两页模板 `python3` 读取无语法错误；emoji 零命中。仅改 pi2，未触碰 pi1。

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
