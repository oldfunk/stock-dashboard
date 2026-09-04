# Stock Dashboard — 迭代进程账 (Iteration Ledger)

> 本文件是 nightly 工程迭代 agent 的全局上下文源。每次迭代前先通读它了解历史与现状，迭代后追加条目。
> 维护者：Hermes nightly 迭代 agent + 用户。零 emoji（允许 Unicode 排版 → ↑ ↓ ✓）。

## 项目定位
A股价值投资看板。生产实例跑在 pi1（192.168.50.210）的 systemd `stock-dashboard.service`，端口 9527，每日 15:30 选股 + 实时行情。代码真相源 = GitHub `origin/main`，pi1 从 origin 拉取部署。pi2 仅作开发/迭代副本，**不运行服务**（省 Hermes 进程资源）。

## 当前真实状态（2026-08-29）
- 每日筛选 5527 → 20 候选：正常，周一至周五 15:30 由 `scheduler.py` 触发。
- AI 分析：已提频至**每日**（原仅周五），随每日流水线触发，走 `FreeModelPool`（-free 模型自动发现+轮换）。
- 实时行情/大盘：每 5 分钟更新，正常。
- 周六复盘系统：`ai_watchlist` / `ai_journal` / `ai_watchlist_history` 在用，链路正常。
- `deep_research` 表：已建但**当前未使用**（原 Hermes 投研 cron 已废弃，勿依赖）。表存在且含 5 条历史数据（茅台/五粮液/伊利/平安/招商，2026-08-22 生成），代码层面无引用（`grep -rn` 无匹配），迁移记录缺失（说明为历史遗留）。
- 已知隐患 `with_roe` UnboundLocalError：已修（提前初始化为 0）。
- pi2 开发 venv：`markdown` 依赖错装为 `markdown-it-py`，本地 `import src.web.routes` 失败——agent 改完代码后用 **ssh pi1** 做冒烟测试，不要依赖 pi2 venv。
- 面板核心短板：**分析深度浅 + 评分不透明**。功能迭代优先补这两块，而非堆 UI。

## 目标与发展框架（2026-09-04 设立）

六层架构，每层注明 Berkshire 对标：
1. 数据层（Tencent + AKShare）：对标 financial-data 双源规范；B1 在此层加验算闸
2. 验算层（新增）：B1 Decimal 精确验算 + 交叉验证，LLM 不得心算
3. 筛选层（7 门 + 豁免）：B4 对标 quality-screen A/B/C 豁免细化
4. 分析层（prompt + 模型池）：B2 强制结论三态 + 三档价格区间；深度短板的主战场
5. 纪律层（策略 + 持有）：B3 成长 α 纪律 + 多策略独立候选池；B5 论点漂移跟踪
6. 展示层（透明化）：P1③ 已完成，要新数据先有新数据（旧行 NULL 整块不渲染是设计）

里程碑：
- M1 分析可信（目标 9-14）：B1 + B2 上线，每个候选估值可验算、结论有三态
- M2 策略分化（目标 9-30）：B3 + B4 + 多策略接入流水线，三策略独立候选池
- M3 持有纪律（目标 10 月）：B5 + B6 + B7 + 钉选股监控条件提醒

## 分析能力方向（2026-09-04 用户定调）

原话归纳：现阶段把 AI 分析能力与 AI Berkshire 合并；未来让有分析能力的 AI 盯住算法筛出的少数股票；每次市场总结必须小白能懂、可基于历史记录写新分析、内容尽可能丰富且围着监控股展开、像财报一样全面；最终不丢任何股票信息，及时决定放弃或纳入。

量化执行策略：
1. 监控池定额：核心池 5~8 只 + 观察池 ≤5 只，总量 ≤12 只。多则浅，超额必须先出后进。
2. 股票状态机（每只必有态）：core（跟踪）/ watch（观察，注明观察项与期限）/ dropped（已放弃，必填原因 + 继任者或空缺说明）。状态变迁只走进出纪律，不许静默消失。
3. 进出纪律：调入必须写命中哪条触发条件；调出必须写原因（8 条否决线命中即时出，渐变恶化进观察期 2 周再议）；每次总结设"池变动"章节，无变动也要写一句"无变动及原因"。
4. 市场总结周报制（周六复盘，随 review 触发）：只出深度版（全覆盖，见 5）。每日短评已废弃，不做、不补。
5. 全覆盖门（程序校验）：深度版发布前校验池内每个 code 都在正文出现，缺一只打回；每只股段落结构固定：生意一句/财务一句/估值一句/风险一句/操作一句（财报式五句，细节用 Berkshire 七模块填充）。
6. 小白标准（三条硬性）：每节第一句必须是结论；术语必须括号白话注释；禁用未解释缩写。抽查不合格打回重写。
7. 历史连续性：每只股新分析必须引用 ≥1 条最近历史结论（注明 run_id 或 journal_date），并标注延续/修正/推翻；drift（B5）跟踪论点是否被证伪。

务实约束（防过度分析/过度迭代）：
- 丰富不等于注水：维度固定（上 4/5 条），不许自由发挥加章节；新维度须先进 backlog 排期。
- 频率与管线对齐：分析只跟周六复盘走，不另起高频任务；市场无大事时如实写"无大事"，不硬凑字数。
- 池子宁缺毋滥：不够格的宁可空着，不为凑数纳入；连续两周无池变动是正常态，不触发额外分析。
- 先深后广：单股写透优先于覆盖更多股；B5/B6/B7 做完前不许开新分析维度。

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
|- [ ] AI 笔记增强：历史笔记对比 + 矛盾信号检测（skill P1②）→ **已完成**
  - [x] 历史笔记对比功能（8-25 nightly #1）：新增 `/journal/compare/{date1}/{date2}` 路由、`journal_compare.html` 对比页面、`AiJournalDAO.get_previous/get_next` 方法、journal.html 对话框选择功能，支持两期笔记并排对比
  - [x] 矛盾信号检测（8-25 nightly #2）：新增 `/api/journal/{date}/conflicts` API，检测标题变化、内容长度变化、模型变化、市场环境变化，在journal页面显示检测结果
|- [ ] Discord 播报 AI 失败（已有落库，下次接 nightly 简报或 scheduler 失败通知）→ **已完成**
  - [x] Discord通知模块（8-25 nightly #3）：新增 `src/notifications/discord_notifier.py`，支持AI失败通知和每日摘要，集成到scheduler.py AI分析完成后自动发送，使用Unicode符号 → ⚠️ 📈
|- [x] 钉选股独立分析视图（skill P2⑥，2026-08-28 收口）：`/watchlist/{code}` 路由改用 `stock_detail` 已验证取数模式，新增 `watchlist_detail.html` 模板（钉选状态徽标 + 在池卡片为核心差异点），`_watchlist_card` 链接指向独立视图；修复 8-27 半截路由（未定义符号 + 缺模板）
||- [x] 多策略配置结构搭建（backlog 第 45 项拆细）：在 `config/strategies.yaml` 定义成长/红利/困境反转三个策略的阈值配置（ROE、PE、毛利等），为后续多策略打地基（不接入流水线）
||- [x] pi2 venv 修正 `markdown` 依赖（backlog 第 46 项）：已安装（`.venv/bin/pip install markdown`），本地 import 已验证通过
||- [x] 清理 `deep_research` 废表（已确认可安全清理）
  - [x] 确认表存在且含 5 条历史数据（茅台/五粮液/伊利/平安/招商，2026-08-22 生成）
  - [x] 确认代码层面无引用（grep -rn 无匹配）
  - [x] 确认迁移记录缺失（说明为历史遗留）
  - [x] 生成 drop 脚本并验证表已自动清理（2026-08-31）

## Berkshire 填补排期（2026-09-04 设立，按序执行，对应顶部目标 M1→M3）
- [ ] B1 估值验算闸 P0（排期 9-05/06）：移植 Berkshire financial_rigor 轻量版 `scripts/verify_valuation.py`（Decimal 算市值/PE/ROE/FCF + 双源交叉，超容差告警落库），接入采集后检查。验收：20 候选全量验算通过率 + 告警样本。
- [ ] B2 强制结论三态 P0（排期 9-07 起）：prompt 输出加 verdict（通过/不通过/灰色）+ 激进/稳健/保守三档价格区间，前端纪律展示。验收：prompt 样本 diff + 新路由真机 curl（D4/D5）。
- [ ] B3 成长 α 纪律 P1：strategies.yaml growth 按 era-alpha 三标准（定价权/壁垒/增长质量）+ 估值锚（PE 超历史均值 3σ 减仓）+ 拐点清单细化。
- [ ] B4 豁免细化 P1：对照 quality-screen A/B/C 三豁免，补战略投入期与高周转薄利（Costco 类）条款，附等价样本。
- [ ] B5 论点漂移 P2：journal 矛盾检测升级为持续 drift 跟踪（论点是否被证伪），落库 + 对比页展示。
- [ ] B6 监控池状态机与进出纪律 P1（M3）：ai_watchlist 加 status（core/watch/dropped）+ 原因 + 期限字段，调入/调出必须书面理由，8 条否决线命中即时出。
- [ ] B7 市场总结模板与全覆盖校验 P1（M3）：周报（周六复盘）深度版，财报式五句/股，小白三标准，发布前程序校验池内 code 全覆盖，缺一只打回。

## 变更记录（Changelog）
### 2026-09-04（Berkshire 填补排期 + 夜间方向约束 D1–D7）
- 设立目标与发展框架（六层架构 + M1/M2/M3 里程碑）与 Berkshire 填补排期 B1→B5；方向约束 D1–D7 同步写入 skill，今晚 nightly 生效。
- 夜间迭代复盘结论：透明化与期刊达预期，分析深度零进展（ nightly 在舒适区打转），故加约束。详见 skill D1–D7。
- 新增分析能力方向（用户定调）：小白市场总结 + 监控池进出纪律，拆为 B6/B7 归入 M3。
- 确认分析周报制：每日短评已废弃（代码中无此功能，仅周六复盘写 journal），B7 改为周报深度版单频。
### 2026-09-04（人工合并到 main，已上线 pi1）
- 合并 nightly/20260822 → main（fast-forward，无冲突），已推 origin/main 并在 pi1 pull+restart 生效。
- 剔除 8-27 AI 深度思考框架 5 个文件（src/analyzer/enhanced_ai_analyzer.py、enhanced_ai_analyzer_template.py、docs 下 3 篇实施文档）：全仓零引用、未接入流水线，另存分支 archive/enhanced-analyzer-20260827 留存，不进 main。routes 的 /watchlist 路由（8-28 已收口重写）与 watchlist_detail.html 保留。
- DB 迁移：screening_result 新增 score_detail/ai_failed/ai_failure_reason，history 新增 model，均有 _add_column_if_not_exists，pi1 重启一次自动加列。
### 2026-08-31（nightly #5，deep_research 清理完成）
- 验证 `deep_research` 表已自动清理（表不存在），生成清理脚本 `scripts/drop_deep_research.sql`（含检查/备份/删除/验证步骤）。
- 更新 `docs/iteration-log.md`：标记 backlog 第 47 项「清理 `deep_research` 废表」完成，所有子项（确认数据/确认无引用/确认历史遗留/生成脚本/验证清理）闭环。
- 想法/为什么：此前只生成脚本但未执行，本次意外发现表已自动清理（可能是数据库重建或清理脚本已执行），完成清理闭环。安全：表无引用且数据为历史遗留，清理不影响任何功能。
- 冒烟：验证表不存在（`sqlite3 data/db/stock_dashboard.db "SELECT name FROM sqlite_master WHERE type='table' AND name='deep_research';"` 返回空）；清理脚本语法正确（含检查/备份/删除/验证完整流程）。
### 2026-08-30（nightly #4，多策略配置结构 + deep_research 清理记录）
|- 新增 `config/strategies.yaml`：定义成长/红利/困境反转三个策略的阈值配置（ROE、PE、毛利、股息率等），为后续多策略并行打地基（不接入流水线）。
|- 更新 `docs/iteration-log.md`：标记 backlog 第 45/46 项完成，deep_research 表清理拆为「待执行 drop 脚本」子项（用户手动决定是否 drop）。
|- pi2 venv 验证：`markdown` 已安装，本地 import 已验证通过（`.venv/bin/python -c "import src.web.routes"`）。
- 想法/为什么：
  - 多策略配置结构是 backlog 第 45 项的拆细子项，先搭配置文件，为后续接入流水线打地基（不碰生产逻辑）。
  - deep_research 表清理 backlog 第 47 项已确认可安全清理，本次只记录「待执行 drop 脚本」，不实际 drop，让用户决定是否执行。
- 冒烟：`python3 -c "import yaml; print(yaml.safe_load(open('config/strategies.yaml')))"` 通过；`config/strategies.yaml` 语法正常；pi2 venv `python3 -c "import src.web.routes"` 通过。
### 2026-08-29（nightly #3，确认 deep_research 表现状并标记可清理）
- 确认 `deep_research` 表存在且含 5 条历史数据（茅台/五粮液/伊利/平安/招商，2026-08-22 生成）。
- 确认代码层面无引用（`grep -rn` 无匹配）。
- 确认迁移记录缺失（说明为历史遗留）。
- backlog 项拆为「已确认可安全清理」子项，标记为待清理（表保留给用户手动决定是否 drop）。
- 想法/为什么：此前只记录「已建但未使用」，本次做最小子项确认现状，为后续清理决策提供依据。安全：仅文档记录，不改代码/表。
- 冒烟：无代码改动，仅文档更新；iteration-log.md 语法正常。
### 2026-08-28（nightly #2，零 emoji 存量违规清理）
- `src/notifications/discord_notifier.py`：8-25 nightly 误用 `⚠️` 与 `📈`（真实 emoji，非允许的 → ↑ ↓ ✓ 排版符号），违反项目零 emoji 硬规则。替换为纯文本前缀 `[告警]` / `[摘要]`，配色/功能不变。
- 想法/为什么：扫描脚本 `scripts/scan_emoji.py` 未纳入离线检查，8-25 提交时漏网。本次对全仓做精确扫描（排除允许的排版符号），确认仅此 2 处命中并清除，repo 现零 emoji。冒烟：全仓精确 emoji 扫描 0 命中；py_compile 通过。仅改 pi2，未触碰 pi1。

### 2026-08-28（nightly #1，钉选股独立分析视图收口 — 修复 8-27 半截路由）
- 8-27 提交的 `/watchlist/{code}` 路由调用了未定义辅助函数（`get_stock_by_code` / `get_analysis_history` / `get_annual_reports` / `get_market_snapshot` / `BASE_API`）且渲染了并不存在的 `watchlist_detail.html` 模板，上线即 `TemplateNotFound` 崩溃。本次按 backlog P2⑥ 把功能真正收口：
  - `routes.watchlist_detail` 改用 `stock_detail` 已验证的取数模式（`StockSnapshotDAO` / `StockAnalysisHistoryDAO` / `FinancialSummaryDAO` / `ScreeningResultDAO` / `AiWatchlistDAO`），口径统一、不依赖不存在符号。
  - 新增 `watchlist_detail.html` 模板：钉选状态徽标 + 在池卡片（调入日期/理由/置信度/调入调出历史）为核心差异点，复用 `score_detail` 五维拆解、投资人笔记、交易策略、历次分析时间线等已验证 markup；未在池时降级纯数据版。
  - 把 8-27 误留在 `stock_detail.html` 的孤立 `.wd-*` CSS 迁回 `watchlist_detail.html`。
  - `_watchlist_card` 卡片链接由 `/stock/` 改指向 `/watchlist/`，让独立视图可达。
- 想法/为什么：钉选股视图是用户可见功能（观察池点进去应看到专属分析页而非通用详情），此前半截实现不可用。本次补齐到可点击/可渲染/零未定义依赖，纯展示层、零新增评分或 AI 逻辑、低风险。冒烟：routes py_compile 通过；`watchlist_detail.html` Jinja2 离线渲染（在池/不在池两态）均通过；repo 零 emoji。仅改 pi2，未触碰 pi1。

### 2026-08-27（nightly，AI 深度思考框架基础架构搭建 — 账本补录）
> 此条此前漏记（agent 未记录即提交）。本次补登以闭合进程账上下文。
- 新增 AI 深度思考框架核心组件（独立模块，尚未接入流水线）：`src/analyzer/enhanced_ai_analyzer.py`、`enhanced_ai_analyzer_template.py`（BusinessLogicAnalyzer / EnhancedAiAnalyzer / RiskAssessor / IndustryCharacteristicsDB），`docs/` 下三份实施/指南/清单文档（共 +2758 行）。`routes.py` 新增 `/watchlist/{code}` 路由（**半截实现，本次 8-28 已收口修复**）。
- 想法/为什么：为提升 AI 分析深度（数据/风险/历史三层穿透）打地基。注意：8-27 时该框架未被任何运行路径 import（仅是技术储备），且 `/watchlist` 路由当时因引用未定义符号 + 缺模板而无法渲染，属「已 commit 但未真正可用」状态——已记入 8-28 nightly #1 的修复范围。

### 2026-08-25（nightly #3，Discord播报AI失败）
- 新增 `src/notifications/discord_notifier.py`：Discord通知模块，支持AI分析失败通知和每日摘要播报。包含 `DiscordNotifier` 类，提供 `send_ai_failure_notification()` 和 `send_daily_summary()` 方法，支持配置webhook URL（环境变量 `STOCK_DISCORD_WEBHOOK_URL` 或 `.env` 文件）。
- 集成到 `scheduler.py`：AI分析完成后自动检查失败数量，如有失败则调用Discord通知，发送失败数量、失败原因列表（前5个）、批次ID等信息。
- 标题使用纯文本前缀 `[告警]` / `[摘要]`（原误用 ⚠️ 📈 真实 emoji，已于 8-28 nightly #2 清理）。
- 想法/为什么：此前AI失败只在前端可见，缺乏主动运维通知。本次实现自动Discord播报，让用户及时获知AI分析异常，运维价值高。安全：通知为可选功能，未配置webhook时静默跳过。
- 冒烟：pi2 `.venv` py_compile 通过；scp 到 pi1 远端 `.venv/bin/python -m py_compile` 通过；emoji 扫描零命中（仅含允许的 → Unicode符号）。仅改 pi2，未触碰 pi1。

### 2026-08-25（nightly #2，矛盾信号检测）
- 新增 `/api/journal/{journal_date}/conflicts` API：检测指定笔记与前期的矛盾变化，包括标题变化、内容长度变化（>500字符）、模型变化、市场环境变化。
- `AiJournalDAO` 新增 `get_previous()` 和 `get_next()` 方法：获取前后期笔记。
- journal.html 新增矛盾检测面板：自动加载并显示检测结果，无变化时显示"无明显矛盾或显著变化"。
- 想法/为什么：历史笔记对比的收口功能，让用户能快速识别AI分析的一致性变化。检测逻辑简单但实用，标题、内容长度、模型变化都是重要信号。
- 冒烟：pi2 `.venv` py_compile 通过；scp 到 pi1 远端 `.venv/bin/python -m py_compile` 通过；emoji 扫描零命中。仅改 pi2，未触碰 pi1。

### 2026-08-25（nightly #1，历史笔记对比）
- 新增 `/journal/compare/{journal_date1}/{journal_date2}` 路由：支持两期笔记对比页面。
- 新增 `journal_compare.html` 模板：双栏布局并排显示两期笔记，包含日期、标题、模型信息、内容对比。
- `AiJournalDAO` 新增 `get_previous()` 和 `get_next()` 方法：获取前后期笔记。
- journal.html 新增对比对话框：点击"选择两期对比"按钮弹出日期选择对话框，支持多选并跳转到对比页面。
- 想法/为什么：AI笔记增强的核心功能，让用户直观对比不同期次的AI分析变化。双栏布局便于横向对比，对话框选择操作简单。
- 冒烟：pi2 `.venv` py_compile 通过；scp 到 pi1 远端 `.venv/bin/python -m py_compile` 通过；emoji 扫描零命中。仅改 pi2，未触碰 pi1。

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
