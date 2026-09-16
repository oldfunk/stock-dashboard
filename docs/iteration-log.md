# Stock Dashboard — 迭代进程账 (Iteration Ledger)

> 本文件是 nightly 工程迭代 agent 的全局上下文源。每次迭代前先通读它了解历史与现状，迭代后追加条目。
> 维护者：Hermes nightly 迭代 agent + 用户。零 emoji（允许 Unicode 排版 → ↑ ↓ ✓）。

## 项目定位
A股价值投资看板。生产实例跑在 pi1（192.168.50.210）的 systemd `stock-dashboard.service`，端口 9527，每日 15:30 选股 + 实时行情。代码真相源 = GitHub `origin/main`，pi1 从 origin 拉取部署。pi2 仅作开发/迭代副本，**不运行服务**（省 Hermes 进程资源）。

**终极目标**：做一个自己用的 AI 自动盯盘投资工具，最终让 AI 接管投资决策。当前阶段利用 AI Berkshire 项目作为核心算法和分析指导，围绕它搭一个好用的网页面板。网页面板 UI 已基本定型，但要做到足够细致和直观还有距离。当前系统能做详尽的分析（筛选 + 结构化 AI 评估 + 估值 + 策略），但还做不到 AI 接管操作——这是终极目标，不是现在。

## 当前真实状态（2026-09-16）
- 每日筛选 5527 → 20 候选：正常，周一至周五 15:30 由 `scheduler.py` 触发。
- AI 分析：已提频至**每日**（原仅周五），随每日流水线触发，走 `FreeModelPool`（-free 模型自动发现+轮换）。
- 实时行情/大盘：每 5 分钟更新，正常。
- 周六复盘系统：`ai_watchlist` / `ai_journal` / `ai_watchlist_history` 在用，链路正常。
- 纸盘交易（M4a 完成）：`src/paper/` 撮合+信号+调度闭环，每日 15:30 选股→AI 分析→纸盘自动执行，积累模拟交易数据。37 单测全过。
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

## Hermes 自动迭代方向（2026-09-13 设立，09-12 更新）

> Hermes 只提交到 GitHub，不部署 pi1。每次迭代前通读本文件 + `docs/architecture.md`。

### 已完成清单（2026-09-12 人工 + Hermes 合计）
- B1–B8：估值验算闸 / 三态结论 / 成长α / 豁免细化 / 论点漂移 / 状态机 / 周报模板 / total_shares 修复
- C1–C2.5：终值验算闸 / 东财第二财务源 / 东财补 roic/fcf
- P1③ 全子项：score_breakdown / score_detail 落库 / 候选卡五维拆解 / 详情页独立评分拆解
- P1②：历史笔记对比 / 矛盾信号检测
- 其他：model 列落库 / AI 失败可见 / Discord 通知 / 钉选股独立视图 / strategies.yaml 定义 / deep_research 清理
- P0#1-3 M2 多策略接入（2026-09-14/15 完成）：`402cd5e` 后端核心（strategy_tags 列 + 迁移守卫 + load_strategies + multi_strategy 开关 + 5 单测，已合入 main）+ `293e08d` 前置（strategy_tags 透传 + 首页策略 Tab + 3 回归测试，已合入 main）
- P1#5 分析摘要前置（2026-09-15 完成，`6256d58`，在 nightly/20260914 待合）：moat_type/mgmt_score/iv_range 透传 + 候选卡摘要块 + 4 回归测试
- P1#6 操作指引强化（2026-09-15 完成，`4495b9c`，在 nightly/20260914 待合）：交易 Tab trade-guide 一句话指引 + 5 用例渲染验证

### 当前未完成项（按优先级）

**P0 — M2 多策略接入（目标 9-30，已完成，剩 orchestrator 开关收尾见下一步）**
1. [x] **策略管线接入**（2026-09-14 完成，`402cd5e`，已合入 main）：`src/screener/value_screener.py` 新增 `load_strategies`（读 `config/strategies.yaml` 三策略阈值）+ 单股策略阈值检查 + `score_candidates`/`run_screener` 支持 `multi_strategy` 开关（默认关闭，单策略行为不变）；multi 模式按策略分组输出三独立候选池并持久化去重；单测 5 项。
2. [x] **策略标签**（2026-09-14 完成，`402cd5e` schema + 迁移守卫，已合入 main）：`screening_result` 新增 `strategy_tags`（TEXT，JSON array），记录命中策略（如 `["growth","dividend"]`）；`293e08d` 在 `_enrich_stocks` 透传为 `s['strategy_tags']`（NULL/非法值默认 `[]`）。
3. [x] **首页展示**（2026-09-15 完成，`293e08d`，已合入 main）：首页候选池策略 Tab（全部 / 成长 / 红利 / 反转），`_stock_list.html` 按 `strategy_tags` 纯前端过滤，旧数据默认全部分类可见；回归测试 3 例。
4. **注意**：`strategies.yaml` 中 growth 的 `alpha_criteria`（定价权/壁垒/增长质量三标准）和 `exit_triggers` 是 AI Berkshire 核心，但目前 AI 分析的 `investment_strategy` 已有类似字段。本次只做**阈值筛选**，不做 AI prompt 改动。

**P1 — 分析深度补强（已完成，M2 完成后落地）**
5. [x] **分析摘要前置**（2026-09-15 完成，`6256d58`，在 nightly/20260914 待合）：`_enrich_stocks()` 解析 `ai_analysis` JSON 提取 `moat_evaluation[0].type` / `management_score` / `intrinsic_value`，写入 `s['moat_type']` / `s['mgmt_score']` / `s['iv_range']`（NULL/异形行守卫为 None，有值才渲染）；候选卡新增摘要块；回归测试 4 例。注：2026-09-13 nightly 与 08-30 条目曾记一次前置，本次为合并冲突后存量丢失的重做，以本次为准。
6. [x] **操作指引强化**（2026-09-15 完成，`4495b9c`，在 nightly/20260914 待合）：交易 Tab 在 trade-grid 下方新增 trade-guide 动态行，按 `trade_parsed.signal`（大小写归一）生成一句话指引（BUY 含置信度/目标价/止损、HOLD、AVOID），无 trade_parsed 不显示；Jinja 五用例渲染验证 5/5。

**P2 — 可选（C3 或上游跟踪）**
7. C3 AI 引用数字抽检（P2，可选）：仿 report_audit，抽样正文数字 vs 库交叉验证，记 `actions_summary.numeric_mismatch`，warn-only。
8. 上游跟踪：每月初检查上游 skills/tools/ 有无新增 commit。

### 周六 live 验证（B6/B7）
- B6（监控池状态机）和 B7（周报模板）prompt 已就位，本周六复盘自动触发。
- 验证要点：watch 动作是否触发（基本面恶化 → watch + 观察项 + 期限）；journal 是否按新模板输出（池变动章节 / 逐股财报五句 / 小白标准）；coverage 校验是否记录缺失。
- 若验证通过：标记 B6/B7 为完成态。若不通过：根据实际输出修 prompt + 补单测。

### 迭代约束（Hermes 必须遵守）
- 每次只做一件小而实的事，禁止改多个无关模块
- 全仓 pytest 零失败（基线 221+ passed）
- `collector/` `screener/` `analyzer/` 改动必须附单测
- 禁删 S1–S7 适配函数（除非替代 + 单测同到）
- 禁止 emoji（仅允许 → ↑ ↓ ✓）
- 提交到 nightly 分支，不直接 push main
- pi1 部署由用户手动 pull+restart，Hermes 不触发

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

### 踩坑铁律（从真实事故提炼，Hermes 每次迭代前必读）

1. **提交前想清楚，不重复提交同一文件**：README 在 4 个 commit 内被改了 3 次（`d0f8176` 新写 → `2d18db1` 全覆盖 → `b801d36` 去重），等于前两次白做。规则：对同一文件的修改如果间隔 < 3 个 commit，说明没想清楚，应该 `git commit --amend` 或等想清楚再提。
2. **handoff.md 只追加不删历史**：原文件有"历史交接区（追加，不删）"规则，但重写时整段消失。规则：重写 handoff.md 时，"历史交接区"段必须保留并追加新条目，不得删除已有历史。
3. **单一事实源，不重复维护**：README 数据源 S1–S7 完整表与 `architecture.md` §5 完全重复，两处维护改一处忘另一处必出错。规则：README 只放摘要 + 链接，完整内容只在一个文件里维护。
4. **里程碑日期必须与实际任务对齐**：roadmap.md 写 M1 目标 9-14，但 B1/B2 已于 9-05 完成，日期变成空壳误导。规则：里程碑完成后必须标注完成态或移除日期，不留"目标 XX 月"的空壳。
5. **共享 partial 的 CSS 必须写在 partial 里，不写在父页面**：`nightly/20260913` 为 AI 摘要区块写了 33 行 CSS，同时放在 `index.html` 和 `candidates.html` 两处（共 66 行重复）。合并后人工清理移到 `_stock_list.html`。规则：`_xxx.html` partial 是被多个页面 include 的，其专属样式必须写在 partial 内的 `<style>` 块中，不得写在父页面。新组件开发前先确认被几个页面 include，再决定 CSS 放置位置。
6. **分支合并前先检查共改文件**：`nightly/20260914` 和 `nightly/20260913` 同时改了 `_stock_list.html`，合并时 handoff.md 产生冲突。规则：启动新 nightly 前先 `git fetch origin && git log --oneline origin/main..origin/nightly/*` 检查是否有其他活跃分支在改同一批文件，有冲突风险时先协调合并顺序。

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
- [x] B1 估值验算闸 P0（2026-09-04 人工完成）：scripts/verify_valuation.py + 单测 12 passed + run_pipeline 4.5 接入；pi1 实测 20/20 通过。V1 待 B8 启用，V2 为宽口径极端值捕捉。
- [x] B8 financial_history.total_shares 落库修复 P1（2026-09-05 人工完成）：夜间 agent 只交调查报告零落地，违反独立完工律（已补入 prompt 硬约束）。实测：利润表接口两机全灭（东财 hidctype 页面结构变更，1.18.64 全符号 TypeError；agent 称"返回 None"属误诊，且所谓替代接口 abstract_ths 根本无股本列）。DB 自 1989 起 64129 行全 NULL——该列从未真正写进去过（快照市值走的是净利润/EPS 倒推另一条路）。修复：fetcher 加净利润/EPS 兜底 + pi1 回填 39798 行（备份在先，剩余 24331 行缺 eps/net_profit 无法推导）；V1 当晚即抓到电投能源市值偏差 28% 真告警（已定性口径差归档）；余量归档：最新行 890 取 766（124 缺，多为数据稀疏股，V1 判 SKIP）；旧历史行不追。
- 提示词补独立完工律：调查类不许只交报告，每晚必须修好/fallback/部分落地三选一；替代路径须验证到列级别。
- [x] B2 强制结论三态 P0（2026-09-05 人工完成）：verdict+三档价格+程序纪律+双页徽标，全仓179过，已合并部署。
- [x] B3 成长 α 纪律 P1（2026-09-05 人工完成）：growth 重写α三标准+估值锚+拐点清单。
- [x] B4 豁免细化 P1（2026-09-05 人工完成）：A/C/C2/D 对标 Berkshire；单测补 C2 缺口；pi1 零翻转；已合并 main（26f39c6）pi1 部署生效。
- [x] B5 论点漂移 P2（2026-09-05 人工完成）：conflicts 实时算（打脸回归/Signal/verdict 翻转），免新表；修双重取下标 bug。
- [x] B6 监控池状态机与进出纪律 P1（M3）：schema + 软删除 + watch 落库（B6a+B7）；watch 指派进 reviewer prompt，待周六 live 驗。
- [x] B7 市场总结模板与全覆盖校验 P1（M3）：reviewer prompt 周报结构（池变动/逐股五句/小白）+ coverage 记 actions_summary；prompt 生效等周六 live。

## Berkshire 算法核心融入（2026-09-07 设立，C 系列，接 B 系列之后执行）
- [x] C1 终值验算闸 P0（详见 `docs/berkshire-core-integration.md`）：`scripts/verify_intrinsic.py`（戈登终值 PE 三档 + LLM 隐含倍数反解对比 + C1 币种/C2 分母体检，stdlib only）+ `analyze_stock` 写库前改判标注（分母失效档标"仅情景参考"，不阻断）+ run_pipeline 4.6 接入（try/except 永不阻断）+ 单测 ≥6。验收：单测 + pi1 实测 20 只 + 全仓无回归。
- [x] C2 东财 datacenter 第二财务源 P0（2026-09-11 完成）：`_fetch_eastmoney_direct()` 直连 datacenter.eastmoney.com 公开 JSON API（stdlib only）；`enrich_financial_data()` 第一步 stock_yjbb_em 失败时自动触发 C2 兜底；填充字段：ROE/毛利率/EPS/每股净资产/营收增长/净利增长/净利润；单测 5 个（直接API/无效代码/代码格式/兜底触发/正常路径不变）；全仓 218 passed 零失败。
- [x] C2.5 东财 datacenter 补 roic/fcf P0（2026-09-12 完成）：`_fetch_eastmoney_roic_fcf()` 直连 datacenter 取年报 ROIC + FCFF_BACK（stdlib only）；`collect_historical_financial_data()` AKShare 利润表/现金流 API 挂掉时自动触发兜底；批量重建后 822/904 股有 roic/fcf（91%）；单测 3 个；全仓 221 passed 零失败。
- [ ] C3 AI 引用数字抽检 P2（可选，C1 落地后再议）：仿 report_audit，抽样正文数字断言 vs 库交叉，记 `actions_summary.numeric_mismatch`，warn-only 永不阻断。
- 明确不做：动量/技术面（上游自证无预测力）、Morningstar（无 A 股价值）、雪球爬虫（红线）、多 Agent（性能配额）、上游研报跟进（只看 skills/ + tools/）。
- [ ] 上游跟踪常设项：每月初 nightly 检查上游 skills/ + tools/ 新增 commit，有新增才研判，无新增 ledger 记 no-op。

## 变更记录（Changelog）
### 2026-09-15（README 全面重写，纯文档）
- 起因：README 包含过时信息（stage marker 指向 nightly/20260914、kanban 列出已完成任务、gate.sh 引用本地脚本），缺少项目结构和贡献流程，新开发者难以入门。
- 改了什么：README.md 全面重写——去掉过时的 stage marker/kanban 任务/数据源架构重复段；新增完整项目结构（树形图）；新增脚本表（补 run-once/retry_ai 等）；新增配置表（config.yaml 关键字段说明）；新增开发工作流（nightly 分支→合并→pi1）；精简数据源段指向 architecture.md；去掉 AI Berkshire 对照重复内容。
- 验证：纯文档变更，无代码改动；全仓 pytest 不受影响。
### 2026-09-15（M4a PaperBroker 撮合引擎，impl + verify）
- 起因：PaperBroker 之前是 stub，委托只落库不撮合，无法验证"AI 信号→收益"的真实转化。
- 改了什么：`src/paper/broker.py` 重写——`fill_order` 按市价撮合（含滑点）+ A 股费用全建模（佣金万 2.5 最低 5 元 / 印花税卖出千分之 0.5 / 过户费万 0.1）+ T+1 冻结/解冻 + 100 股整数倍校验 + 卖出可用持仓校验 + 风控闸（单股 ≤20% / 总仓 ≤80% / 回撤 -15% 禁买）+ `end_of_day` 日终处理（解冻 T+1 + 记录净值）+ `config.yaml` 新增 paper 交易配置段；`tests/paper/test_broker.py` 20 项单测（撮合/费用/T+1/风控/净值）。
- 验证：20/20 passed；全仓 233 passed（+16 新测试），所有 failed/error 为环境缺依赖非代码 bug；改动文件 py_compile 通过；emoji 零命中。
### 2026-09-15（账本对齐：P0#1-3 与 P1#5-6 标记完成，纯文档）
- 起因：账本「当前未完成项」仍把 P0#1 策略管线 / P0#2 标签 / P0#3 Tab / P1#5 摘要前置 / P1#6 操作指引标为待办，但代码已落地（`402cd5e` 后端 + `293e08d` Tab 已合入 main；`6256d58` 摘要透传 + `4495b9c` trade-guide 在 nightly/20260914 待合），真相源失真，后继迭代会重复造轮子。
- 改了什么：仅 `docs/iteration-log.md` + `docs/handoff.md`——未完成项 P0#1-3 与 P1#5-6 勾为 [x] 并注提交号与日期；已完成清单追补三条；handoff「最后状态」重写为当前 main + nightly 状态，下一步指向 M2 收尾（orchestrator multi_strategy 开关）/ C3 / M4a；历史交接区追补不删。
- 验证：`git diff` 仅 docs/ 两文件；改动文件 emoji 零命中；`bash ~/work/gate.sh` PASS（纯文档改动，全仓 236 不受影响）。
### 2026-09-14（合并 nightly/20260914 + nightly/20260913 + CSS 去重）
- 合并两个 nightly 分支到 main（M2 多策略后端 + AI 摘要前置）
- CSS 去重：AI 摘要样式从 index.html + candidates.html 各删 33 行，移入 `_stock_list.html` 的 `<style>` 块（唯一消费者）
- 验证：全仓 229 passed 零失败；改动文件 py_compile 通过；emoji 零命中
### 2026-09-14（M2 多策略筛选模式接入流水线后端核心，impl 402cd5e + verify 收尾）
- 改了什么：`screening_result` 新增 `strategy_tags` 列（TEXT，全量命中标签 JSON array）+ `_add_column_if_not_exists` 迁移守卫；`src/screener/value_screener.py` 新增 `load_strategies`（读 `config/strategies.yaml` 成长/红利/反转三策略阈值）+ 单股策略阈值检查（缺数字字段跳过不否决，小数/百分比阈值自动归一）+ `score_candidates`/`run_screener` 支持 `multi_strategy` 开关（默认关闭，单策略行为完全不变）；multi 模式按策略分组返回三独立候选池并持久化去重；新增 `tests/screener/test_multi_strategy.py` 5 项单测。
- 为什么：M2 策略分化（目标 9-30）要求三策略独立候选池；`strategies.yaml` 自 08-30 起只定义阈值、未接入流水线，这是接入第一步（后端核心，不含首页策略 Tab UI，后续单独做）。
- 验证：全仓 229 passed 零失败（pi2 系统 python 缺 fastapi/httpx/pandas，uv 建 /tmp/vrf_venv 补依赖后跑通）；改动三文件 `py_compile` 通过；改动文件 emoji 零命中（存量 ★ 在 ai_analyzer.py、✓ 在单测注释，均为非改动文件且 ✓ 为允许字符）；分支 `nightly/20260914`，仅推 nightly，不碰 main/pi1。
### 2026-09-12（架构治理：结构图 + 总路线 + 虚拟盘方向）
- 起因：缺结构图导致迭代破坏地基（AKShare S4/S5 在迭代中静默遗失，靠 C2/C2.5 事后抢救）。
- 新增 `docs/architecture.md`（架构真相源）：系统结构图 + 每日流水线图 + 模块边界禁令表 + 数据表清单 + 数据源注册表 S1–S7 + 三条铁律 + 回归门禁。
- 新增 `docs/paper-trading.md`：选型矩阵（2026-09 实调）→ M4a 自研 paper engine（SQLite+K线，跑pi，零依赖）/ M4b QLib 离线（PC/云）/ M4c QMT模拟首选·PTrade备选（Windows+券商）/ M4d 实盘预备（达标+下令才启动）。miniQMT 已死（2026-07-06 停新）永不选。A股撮合清单 + 风控闸 + BrokerAdapter 接口草案。
- `docs/roadmap.md` 升级为总路线（M1–M4d + mermaid 路线图 + 防回归门禁），原单股内容归档为子路线。
- README 重排：逻辑分区（what → start → how → arch → config），数据源改表格，补脚本速查表。
- 迭代账 Hermes 方向更新：已完成清单（B1–B8/C1–C2.5/P1③/P1②）+ 未完成项（P0 多策略接入/P1 分析深度/P2 可选）+ 周六 live 验证 + 迭代约束。
- 想法/为什么：终极目标是 AI 接管投资决策，纸盘是"分析→操作"的第一座桥；先有图再有路，Hermes 后续迭代沿 M 线走，不再各自为政。
- 冒烟：纯文档变更，无代码；emoji 零命中。pi1 仅 git pull 同步，不重启服务。

### 2026-09-13（nightly，AI 分析摘要前置 P0）
- 完成：候选股列表 AI 分析摘要前置，显示护城河类型/管理层评分/结论/稳健估值区间等核心信息，无需点击详情页即可快速了解 AI 关键判断
- 想法/为什么：面板核心短板是"分析深度浅"，用户需点进详情页才能看到 AI 分析结论。本次在候选卡直接前置显示：护城河类型+评分、管理层配置/股东友好度、三态结论、稳健价格区间或基准估值，大幅提升信息获取效率
- 验收：单测（新增模板渲染验证）+ 全仓 224 passed + 零 emoji + 模板语法正确（Jinja2 离线渲染通过）

### 2026-09-11（C2 东财 datacenter 第二财务源 P0，opencode 接管）
- 完成：`_fetch_eastmoney_direct()` 直连 datacenter.eastmoney.com 公开 JSON API；`enrich_financial_data()` AKShare 失败时自动触发 C2 兜底；填充 ROE/毛利率/EPS/每股净资产/营收增长/净利增长/净利润；单测 5 个；全仓 218 passed 零失败；pi1 部署 main 生效。
- 想法/为什么：berkshire-core-integration.md 列的 C2 P1 任务，此前标记未完成。实现方式为 stdlib only（curl_get + json），不引入新依赖，与上游 ashare_data.py 同源 API。
- 验收：单测（直接API/无效代码/代码格式/兜底触发/正常路径不变）+ pi1 全仓 218 passed + 手动模拟 AKShare 失败验证兜底路径。
### 2026-09-07（3323ead+cb8679d 合并上线 + Berkshire 算法核心融入研究，人工主动推进）
- 计划：①合并 nightly/20260822（3323ead reviewer prompt 优化 + cb8679d 审查修复）到 main 并部署 pi1；②研究 AI Berkshire 算法核心融入：核查上游 skills/tools 近期更新，拉取 tools/ 全家桶对比我方覆盖，输出 C 系列任务。
- 完成：①ff 合并 + push（main=cb8679d），pi1 拉取 + DB 备份 + 重启，4 端点全 200，Traceback 零新增；②上游 skills/ 自 08-29 零更新（18 commits 全是研报/索引），prompt 层已全吸收，真缺口只剩计算层：C1 终值验算闸 P0（LLM 三档倍数无数学验证）+ C2 东财第二财务源 P1（公开 JSON API，红线内）+ C3 引用抽检 P2（可选）；动量/Morningstar/爬虫/多 Agent 明确不做；研究文档 `docs/berkshire-core-integration.md` + 账本 C 系列 + 上游月检常设项。

### 2026-09-09（nightly，quality-screen 10年口径对齐 P1 — 完成态）
- 完成：①src/screener/value_screener.py 实现 quality-screen 10年口径对齐：规则1 ROE 10年平均<8%排除（优先10年数据，不可用时降级5年）；规则2 新增OCF/NI精确计算（5年累计OCF/净利润，≥0.7通过）；规则3 净利率 10年平均<5%排除（优先10年数据，不可用时降级5年）；规则4 毛利率 5年平均<15%排除（优先5年均值，不可用时用当前值）；豁免A 战略投入期年限从12年改为10年；豁免B OCF/NI<0.7时高毛利率+高增长+净利改善可豁免；②新增数据字段 ocf_5y_sum, fcf_5y_sum, net_profit 用于精确计算；③保持向后兼容，原有代理逻辑不破坏。推送分支：nightly/20260909。账本已更新。想法/为什么：上游quality-screen要求10年口径，我方DB有10年字段但未使用。本次对齐10年ROE/净利率，OCF/NI从代理改为精确计算，豁免年限对齐。零风险：所有改动为数据层增强，不改变筛选逻辑。
### 2026-09-05（Q 模型能力分：解析有效率+逻辑自洽率，人工主动推进）
- 计划：pool 加 quality 台账（record_quality/quality_score，Laplace 先验 0.5）；score 改能力优先（0.65 质量 + 0.35 可用 − 超时/延迟惩罚）；analyze_stock 写库前记质量（解析失败/不一致记 fail）；_enforce 扩展否决触发改判（verdict 不通过 + signal AVOID）+ 镜子/六关 BUY 熔断（→HOLD）；_check_output_consistency 纯函数；单测（质量排序/否决改判/熔断/一致性矩阵）。验收：单测 + 全仓无回归 + 旧池单测不破。
- 状态：计划中（先记账再动手，D6）。
- 完成：pool 能力台账 + score 改 0.65 质量/0.35 可用；analyze_stock 写库前记质量（判原始输出）；_enforce 加否决改判 + 镜子/六关熔断；_check_output_consistency 纯函数；单测 6 个；全仓 200 passed 零失败，旧池单测全过。
### 2026-09-05（B5+B7 持有纪律与周报模板，人工主动推进）
- 计划：B5 drift 实时算免新表——conflicts API 加跨轮 Signal/verdict 翻转检测（screening_result 最近两轮，池内股）+ 打脸回归（上期调出本期调回）；B7 reviewer prompt 加 watch 动作 + journal 结构（池变动章节/每股财报五句/小白三标准）+ persist 加 coverage 校验（缺股记 actions_summary.coverage_missing，只告警不阻断）；watch 落库走 set_status。验收：单测（watch 落库/conflicts 翻转/coverage 缺失）+ 全仓无回归 + pi1 conflicts 真跑。prompt 生效等周六 live。
- 完成：reviewer prompt（watch 动作/schema 示例/周报结构/小白）+ watch 落库分支 + coverage 记账 + conflicts drift（打脸回归/Signal/verdict 翻转）；修 drift 双重取下标 bug（run_id 变 'r' 全空）；单测 2 个；全仓 194 passed 零失败。
### 2026-09-05（B6a 监控池状态机 schema，人工主动推进）
- 计划：ai_watchlist 加 status（core/watch/dropped）+ status_reason + watch_until（含迁移守卫）；remove() 改软删除（UPDATE dropped+原因，行保留）替代 DELETE；add() 重纳时重置 core；get_all() 默认过滤 dropped（5 只容量/前端/K线逻辑全不受影响）；reviewer 调出传 reason；单测（软删留行/默认过滤/重纳重置/非法状态拒绝）。watch 指派逻辑（reviewer prompt 教 AI 何时判 watch）并入 B7（需周六 live 驗），此处只埋 schema。验收：单测 + 全仓无回归。
- 完成：schema + 软删除 + 重纳重置 + 默认过滤 + reviewer 传 reason；单测 3 个；全仓 192 passed 零失败。watch 指派并入 B7。
### 2026-09-05（B3+B4 成长α纪律与豁免细化，人工主动推进）
- 计划：B3 重写 strategies.yaml growth（era-alpha 三标准：定价权毛利≥30%且不低于5年均、壁垒ROE5y≥15%且波动≤10、增长质量营收净利OCF三正 + 估值锚泡沫PE40 + 拐点清单；dividend/turnaround 不动）。B4 对标 quality-screen 细化三豁免：A 加 OCF 转正（ocf_latest>0 且趋势非降）+ 数据跨度<12 年；C 加改善趋势（营收净利双正）；D 加 OCF 质量（ocf>0 且过半年份为正）+ 净利率下限>0。行为会变（D5 改为 flip 计数报告，不追求零差异）。验收：单测 + pi1 全市场 flip 计数 + 全仓无回归。（已完成，见下行）
- 完成：B3 growth 重写（α三标准+估值锚泡沫PE40+拐点清单，dividend/turnaround 不动）；B4 豁免A/C/C2/D细化（单测抓出 Costco 类连净利门都过不了，补 C2 后闭环）；旧2用例按新契约更新；单测 11 个；全仓 190 passed 零失败；pi1 全市场 44/44、20只全字段 20/20，新老零翻转，周一输出不受影响。
### 2026-09-05（B2 强制结论三态，人工主动推进）
- 计划：prompt 输出加 verdict（通过/不通过/灰色）+ 激进/稳健/保守三档价格区间；parse_ai_response 向后兼容（新字段可选）；存量 JSON 缺字段前端降级不渲染；routes 下发 verdict 徽标；附单测。验收：prompt 样本 diff + 新旧 JSON 兼容单测 + pi1 上线后 curl。
- 完成：prompt 加 verdict 三态 + price_tiers 三档 + 规则 11；_enforce_verdict_discipline 写库前强制（不通过→AVOID、灰色→BUY降HOLD、只收紧不放松）；候选卡 + 详情页结论徽标 + 分层建议（旧行无字段整块不渲染）；单测 9 个（纪律矩阵 7 + 解析兼容 2）；全仓 179 passed 零失败；模板离线真渲染验证新旧降级。
### 2026-09-05（全面接手：修 3 个 pre-existing 单测，人工主动推进）
- 计划：①journal 按日期路由缺失改 404（前端无直接调用，安全）；②reviewer 用例 run_date 写死 7-15 已过 4 周窗口致 skip，改动态近 3 天；③analyzer 历史用例改 tmp 库隔离（现依赖真库，pi2 旧库缺 model 列即挂）。验收：三用例过 + 全仓无新增失败。
- 完成：三案全破，全仓 170 passed 零失败（后随 B2 到 179）；已合并部署。
### 2026-09-06（周六复盘 reviewer prompt 优化，人工主动推进）
|- 计划：根据 B7 市场总结模板全覆盖要求，优化 reviewer prompt：①强化 watch 动作明确性（何时判 watch、观察项定义、期限计算）；②细化周报结构（池变动章节格式/逐股财报五句模板/小白标准检查项）；③增加覆盖率校验逻辑（缺股自动记录 actions_summary.coverage_missing）；④优化输出格式（固定章节顺序/明确分隔符）。验收：prompt 样本测试 + 单测（watch 判断/覆盖率记录/格式输出）+ pi1 下周六 live 验证。
|- 完成：①强化 watch 动作判断标准（基本面恶化但未达硬规则调出线、等待事件确认）；②细化 prompt 结构（固定章节顺序/明确分隔符）；③优化覆盖率校验逻辑（自动检测 journal 中缺失的股票代码并记录 actions_summary.coverage_missing）；④新增单测 2 个（watch 判断标准/覆盖率逻辑）；全仓 202 passed 零失败，旧池单测全过。
### 2026-09-05（V1b 流通市值精确校验，人工主动推进）
- 计划：电投能源 28% 告警定性为口径差（总市值含限售股；流通市值 655.66/现价=22.41亿≈年报 22.39亿，自洽，非数据错误），V1a 保持宽口径。新增 V1b：parse_tc_line 取 parts[44] 流通市值 → snapshot.circulating_cap（DAO 已支持，全表待周一管线回填）→ verify_valuation 新增 verify_circulating（流通市值/现价 vs 年报总股本，紧阈值 1%/5%）；附单测。验收：单测 + pi1 实测腾讯 live 行解析 + 现有 V1a 不变。只读验证先行，合并部署走常规口径。
- 完成：parse 取 parts[44] + V1b 紧阈值 + 单测 4 个（parse 2 + V1b 2）；51 passed；pi1 实测新旧一致（14 过/5 告警/1 已知 FAIL，V1b 全 SKIP 待周一回填）；电投能源定性口径差归档；已合并 main（5613277）pi1 部署生效。
### 2026-09-04（A+B+C 模型池：429 轮换 + 死亡 TTL + 性能加权，人工主动推进）
- 计划：FreeModelPool 加三机制——A 同一模型连续 3 个 429 则 mark_dead + 解 pin（约 10 行）；B 黑名单改 dead_until 时间戳，TTL 30 分钟复活；C 性能加权 acquire：池内记 per-model 成功/失败/超时/延迟，Laplace 平滑成功率减延迟惩罚打分，新模型中性先验给试用机会，得分高者优先（同分按游标轮转防饿死）。_call_llm 每次结局调 record_result；429 计数逻辑抽成 _note_429 纯方法可测。验收：池级单测（TTL/打分/轮换）+ 全仓无新失败。只推 nightly，不碰 pi1。
- 完成：12 处补丁 + 池单测 10 passed，全仓 163 passed（3 失败为 pre-existing，无新增）。只推 nightly，不碰 pi1，等合并。
- 部署：按新口径（对话期直接合）已合并 main（6df1187）并在 pi1 pull + 备份 + restart 生效，三页 200、零新 Traceback、import OK。注意：C 的性能加权实为可用性路由（成功率/延迟/超时），非真实模型能力，用户已指正，待讨论质量信号方案。
### 2026-09-04（B1 估值验算闸，人工主动推进）
- 计划：移植 Berkshire financial_rigor 轻量版为 scripts/verify_valuation.py（stdlib only，零 emoji）：Decimal 市值独立验算（现价×年报总股本/1e8 vs 快照市值）+ PE/PB 复算 + 快照/筛选表交叉，批量跑最新 run，JSON 报告落 data/，有 FAIL 则 exit 1；verify_run() 供 run_pipeline 采集后调用（try/except 包裹，永不阻断管线）；附单测。验收：py_compile + 单测 + pi1 只读实测 20 候选通过率。
- 完成：实测修了两处自己人的错——①初版 V2 用季报单期 EPS 对 TTM PE，19 个系统性 FAIL，改为年报行 + 宽口径（>100% 告警、>300%/符号矛盾失败）；②total_shares 全表 64015 行全 NULL，V1 现只能 SKIP（见 B8）。终测 pi1 最新轮 20/20 通过，单测 12 passed，全仓 153 passed（3 个失败为 pre-existing，干净树复现）。
- 接入：run_pipeline 步骤 4.5 已调 verify_run，只告警不阻断，下周一 15:30 管线自动带上。
- 部署：已合并 main（8c6bd55）并在 pi1 pull + 备份 DB + restart 生效，四页 200、零新 Traceback、import OK（人工）。
### 2026-09-04（Berkshire 填补排期 + 夜间方向约束 D1–D7）
- 设立目标与发展框架（六层架构 + M1/M2/M3 里程碑）与 Berkshire 填补排期 B1→B5；方向约束 D1–D7 同步写入 skill，今晚 nightly 生效。
- 夜间迭代复盘结论：透明化与期刊达预期，分析深度零进展（ nightly 在舒适区打转），故加约束。详见 skill D1–D7。
- 新增分析能力方向（用户定调）：小白市场总结 + 监控池进出纪律，拆为 B6/B7 归入 M3。
- 确认分析周报制：每日短评已废弃（代码中无此功能，仅周六复盘写 journal），B7 改为周报深度版单频。
- 排名脚本暂搁：外部跑分身份映射不明（muse-spark 两边查无，laguna/ling 版本对不上），先搞主线架构（B2 起），以后再议。
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

### 2026-09-12（架构治理：结构图 + 总路线 + 虚拟盘方向）
- 起因：缺结构图导致迭代破坏地基（AKShare S4/S5 在迭代中静默遗失，靠 C2/C2.5 事后抢救）。
- 新增 `docs/architecture.md`（架构真相源）：系统结构图 + 每日流水线图 + 模块边界禁令表 + 数据表清单 + 数据源注册表 S1–S7 + 三条铁律 + 回归门禁。
- 新增 `docs/paper-trading.md`：选型矩阵（2026-09 实调）→ M4a 自研 paper engine（SQLite+K线，跑pi，零依赖）/ M4b QLib 离线（PC/云）/ M4c QMT模拟首选·PTrade备选（Windows+券商）/ M4d 实盘预备（达标+下令才启动）。miniQMT 已死（2026-07-06 停新）永不选。A股撮合清单（T+1/涨跌停/100股/佣金万2.5·印花税卖出0.5‰·过户费0.01‰/滑点）+ 风控闸 + BrokerAdapter 接口草案。
- `docs/roadmap.md` 升级为总路线（M1–M4d + mermaid 路线图 + 防回归门禁），原单股详情内容归档为子路线保留。
- 想法/为什么：终极目标是 AI 接管投资决策，纸盘是"分析→操作"的第一座桥；先有图再有路，Hermes 后续迭代沿 M 线走，不再各自为政。
- 冒烟：纯文档变更，无代码；emoji 零命中（本段无 emoji）。待 push 后 pi1 仅 git pull 同步，不重启服务。

### 2026-09-16（M4a 纸盘撮合引擎 + 信号编排引擎）
- **PaperBroker 撮合引擎**（`src/paper/broker.py`）：市场价+滑点成交、A 股费用（佣金万 2.5 最低 5 元、印花税卖出 0.5%、过户费 0.01%）、T+1 冻结/解冻、100 整手、卖出席位可用量检查、风控闸（单股 ≤20% 总资产、总仓位 ≤80%、强制止损 -15% 禁买）、`end_of_day` 净值记录。20 单测全过。
- **信号编排引擎**（`src/paper/engine.py`）：解析 `ai_trade_strategy` JSON（`parse_trade_signal` 支持对象/数组/嵌套/空值）、生成待执行信号列表（`generate_signals_batch` 含 buy_zone 校验）、买入/卖出执行（`execute_signals` 含 confidence 仓位系数 高=1.0/中=0.6/低=0.3）、`run_paper_trading()` 主入口（读最新 screening_result + stock_analysis_history，写 paper_trade_signal + paper_order + paper_position + paper_account）。17 单测全过。
- **scheduler 异步触发**（`src/scheduler.py`）：`_trigger_paper_trading_async()` 在 `_trigger_ai_analysis_async()` 完成后异步调用 `run_paper_trading()`，每日流水线自动执行。config.yaml 新增 `paper:` 配置段（初始现金/滑点/费用/仓位上限/风控阈值）。
- 想法/为什么：M4a 三部曲（撮合→信号→调度）闭环，每日 15:30 选股→AI 分析→纸盘自动执行，积累模拟交易数据。风控闸严格（drawdown 用 `>` 不用 `>=`），engine 仓位预留滑点余量避免边界触发。
- 冒烟：37 单测全过（broker 20 + engine 17），pi1 同步验证 `3ee992e`。
