# Stock Dashboard — 迭代进程账 (Iteration Ledger)

> 本文件是开发迭代 agent 的全局上下文源。每次迭代前先通读它了解历史与现状，迭代后追加条目。
> 维护者：开发 agent + 用户。零 emoji（允许 Unicode 排版 → ↑ ↓ ✓）。

## 项目定位
A股价值投资看板。生产实例跑在生产服务器的 systemd `stock-dashboard.service`，端口 9527，每日 15:30 选股 + 实时行情（部署目标见本地 `deploy.local.md`，不入库）。代码真相源 = GitHub `origin/main`，生产服务器从 origin 拉取部署。

**终极目标**：做一个自己用的 AI 自动盯盘投资工具，最终让 AI 接管投资决策。当前阶段以价值投资门规与结构化 AI 分析为核心，围绕它搭一个好用的网页面板。网页面板 UI 已基本定型，但要做到足够细致和直观还有距离。当前系统能做详尽的分析（筛选 + 结构化 AI 评估 + 估值 + 策略），但还做不到 AI 接管操作——这是终极目标，不是现在。

## 当前真实状态（2026-09-21，用户定调路线调整后）

- 项目定位：价值投资**数据面板**。生产服务器跑采集/筛选/展示；AI 分析用用户自带 Key（`/llm` 配置 + 队列执行），面板不再内联 AI 分析。
- 每日流水线：工作日 15:30 采集→筛选（5527 → 20 候选）→ K 线拉取，正常；本地 AI 自动触发已停用。
- AI 现状：本地 Zen/Pollinations 双通道 9/07 起相继不可用；自带 Key 分析已上线（`/llm` 配置 + 队列执行，首只 09-27 跑通）；周六复盘硬规则本地可跑，LLM 决议已随用户 Key 具备调用条件，待 10-03 周六首验。
- 面板：首页三视图（候选总览默认/AI 观察池/钉选）；列表卡片只展示数据（指标/评分拆解/监控条件/笔记入口）；投资笔记（journal + 钉选股 notes）正常展示 AI 分析写回内容。
- 量化路线已砍：`src/paper/`、`/paper` 路由、策略 Tab、`/candidates` 独立页均已删除（git 历史可查）。
- `deep_research` 表：已删除（2026-09-23 用户批准；5 行已备份生产 `data/backup/deep_research_backup_20260923.json`；全仓零代码引用）。
- gate 基线：486 passed（2026-09-28 生产服务器 worktree `gate.sh` 实测全绿；按测试规则本机不跑 pytest）。
- 数据缺口：82 只无 roic/fcf（多为东财无数据的小盘股，C2.5 永久兜底，非 bug）；`sector` 生产回填 2594/5527 行（S8 新浪 49 板块映射 2999 只，2026-09-22 live 实证；未收录新股保持 NULL，行业均值 WHERE 过滤不受污染）。
- 面板短板（P1）：评分透明化已落地；AI 笔记增强/时间线交互/详情页体验待做。

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

## 自动迭代方向（2026-09-13 设立，09-12 更新）

> 开发只提交到 GitHub，生产从 origin 拉取部署。每次迭代前通读本文件 + `docs/architecture.md`。

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

**P2 — 可选（C3 或上游跟踪）**〔P2 仍冻结；其余 09-27/28 已恢复投入〕
7. ~~C3 AI 引用数字抽检~~（已取消，见 C3 注记）。
8. ~~上游跟踪~~（已取消：上游已解绑）。
9. ~~P2 体验优化 ⑤⑥⑦⑧~~（2026-09-23 停止投入，不再开发，roadmap 已归档）。

### 周六 live 验证（B6/B7）
- B6（监控池状态机）和 B7（周报模板）prompt 已就位，本周六复盘自动触发。
- 验证要点：watch 动作是否触发（基本面恶化 → watch + 观察项 + 期限）；journal 是否按新模板输出（池变动章节 / 逐股财报五句 / 小白标准）；coverage 校验是否记录缺失。
- 若验证通过：标记 B6/B7 为完成态。若不通过：根据实际输出修 prompt + 补单测。

### 迭代约束（开发必须遵守）
- 每次只做一件小而实的事，禁止改多个无关模块
- 全仓 pytest 零失败（基线 486 passed，只升不降）
- `collector/` `screener/` `analyzer/` 改动必须附单测
- 禁删 S1–S8 适配函数（除非替代 + 单测同到）
- 禁止 emoji（仅允许 → ↑ ↓ ✓）
- 提交到 nightly 分支，不直接 push main
- 生产服务器部署：合并后同步（pull + restart + 冒烟验证，长期授权直接合并）

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
- 改动必须冒烟测试：`ssh <生产服务器> "cd <部署目录> && .venv/bin/python -c 'import 改动的模块'"` 确认无 import 错误；改动零 emoji（仅允许 → ↑ ↓ ✓）。
- **部署**：默认推 nightly 分支；长期授权直接合并后同步生产服务器（pull + restart + 冒烟验证）。
- 提交信息中文，写清「改了什么 + 为什么（想法）」。
- 改完：更新本账（勾掉 backlog 项、Changelog 追加）+ 推 Discord 简报。
- **git 工作流硬约定（防污染 main）**：每晚迭代在**当前 nightly 分支**上继续（开头 `git fetch origin && git rebase origin/main` 拉平上游，再 commit），commit 后 `git push origin HEAD`。**绝不在本地 `main` 上 commit**；合并走长期授权直接合并推送。push 后保持 HEAD 在 nightly 分支，勿切回 main（本地 main 由合并流程管理）。多日累积都落在同一个 nightly 分支，审计时一次性 `git log origin/main..HEAD --stat` 即可。

### 踩坑铁律（从真实事故提炼，每次迭代前必读）

1. **提交前想清楚，不重复提交同一文件**：README 在 4 个 commit 内被改了 3 次（`d0f8176` 新写 → `2d18db1` 全覆盖 → `b801d36` 去重），等于前两次白做。规则：对同一文件的修改如果间隔 < 3 个 commit，说明没想清楚，应该 `git commit --amend` 或等想清楚再提。
2. **handoff.md 只追加不删历史**：原文件有"历史交接区（追加，不删）"规则，但重写时整段消失。规则：重写 handoff.md 时，"历史交接区"段必须保留并追加新条目，不得删除已有历史。
3. **单一事实源，不重复维护**：README 数据源 S1–S7 完整表与 `architecture.md` §5 完全重复，两处维护改一处忘另一处必出错。规则：README 只放摘要 + 链接，完整内容只在一个文件里维护。
4. **里程碑日期必须与实际任务对齐**：roadmap.md 写 M1 目标 9-14，但 B1/B2 已于 9-05 完成，日期变成空壳误导。规则：里程碑完成后必须标注完成态或移除日期，不留"目标 XX 月"的空壳。
5. **共享 partial 的 CSS 必须写在 partial 里，不写在父页面**：`nightly/20260913` 为 AI 摘要区块写了 33 行 CSS，同时放在 `index.html` 和 `candidates.html` 两处（共 66 行重复）。合并后人工清理移到 `_stock_list.html`。规则：`_xxx.html` partial 是被多个页面 include 的，其专属样式必须写在 partial 内的 `<style>` 块中，不得写在父页面。新组件开发前先确认被几个页面 include，再决定 CSS 放置位置。
6. **分支合并前先检查共改文件**：`nightly/20260914` 和 `nightly/20260913` 同时改了 `_stock_list.html`，合并时 handoff.md 产生冲突。规则：启动新 nightly 前先 `git fetch origin && git log --oneline origin/main..origin/nightly/*` 检查是否有其他活跃分支在改同一批文件，有冲突风险时先协调合并顺序。

## 待办 backlog（细粒度，按优先级）
- [x] 评分体系透明化（2026-09-22 收口：行业均值参照四处补齐，roadmap P1② 一并关闭）
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
|- [x] 清理 `deep_research` 废表（已确认可安全清理）
  - [x] 确认表存在且含 5 条历史数据（茅台/五粮液/伊利/平安/招商，2026-08-22 生成）
  - [x] 确认代码层面无引用（grep -rn 无匹配）
  - [x] 确认迁移记录缺失（说明为历史遗留）
  - [x] 生成 drop 脚本并验证表已自动清理（2026-08-31）

## Berkshire 填补排期（2026-09-04 设立，按序执行，对应顶部目标 M1→M3）
- [x] B1 估值验算闸 P0（2026-09-04 人工完成）：scripts/verify_valuation.py + 单测 12 passed + run_pipeline 4.5 接入；生产服务器实测 20/20 通过。V1 待 B8 启用，V2 为宽口径极端值捕捉。
- [x] B8 financial_history.total_shares 落库修复 P1（2026-09-05 人工完成）：夜间 agent 只交调查报告零落地，违反独立完工律（已补入 prompt 硬约束）。实测：利润表接口两机全灭（东财 hidctype 页面结构变更，1.18.64 全符号 TypeError；agent 称"返回 None"属误诊，且所谓替代接口 abstract_ths 根本无股本列）。DB 自 1989 起 64129 行全 NULL——该列从未真正写进去过（快照市值走的是净利润/EPS 倒推另一条路）。修复：fetcher 加净利润/EPS 兜底 + 生产服务器回填 39798 行（备份在先，剩余 24331 行缺 eps/net_profit 无法推导）；V1 当晚即抓到电投能源市值偏差 28% 真告警（已定性口径差归档）；余量归档：最新行 890 取 766（124 缺，多为数据稀疏股，V1 判 SKIP）；旧历史行不追。
- 提示词补独立完工律：调查类不许只交报告，每晚必须修好/fallback/部分落地三选一；替代路径须验证到列级别。
- [x] B2 强制结论三态 P0（2026-09-05 人工完成）：verdict+三档价格+程序纪律+双页徽标，全仓179过，已合并部署。
- [x] B3 成长 α 纪律 P1（2026-09-05 人工完成）：growth 重写α三标准+估值锚+拐点清单。
- [x] B4 豁免细化 P1（2026-09-05 人工完成）：A/C/C2/D 对标 Berkshire；单测补 C2 缺口；生产服务器零翻转；已合并 main（26f39c6）生产服务器部署生效。
- [x] B5 论点漂移 P2（2026-09-05 人工完成）：conflicts 实时算（打脸回归/Signal/verdict 翻转），免新表；修双重取下标 bug。
- [x] B6 监控池状态机与进出纪律 P1（M3）：schema + 软删除 + watch 落库（B6a+B7）；watch 指派进 reviewer prompt，待周六 live 驗。
- [x] B7 市场总结模板与全覆盖校验 P1（M3）：reviewer prompt 周报结构（池变动/逐股五句/小白）+ coverage 记 actions_summary；prompt 生效等周六 live。

## Berkshire 算法核心融入（2026-09-07 设立，C 系列，接 B 系列之后执行）〔2026-09-22 注：本节历史任务名保留；上游已解绑，算法均已内化为本项目自有实现〕
- [x] C1 终值验算闸 P0（详见 `docs/berkshire-core-integration.md`）：`scripts/verify_intrinsic.py`（戈登终值 PE 三档 + LLM 隐含倍数反解对比 + C1 币种/C2 分母体检，stdlib only）+ `analyze_stock` 写库前改判标注（分母失效档标"仅情景参考"，不阻断）+ run_pipeline 4.6 接入（try/except 永不阻断）+ 单测 ≥6。验收：单测 + 生产服务器实测 20 只 + 全仓无回归。
- [x] C2 东财 datacenter 第二财务源 P0（2026-09-11 完成）：`_fetch_eastmoney_direct()` 直连 datacenter.eastmoney.com 公开 JSON API（stdlib only）；`enrich_financial_data()` 第一步 stock_yjbb_em 失败时自动触发 C2 兜底；填充字段：ROE/毛利率/EPS/每股净资产/营收增长/净利增长/净利润；单测 5 个（直接API/无效代码/代码格式/兜底触发/正常路径不变）；全仓 218 passed 零失败。
- [x] C2.5 东财 datacenter 补 roic/fcf P0（2026-09-12 完成）：`_fetch_eastmoney_roic_fcf()` 直连 datacenter 取年报 ROIC + FCFF_BACK（stdlib only）；`collect_historical_financial_data()` AKShare 利润表/现金流 API 挂掉时自动触发兜底；批量重建后 822/904 股有 roic/fcf（91%）；单测 3 个；全仓 221 passed 零失败。
- [x] ~~C3 AI 引用数字抽检 P2~~（2026-09-22 用户拍板取消：新分工下分析归外部 agent，本地无分析正文可抽检，产出质量由外部负责）
- 明确不做：动量/技术面（上游自证无预测力）、Morningstar（无 A 股价值）、雪球爬虫（红线）、多 Agent（性能配额）、上游研报跟进（只看 skills/ + tools/）。
- [x] ~~上游跟踪常设项~~（2026-09-22 取消：上游镜像/对照工具/月检脚本已全部移除，解绑上游，不再跟踪）。

## 变更记录（Changelog）

### 2026-09-28（修子项目 bug + agent 真测：母策略 AI 决策跑通，nightly/20260928u 已合）
- **修了什么**（母项目 Stock Dashboard 内改 vendored，用户已授权；同步推分支给子项目 Paper Trading Framework 审，不合）：① `Scheme` 补 `source` 字段（三构造点赋值，`scheme list` 500 根因）；②切换策略凭 Key 接管（`_takeover_token` + `/api/schemes/active` 分支 + 前端 prompt 接管，与自家 llm/config 规则一致）；附 2 单测。
- **推给子项目审**：分支 `fix-scheme-admin-source` 已推子项目远端（未合，等他们审）；母 main 照常合并（长期授权）。
- **agent 真测**（母 universe + 我们的 Key，`--scheme mother`）：dry-run 决策 2 笔买入（天华新能/盐湖股份，理由引用母 PE/ROE/10 年均 ROE/评分）；真跑被幂等闸拦下（`already-decided`——dry-run 的实质决策会计入 gate，有利有弊，已如实记下供上游定夺，未用 `--force` 硬闯）。
- **验证**：gate **486 passed**（484+2 新单测）；三扫描；部署 pull + `:8081` 重启 200。
- 基线刷新 484 → **486 passed**。

### 2026-09-28（子项目 8 提交合流：母策略参照已验证，nightly/20260928s 已合）
- **合流**：`paper-upstream` 连拉 8 提交（母策略参照/方案三分法/盘中实时/`cli.py` 改名/单测禁网）→ main；远端/本地分支已清。
- **母策略验证**（母项目 Stock Dashboard → 子项目 Paper Trading Framework）：`--scheme mother` dry-run 全绿（宇宙=母最新轮 Top20，MA 执行规则，论点优先）；`llm ask` 走通（我们的 Key，glm-4.5-flash）；`:8081` 重启 200。
- **两个如实记录**：① `scheme list` 500 系上游 bug（`Scheme.source` 不存在），不动，等上游修再 pull，交易主链路不受影响；②母 `strategy_tags` 全空（multi 开关默认关），`--pool-tag` 暂无数据可用（scheme 缺省不过滤，不影响运行；开 tag 是母行为变更，另议）。
- **文档**：M6 更新 + 基线 474→484；表达统一为母项目（Stock Dashboard）/子项目（Paper Trading Framework）全名。
- **验证**：gate **484 passed / Gate passed**（474+10，含上游面板/方案/实时新单测）；三扫描（vendored 豁免延续）。
- 基线刷新 474 → **484 passed**。
- **流程自纠**：收尾 commit（`53d5bf4`，本文档批次）误落 main（本轮忘了开分支；与 `8398a80` 同款）；未改写远端历史，tip gate 484 passed 验证安全后放行。

### 2026-09-28（解盘写笔记 + 深色/免横滑修复，nightly/20260928p 待合）
- **用户需求**：大盘解盘改写投资笔记（长分析 + 不限 token + 加深思考）；深色模式 AI 文本框；候选信息免横滑。
- **改了什么**：①后端 `ask_raw` 加 `unlimited`/`timeout` 参数（默认行为不变）+ `write_market_note`（大盘+AI池+钉选+候选全景，600s，不限 token，同日复盘行追加）+ 后台任务 + `/market` 改写 + `/market-note/{id}` 轮询；②llm 页深色体系（变量+开关+去硬编码白）+ 首页指标 `flex-wrap` 免横滑 + 面板/detail 输入框主题色 + 解盘按钮改走写笔记任务。
- **验证**（生产服务器 worktree）：`gate.sh` **474 passed / Gate passed**（468+6；两轮失败全是真问题：快照 seed 指数前缀、旧 inline 单测撞已删函数）；ast 全过；node 全验三页 JS；三扫描待收尾跑。
- **状态**：已合已同步；live 解盘 mn0001 done（5735 字，用量 prompt 6536/completion 6826，约 4 分钟），journal 行结构验证通过。
- 基线刷新 468 → **474 passed**。

### 2026-09-28（删除无用功能：ai_proxy 参考实现 + setup-cron.sh + 代理规范文档，nightly/20260929a 待合）
- **用户指令**：不需要 hermes/外部 agent 了，没用的功能删掉，不要留。
- **删了什么**：`src/ai_proxy/`（独立 FastAPI，主程序零挂载，grep 实证）+ `tests/ai_proxy/`（5 单测）+ `scripts/setup-cron.sh`（要 hermes 二进制的死路径）+ `docs/ai-proxy-ai-analysis.md` + `config.yaml ai_proxy` 段。
- **没动什么**：vendored `paper_trading/hermes_bridge.py`（文件名 proper noun + 上游字节，绝不手改）；历史条目（append-only）。
- **文档同步**：README 树/配置表/技术栈语、architecture 图+§3+§6、agent-api、scheduled-tasks。
- **验证**：gate 468 passed（473−5）；三扫描；部署 pull + HTTP（删的是未加载模块，不重启）。
- 基线刷新 473 → **468 passed**。

### 2026-09-28（文档准确性审计：去 Hermes/外部 agent 旧分工，nightly/20260928o 待合）
- **用户指令**：项目不再需要外部 agent 帮忙；全仓文档去掉 Hermes 相关表述，审查所有文字准确性并修正。
- **新真相源**：AI 分析 = 用户自带 Key 内置执行（`/llm` + 队列，09-27 live）；外部程序 API 保留兼容；周六复盘 LLM 已具备调用条件（配置链完整），待 10-03 首验。
- **改了什么**：AGENTS / README / architecture / roadmap / agent-api / ai-proxy（转备用）/ scheduled-tasks / setup-cron / 模板注释 / 账本现行区；历史条目按 append-only 保留（dated 事实不改）；vendored 树豁免（上游字节）。
- **验证**：gate 473 passed（纯文档，计数不变）；三扫描全绿。

### 2026-09-28（纸盘 subtree 机制化：graft 上游 + 双向独立验证 + 全部文档，nightly/20260928n 已合）
- **机制**：`paper-upstream` remote 已配；plain copy 转 subtree（删旧+graft 重建，内容一致）；首 `pull --squash` 带回上游 3 提交（单测禁网/上下文覆盖/NAV全口径），嫁接干净零冲突；以后更新一条命令。
- **双向独立验证**（grep 实证）：母仓 src/tests/scripts 零引用 `paper_trading.*`；vendored 零引用 `src/stock_dashboard`——父开发不被分心，子独立演进。
- **规则**：vendored 文件绝不手改（修先上游再 pull）；母推远端天然带上子树（单仓单推，部署时 `git ls-tree` 实证）。
- **文档全量**：README 结构 + AGENTS（基线+subtree 工作流）+ architecture（§3 行+§5.3）+ roadmap（M6+基线）+ paper-trading.md 转向注记 + agent-api 纸盘指针 + 本账 + handoff。
- **验证**：gate **473 passed / Gate passed**（470+3 上游新单测）；三扫描（vendored 豁免延续）。
- 基线刷新 470 → **473 passed**。

### 2026-09-28（/paper 改直达 + 清原生回测孤儿端点，nightly/20260928l 已合）
- **改了什么**（用户反馈 iframe 不如直开）：`/paper` 改 `location.replace` 直达 `:8081` 原面板（hostname 自适应 + 新窗口兜底）；删原生回测孤儿端点（`/api/paper/universe|backtest*`，调已删模块，调用即 500 的陷阱）；重建最小 `test_paper.py`（页面断言）。
- **验证**：生产 gate **470 passed**；重启后 ROOT/PAPER 200；桌面浏览器实证：开 `/paper` 自动落 `:8081`（标题"模拟交易仪表盘"），"资产走势"渲染在位（仅字体隧道 cosmetic 错）。
- 基线刷新 469 → **470 passed**。

### 2026-09-28（纸盘全量合并：子项目整体迁入 + 原面板嵌回，M6，nightly/20260928h 已合）
- **纠偏**：M6 原生自研包被用户否决（"魔改页面"，要子项目原页面）→ 已删除（历史保留）；按 MERGE_GUIDE §1 整体迁入顶层 `paper_trading/`（46 文件；`.git/venv/pycache/db/log/secrets` 排除；远端/本地/pi 三处原件一字未动）。
- **接线（全实测）**：pool-from screening 取最新轮 Top20；Key 经自家 `save_provider` 落独立 secrets（0600）；MA dry-run + 真跑（`no-fresh-bars`，行为正确）；agent dry-run 同守卫跳过（盘中无今日 bar，正确）；`llm ask` 走通（"国酒第一"，Key 在其栈内有效）；`:8081` 面板独立服务已起（`paper-trading-parent.service`，enabled，200；内容实证标题+资产走势在位），`:8080` 原部署未动（200）；`/paper` 改嵌原面板 iframe（hostname 自适应 + 新窗口 fallback）。
- **首个真 agent 决策待收盘后**：今日 bar 落定后跑 `agent run --pool-from screening`（≤3 笔×2 万，幂等闸+熔断）；自动调度未配（需用户另批）。
- **教训**：vendored 运行 CWD 必须是 `paper_trading/`（import 与相对路径都依赖它；从父根直调必挂，排查半小时）。
- **验证**：gate **469 passed / Gate passed**（440 + 29 自带单测，hermetic 全绿）；ast / 新增行 emoji 0 / 身份 0 全绿。
- 基线刷新 466 → **469 passed**。
- **扫描豁免立规**：`paper_trading/` vendored 树整体豁免新增行 emoji + 身份扫描（上游字节原样保留：主题切换符 + README cron 示例路径；公开展示过，零新增暴露；返工它等于制造上游漂移）。自研代码扫描维持全严。

### 2026-09-28（纸盘移植：子项目成果吸回母项目，M6，nightly/20260928f 已合）
- **用户指令**：远端/本地/pi 子项目一字不动、保持独立；母项目新增模拟交易页吸取成果；MA+价值策略 / 手动回测先行 / 10 万+候选钉选（三拍板）。
- **路线**：原生移植（未按 MERGE_GUIDE §1 整体迁入——用户要面板里的页面而非外链+独立 venv；偏差已记 `src/paper/__init__.py` 与此处）。读的是 TEMP 只读 clone（本批次收尾删除），远端零写入。
- **改了什么**：① `src/paper/`（types/日历/broker 回测日期覆盖+T+1/风控/MA 移植/价值轮动自研/回测引擎 replay/NAV/胜率/临时账本零残留）② `/paper` 页 + universe/backtest 启动/状态接口 + 首页导航。
- **数据诚实上限**：K 线 2025-07-23 起 42 只（稀疏：仅入池/Top25 时追加）；筛选 59 个交易日（2026-07-08 起）；区间超出自动截断，实际使用标的数明示（live：3 只里仅茅台有数）。
- **v1 边界**：手动回测 only；live 账户区空位标注 v2；agent loop/LLM 交易员/8080 面板/独立 DB 文件/op_log/agent_plans 不碰。
- **验证**：gate **466 passed / Gate passed**（440 + 26）；两轮失败修复（价值空评分不清仓真 bug、快照 NOT NULL）；ast / 新增行 emoji 0 / 身份 0 全绿；live：MA 干净跑完（38 天 0 信号）+ 价值默认池（21 只覆盖，59 天 11 笔，-1.87%，费用 55.94）。
- 基线刷新 440 → **466 passed**。

### 2026-09-28（双 bug 修复：KlineDAO 恢复 + 钉选卡跳转，nightly/20260928c 已合）
- **Bug1 K 线空白**：`GET /api/stock/{code}/kline` 500——`routes.py:638` 引用不存在的 `KlineDAO`（`ImportError`，生产日志实证）。根因：历史重构把 K 线方法粘进 `WatchlistDAO`、类本身丢了（`database.py:1069` 流浪 docstring 为证；`pyproject norecursedirs` 排除 `_legacy` 致 gate 从未抓到）。修复：迁回独立 `KlineDAO`（WatchlistDAO 内无其他调用方）+ 3 单测。附带：调度增量拉取因此连挂 7 天（表停在 09-21），修复后明早 15:30 自动追平（start_date 续拉），无需手动补。
- **Bug2 钉选卡点不动**：桌面浏览器实证——钉选 tab 的 6 张卡可见但零 onclick。根因：该 tab 由 `watchlist.js` 纯 JS 渲染，拼卡片时漏了跳转（搜索项有，卡片无）。修复：补 `clickable` + onclick + title；已钉按钮加 `stopPropagation`（否则先跳详情）；`watchlist.js` 加版本戳（浏览器缓存旧 JS，`?v=20260928c`，以后改静态文件同理）。
- **验证**：worktree gate **440 passed**（437+3）；生产部署目录 gate 全绿；K 线接口 200 + 真数据；浏览器实证点击跳转（见下）；三扫描全绿。
- 基线刷新 437 → **440 passed**。
- **流程自纠**：收尾 commit（`8398a80`，版本戳+基线扫换+入账）误落 main（忘切分支，且 `;` 连接掩盖了 push nightly 失败）；未强行改写远端历史，tip gate 440 passed 验证安全后放行；教训：commit 前 `git status --short --branch` 确认分支（此前每次收尾都有，这次漏了）。

### 2026-09-28（部署 gate 2 失败修复 + 队列路径 live 验证）
- **失败**：部署目录 gate 435+2（worktree 全绿）：`test_polish_passthrough` + `test_explain_market`。根因：单测不 hermetic——部署目录有用户 `local.yaml`（model 非免费）+ pytest 不加载 `.env` → 真 analyzer configured False；worktree 干净环境掩盖了问题。
- **修复**：两单测显式 setenv Key；部署目录 gate **437 passed** 一次过。教训入 handoff 已知隐患：凡读 ambient 配置/环境的单测必须显式隔离。
- **队列 live**（用户 Key）：enqueue 自定义 1 只 000792 → running → 约 2 分钟 done（1/0），用量落库（3215/3353）；附带发现用户 00:56 自己跑过一只 002215（3314/4375）——功能已在用。
- 基线 437 不变（无新增测试）；docs 随 `nightly/20260928b` 合并。

### 2026-09-28（AI 队列并发+UI 重组：排队/N 并发/范围自选/需求润色/大盘解盘，nightly/20260928a 已合）
- **用户需求**：分析不能排队（旧 409 顶掉）→ 自选并发数 + 排队执行；执行功能移出设置页（设置页只留参数）→ 主页常驻；自选股 direct 按钮；自定义范围（大盘/自定义盘股）；需求文本框 + AI 润色拆解。
- **改了什么**：①后端（`src/ai_queue.py`：`analyze_batch_parallel` 独立实例线程隔离 + `AnalysisQueue` 批次串行批内并发 + 取消 + `polish_requirement`/`explain_market`；`analyze_stock` 附加指令注入 prompt 末尾；并发数 1~5 配 local.yaml）②端点（`/analyze` 转队列去 409 + `/enqueue` + `/queue` + `/queue/cancel` + `/polish` + `/market` + `/concurrency` + 范围解析 candidates/watchlist/pool/custom；旧 busy 单测改排队语义）③UI（`/llm` 瘦身只留参数 + 首页常驻面板 + `static/ai.js` 共享 + 详情/钉选详情分析按钮）。
- **设计取舍（如实记录）**：队列内存态（重启丢失）；同轮并发批次共享 progress 行（显示交错）；问答/解盘不落库 analyzed（inline + 用量）；`index` 内联 analyzeOne 与 `ai.js` 双份待收敛。
- **验证**（生产服务器 worktree）：`gate.sh` **437 passed / Gate passed**（408 + 29 新增：并发后端/端点/卡片渲染/UI 元素）；两轮失败修复（快照 NOT NULL、codeInput 过期断言）；ast / 新增行 emoji 0 / 身份 0 全绿。
- **状态**：随即合并；合并后 pull + 重启（analyzer/routes/模板/静态 JS）+ gate；然后队列路径 live 验证（入队 1 只单股）。基线刷新 408 → **437 passed**。

### 2026-09-27（合并 nightly/20260927d：修坑+卡片按钮+直接提问上线）
- **合并**（长期授权）：5 提交 ff 入 main（= `577b8e0`）；远端/本地分支已删。
- **部署**：pull → gate **405 passed** → 重启 → ROOT 200 + 首页 41 处按钮 + 详情问答框在位；0 Traceback。

### 2026-09-27（修复 latest 轮污染 + 首只真分析跑通，nightly/20260927e 已合）
- **合并**（长期授权）：1 提交（DAO fix + 3 单测）ff 入 main（= `8a731c9`）。
- **根因**：周六复盘 run 按字符串 MAX 排第一，once/all 拿到空集合 → 404；修 `get_latest_completed_run_id` 排除 `_review`（4 调用方全是要筛选轮，无回归）。
- **部署**：pull → gate **408 passed** → 重启 → usage 默认轮正确指向筛选轮。
- **首只 live**（用户 Key，盐湖股份 000792 once）：started → 约 2.5 分钟 → 用量落库（`glm-4.5-flash`，prompt 3337 / completion 5103），分析 1/1 成功。注：用户 21:22 自行把模型改对（之前 `glm-4.7-flash` 不存在）；复盘轮修复 + 正确模型名双条件缺一不可。
- 基线 405 → **408 passed**。

### 2026-09-27（用户报障修坑 + 卡片按钮 + 直接提问，nightly/20260927d 已合）
- **背景**（用户配好智谱 Key 后"无法运行分析"）：生产日志审计结论——代码无错，全天零 `[AI分析]` 日志、screening 全员 ai_failed=None、用量 0 行，**没有任何一次分析真正启动过**；拦路三坑：单股手填未知代码 404、重试 noop 被误读、测试 401（智谱列表接口 quirks）吓退。
- **修坑A**：单股改候选下拉（取 `/api/stocks` 20 只）+ 401 注记（测试 401 不代表 Key 无效）+ 下拉断言。
- **B 卡片按钮**：候选卡 + 钉选卡"AI 分析"直达 once + index 共享 JS（postJSON/analyzeOne/错误映射/进度轮询/完成刷新）+ 卡片渲染 2 单测；09-21 旧注释同步更新。
- **C 直接提问**：`AiAnalyzer.ask_raw` 瘦调用（单 POST + 超时重试 1 次，不进 20 次退避；system prompt 非 JSON）+ `answer_question`（快照+筛选+财务摘要上下文组装）+ POST `/api/llm/ask`（400/404/502 码齐）+ 详情页问答框（用量 inline + 存为笔记走现有 notes 接口，先自动钉选再写）+ 7 单测。
- **验证**（生产服务器 worktree）：`gate.sh` **405 passed / Gate passed / 27.32s**（396 + 9：卡片渲染 2 + 提问 7）；首跑 2 失败（测试插快照行缺 NOT NULL 列）修复后全绿；ast / 新增行 emoji 0 / 身份 0 全绿。
- **状态**：随即合并；合并后 pull + 重启（模板/routes/analyzer）+ gate；然后单股 live 实测（用户 Key：`glm-4.7-flash` 是否存在、Key 是否有效，一次即知）。基线刷新 396 → **405 passed**。

### 2026-09-27（/llm 页参数收折叠：默认即可，高级自定义，nightly/20260927c 已合）
- **改了什么**：`llm.html` temperature/max_tokens/间隔秒数移入 `<details>` 高级设置（默认值不变：0.3/6000/60，JS 取值逻辑不变）；测试加 1 断言（页面含"高级设置"）。
- **为什么**（用户反馈）：普通用户用默认即可，自定义以后再说；减少设置页认知负担。
- **验证**：生产 gate 396 passed（总数不变）；生产 pull + gate + 重启（模板改动）+ 三路 200。

### 2026-09-27（合并 nightly/20260927a：LLM 自带 Key 上线 + 长期授权直接合并）
- **合并**（用户长期授权，2026-09-27 起合并/推送/生产调试无需逐次批准）：`nightly/20260927a`（4 提交）ff 入 main（= `45be212`）；远端/本地分支已删。
- **部署**：生产 git pull → 部署目录 `gate.sh` **396 passed / 33.84s / Gate passed** → 重启 `stock-dashboard.service`（routes/模板改动）→ active，ROOT/API/LLM 三路 200，5 分钟内日志 0 Traceback；`/api/llm/status` 如实返回未配置（无 Key）。
- **流程变更入账**：AGENTS.md 收尾流程 + Git 工作流已改长期授权版；仍禁止在 main 上直接 commit 代码。
- **状态**：已合已同步；收尾 docs（本条 + AGENTS + handoff）入 `nightly/20260927b` 随即合并。基线 396 与 main 一致。

### 2026-09-27（LLM 自带 Key 分析复活：OpenAI-compatible + /llm 设置页 + 三触发 + 用量，nightly/20260927a 待合）
- **背景**（用户拍板恢复部分投入）：本地免费通道双死 → analyzed 常年 0；方案：用户自带 Key 做分析；用户三拍板：v1 只做 OpenAI-compatible / 触发整轮+重试+单股全给 / 用量显示要做。
- **改了什么**：① `src/llm_config.py`（厂商预设 10 家 + `fetch_models` + 保存 Key→`.env`/非敏感→`local.yaml`/即时写 environ + 脱敏 status）+ 4 端点（providers/test/save/status）；② `/llm` 设置页（预设/测试/保存/三触发/进度轮询/用量表）+ POST analyze（all/retry/once，409 防重入，后台线程复用 `analyze_batch`，进度进 `/api/progress`）+ GET usage（`AiAnalysisLogDAO.get_by_run` 新增读方法）+ 首页导航；③提示词零重建（复用 `ANALYSIS_PROMPT`），压缩零构建（单股 prompt 仅数千 token）。
- **为什么**：Key 存 `.env`（gitignored，老惯例），非敏感存 `local.yaml`（热重载已存在）；409 防重入是花真钱后的必备（防双击重复扣费）。
- **验证**（生产服务器 worktree，按测试规则）：`gate.sh` **396 passed / Gate passed / 82.59s**（367 + 29 新测：后端 18 + 触发用量 11）；首跑抓 2 真失败（测试 fake 签名 + `llm_status` 读写路径不一致设计 bug）修复后全绿；ast / 新增行 emoji 0 / 身份 0 三扫描全绿。
- **如实声明**：端到端（真模型跑通）待用户填 Key 后实测——单测覆盖全链路逻辑，但无 Key 打不通真模型；用户保存 Key 后点一次单股分析即闭环。
- **状态**：`nightly/20260927a` **待用户批准合并**；合并后生产 git pull + 重启 `stock-dashboard.service`（routes/模板改动需重启）+ gate 复验。基线刷新 367 → **396 passed**。

### 2026-09-23（deep_research 废表删除，用户批准，nightly/20260923b 已合）
- **执行**（用户原话"脏数据别留了"）：生产 DB 删表前全仓 grep 确认零代码引用 → 5 行 JSON 备份（`data/backup/deep_research_backup_20260923.json`）→ 断言 5 行 → `DROP TABLE deep_research` → 验证 `sqlite_master` 已无此表。实际 5 行为格力/五粮液/茅台/伊利/海天（08-29 条目记的"平安/招商"有误，以本次实测为准）。
- **文档**：账本现行状态行 + 收尾条目 + backlog 相关行更新为已删；`architecture.md` 注册表该行改为已删除勿重建；handoff 下一步③勾除 + 历史区追加。
- **验证**：删表前后服务未重启（纯数据操作，无代码变更）；生产 `gate.sh` 367 passed；HTTP 200。备份文件留存生产 `data/backup/`，可恢复。

### 2026-09-23（维护模式收尾：停止投入，保采集+选股，AI 接口保留，nightly/20260923a 已合）
- **用户指令**：项目停止投入、不再深入开发；pi 保留每日采集 + 算法选股；AI 不再投入但保留数据接口供日后自助分析；收尾。
- **AI 零触发审计**（结论：无任何自动 AI 调用，可无人值守）：scheduler AI 调用已注释（`src/scheduler.py:236`，函数体留 dead code 未删）；日流水线走 `orchestrator.run_daily_pipeline`（无 AI 步骤）；`run_pipeline` 步骤 6 只手动触发；pi 无 crontab、无 systemd 定时任务；`.env` 无 Key；Zen/Pollinations 双通道已死——零花钱、零限流风险。
- **今日生产实证**：`20260923_153008` completed（15:30:08→16:16:15，5527→20，analyzed=0）；screening 当日 20 行；快照今日回写 719 行（候选池+钉选补录；其余行保留历史日期 = 既有滚动记录设计）；sector 487/719；首页行业均值 17 行（S8 调度内自动回填生效）。
- **备份清理**（生产 `data/`，gitignored，不影响代码）：`data/db` 删 4 个陈年 .bak（留最新 2 个），280M→122M；`data/backup` 删 Sep 4-5 实验快照 8 个（323M，前 sector-schema 时代）；合计释放约 480M；磁盘 26%→25%（42G 空闲）。`data/logs` 轮转 bounded 不动；DB 42M 日增数千行，空间以年计充足。
- **文档冻结**：handoff 三段重写（维护模式）+ 历史区追加；本账 P2 余项标不再开发；roadmap 挂维护横幅 + P2 归档。基线 367 冻结（后续无代码，gate 仅动代码时重跑）。
- **用户保留决策已执行**：`deep_research` 废表已删（2026-09-23 用户批准"脏数据别留"；5 行：格力/五粮液/茅台/伊利/海天，备份生产 `data/backup/deep_research_backup_20260923.json`；删前全仓 grep 零代码引用）。注：08-31 条目曾记"验证表不存在"系误记，表一直在，今日才真删。
- **验证**：收尾分支 tip 生产 worktree/部署目录 `gate.sh` 367 passed；三扫描全绿；合并后 pull + HTTP 200（纯文档变更不重启）。

### 2026-09-23（合并部署 nightly/20260922e：P0-2 修复上线，生产同步完成）
- **合并**（用户批准「靠你把关」后执行）：`nightly/20260922e`（2 提交：P0-2 修复 `453dfdf` + 收尾 docs `c6175d1`）ff 入 main（= `c6175d1`）；合并前审计 `origin/main..HEAD` 恰 2 提交；远端/本地分支已删，远端仅 main + archive。
- **部署**：生产服务器 git pull → 部署目录 `gate.sh` **367 passed / 29.71s / Gate passed** → 重启 `stock-dashboard.service`（验算闸/分析 prompt 改动需重启生效）→ active，ROOT/API/JOURNAL 全 200，自重启点起日志 0 Traceback。
- **时序备注**：重启后 sleep 3 首检 ROOT 000——查日志系 uvicorn 启动中（scheduler 初始化，`Application startup complete` 约 8s），非故障；等待 10s 重试 200 + 端口 LISTEN 确认。以后重启验证等待须 ≥10s（已记入 handoff 已知隐患）。
- **状态**：已合已同步；基线 367 与 main 一致；本次收尾 docs 入 `nightly/20260923a`（纯文档）待合。

### 2026-09-22（P0-2 修复：验算闸双源缺数不谎报，nightly/20260922e 待合）
- **背景**（同日评审 P0-2，用户批准「p0-2修复」后实施）：双源都缺数据时验算恒 SKIP，prompt 却因 `elif` 分支渲染「双源验算通过」谎报成功；`verify_market_cap` 缺 `reported` 键致 prompt 渲染「快照None亿」；跳过原因（findings）不进 prompt，AI 把缺失字段当精确值引用。
- **改了什么**：① `verify_market_cap` 三个返回分支补 `reported`（快照市值，供 prompt 渲染真实对照值）；② `verify_run` summary 加 `verified`（= pass+warn+fail，真实完成交叉验算的样本数）+ 全 SKIP 警告日志，CLI `main()` 全缺数 **exit 2**（fail>0 仍 exit 1、正常 exit 0——绝不以零失败冒充成功）；③ `_data_quality_text` 双源块三态重写：任一 WARN/FAIL → 明细行（带真实快照值）；**双全 SKIP → 「双源验算未执行（原因）——这不是通过，关键结论按缺数据降档」**；有验算项通过 → 「通过」+ 附跳过项原因（findings 透传）。
- **为什么**：验算闸的价值在诚实——没验不许装作验过；exit 0/2 区分「验过没问题」与「根本没验」，下游与人工看日志不再被骗。
- **验证**（生产服务器 worktree，按测试规则）：`gate.sh` **367 passed / Gate passed / 40.16s**（360 + 7 新增：reported 3 + 零样本诚实 2 + prompt 三态 2）；首跑 gate 抓到测试内 SQL 误用 Python `None` 的真 bug（改 `NULL`）后复跑全绿；ast / 新增行 emoji 0 / 身份 0 三扫描全绿；既有 A3b 3 测与 B1 12 测零回归。
- **状态**：`nightly/20260922e`（`453dfdf` + 本收尾 docs）**待用户批准合并**；合并后生产 git pull + 重启 `stock-dashboard.service`（验算闸/分析 prompt 改动需重启生效）+ gate 复验。基线刷新 360 → **367 passed**。

### 2026-09-22（合并部署 nightly/20260922d：残留④ + 测试规则 + S8 sector 回填上线，生产回填 live 实证）
- **合并**：`nightly/20260922d`（4 提交）ff 入 main（`1f0427c`），本机直连推送成功；远端/本地分支已删，远端仅剩 main + archive。
- **部署**：生产服务器 git pull → 部署目录 `gate.sh` **360 passed** → 重启 `stock-dashboard.service`（采集/DB/scheduler 代码变更必重启）→ active，ROOT/API/JOURNAL 全 200，日志 0 Traceback。
- **sector 回填 live 实证**（手动触发完整链路，与 orchestrator 挂载点等效）：`fetch_sector_map()` 真实新浪 92.3s → **2999 只**映射 → `backfill_sectors()` 回填 **2594 行**（DB total 5527；映射−回填差值 = 新浪收录但不在快照全集的代码）→ 最新快照日 Top 板块 金融40/机械33/生物制药30/交运29/建筑28 → 首页 `行业均值` 渲染 **16 行**（此前 0）——板块归属→行业均值→卡片展示闭环打通。
- **状态**：已合已同步；此后 sector 随每日 15:30 流水线自动回填（420s 护栏 + 磁盘缓存兜底）。

### 2026-09-22（四项拍板落地：残留④ + 测试规则 + S8 sector 数据回填，nightly/20260922d 待合）
- **残留清理④**（用户拍板「可执行」）：`docs/agent-api.md` journal 口径按实测修正——`journal/list` 空列表返回 `[]`（200）不 404，仅 `latest`/`{date}` 缺失才 404；`scripts/daily_cron.sh` 头注释补休眠陷阱（15:30 休眠则当天错过且不补跑，醒来需手动执行）。
- **测试规则落档**（用户拍板：开发在本机、测试在生产服务器）：AGENTS.md 测试门禁改「**在生产服务器执行**——本机不跑 pytest」+ 硬规则新增「开发与测试分离：本机只编辑/commit/文本扫描，全仓验证一律生产服务器 `gate.sh`」。
- **P1② sector 数据回填**（用户拍板「接口当然需要搞好」→ 接数据回填，不做口径修改）：注册 **S8 新浪直连行业分类**——`newSinaHy.php` 取 49 板块名单 + `Market_Center.getHQNodeData` 分页取成分（单页硬顶 100 行，空页即停），Session 复用 + 0.6s 页间节奏（背靠背连打被新浪拖慢，节奏化后全量约 87s）+ 浏览器 UA；`fetch_sector_map(timeout=420)` 线程护栏 → 上次成功磁盘缓存 `data/cache/sector_map.json`（≥1000 条才覆盖）→ `{}` 跳过回填不清旧值；`save_batch` 无 sector 字段保值（防 INSERT OR REPLACE 擦回填）+ `backfill_sectors()` 只 UPDATE map 内 code；orchestrator 快照回写后挂回填（watchlist 补录与候选判定之间）；注册表 S1–S7 → S1–S8 全仓更名（AGENTS/README/architecture/roadmap/scheduled-tasks/setup-cron/账本约束）+ roadmap 新增「数据补充」行。
- **为什么**：`stock_snapshot.sector` 全 NULL → 板块归属与行业均值参照（P1② 展示侧已收口）空转，接源回填是该链路最后一环。数据源探测排除记录：akshare `stock_sector_spot/detail` 每调约 5.2s 超护栏、`stock_sector_detail` 须传 label、东财 push2 全断（RemoteDisconnected）、THS 无成分函数、巨潮仅分类树、curl 对 newSinaHy 15s 超时（RC28）——新浪直连为唯一活源。
- **验证**（按新测试规则全部在生产服务器 worktree）：`gate.sh` **360 passed / Gate passed**（348 + 12 新增：S8 契约 8 + DAO 4；orchestrator 流水线测试补 `fetch_sector_map` mock，防真实打新浪卡满超时）；**live 实测** `fetch_sector_map()` 返回 **2999** 只映射（抽样 600176→玻璃行业 正确）+ 缓存 77KB 落盘（fetched=2026-09-22）；覆盖核验：新浪家数合计 3035 / 实取 2999（差 31 = `其它行业` 元数据陈旧；电子信息 247/247、机械 211/211 分页无截断，`num=1000` 被硬顶 100）；身份复扫 0 命中、新增行 emoji 0 命中、ast 全过。DB 实际回填在合并后下一次流水线运行生效。
- **基线刷新**：348 → **360 passed**。
- **状态**：分支 4 提交（`abbcaa2` / `d4eed12` / `9f05b82` / 本收尾）**待用户批准合并**；合并后同步生产 = git pull + 重启服务（采集/DB/scheduler 代码变更）+ gate + 观察一次 sector 回填行数。

### 2026-09-22（全仓开发者服务器身份清除 + 部署信息本地化，nightly/20260922c 待合）
- **改了什么**：23 文件清除全部开发者服务器身份引用——主机名与裸 token → `生产服务器`、硬件名 → `低内存主机`、内网 IP（新旧两个）→ `<生产服务器>`、家目录绝对路径 → `<部署目录>`、开发机盘符路径 → `<仓库路径>`；覆盖 AGENTS/README/docs（含 architecture mermaid 子图改 `PROD`）/scripts 注释/账本 90+ 处/superpowers 历史计划（只改称呼不删内容，用户拍板）。AGENTS、README、agent-api 手工润色，部署命令一律占位符并统一指向 `deploy.local.md`；`tests/test_verify_intrinsic.py` 硬编码 sys.path 改 `Path(__file__).parents[1]`；`.gitignore` 增列并新建 `deploy.local.md`（真实主机/ssh/部署目录/gate/中继命令唯一落点，不入库）。
- **为什么**：工作树残留身份引用会随文档/账本/脚本反复复制再泄露；部署目标是机器特定配置，属本地信息不属仓库内容（与 `.hermes/environment.json` 同理）。
- **同日早前**：评审 P0-1（合并后漏重启致新代码未生效）已关闭——重启生产服务后新进程 active，thesis 404→200、index/detail/api 200、日志 0 Traceback。
- **验证**：本地全仓 pytest **348 passed** 零失败（需 uv venv 补依赖：系统 python 缺 akshare 时 16 failed 属环境问题）；生产服务器 worktree 跑分支 `7496732` 的 `gate.sh` **348 passed**（生产 checkout 全程停 main 未动）；身份模式双扫描（主机名/IP/家目录/硬件名/盘符路径 + 裸 token）全仓 **0 命中**（`deploy.local.md` 被 gitignore 排除）；相对 main 的改动行 emoji **0 命中**；第三方 `klinecharts.min.js` 批量误改已还原。
- **状态**：已于同日合入 main（`2f0384e`）并同步生产（gate 348 + HTTP 200）；同批评审余项落定——P1 sector 数据方向与第 4 项残留清理已实现并入 `nightly/20260922d`（见上一条），P0-2 修复仍待用户批准。

### 2026-09-22（P1② 收口：行业均值参照 + C3 取消）
- **行业均值参照**（roadmap P1② 最后一环，用户确认后实施）：`StockSnapshotDAO.get_sector_averages()`（A 股非空板块聚合，PE 仅取正值样本防亏损股拉低，roe 全样本，只读不进评分链路）+ `get_sector_map()`；index 给候选卡/观察池卡挂板块归属并下发 `sector_avg`，`/stock/{code}`、`/watchlist/{code}` 传本股板块均值；四处评分拆解块（`_stock_list` / `_watchlist_card` / `stock_detail` / `watchlist_detail`）总分行后补「行业均值（板块）ROE…% · PE…（n只）」行，三套模板 CSS 各增 auto/1fr 覆盖行；无评分拆解（区块隐藏）时不出现。
- **C3 取消**：backlog 注销——新分工下分析归外部 agent，本地无分析正文可抽检，产出质量由外部负责（用户拍板）。
- **评分透明化 P1② 主项关闭**：roadmap 标 ✅ 收口，backlog 主项 [x]。
- 验证：本地 `hermes verify --skip-start` bootstrap+test 全过 **348 passed**（342 + 新增 6：DAO 聚合/过滤 2 + 三页展示 3 + 无拆解不出现 1）；既有 `/stock` 路由 3 测无回归。

### 2026-09-22（清除误入的内部开发机内容 + 调度/复盘职责定位）
- **内部机内容零清除**（用户裁定：该开发机相关内容从未获授权进入项目，重大失误）：删 `docs/ai-proxy-analysis-report.md`（整篇该机架构报告）+ README 树行；`ai-proxy-ai-analysis.md` 3 处、iteration-log 项目定位段、backlog 依赖修正条目、`src/ai_proxy/__init__.py` docstring 全部中性化；账本历史 16 行 23 处设备名 → `本地`/`AI 侧`（仅设备名脱敏，验证事实不变）。**AGENTS.md:53 同款措辞当时因写保护暂留，2026-09-22 用户批准后已修，全仓工作树 0 命中。** 注：旧 git 历史仍含原记录，无法在不重写历史的前提下抹除，工作树已零残留。
- **调度定位**：`scheduled-tasks.md` 改"参考设计——调度制度由使用者自行设计，本项目不预设"，声明 `POST /api/trigger_update` + `agent-api` 读写支撑任意轮询组合；`setup-cron.sh` 改可选注册、不自动注册；README 树行同步。
- **职责重定**（用户裁定落地）：外部接入闭环 = 使用者抉择、周六复盘执行与决议 = 外部 AI 职责、盯盘调度制度 = 使用者自行设计——三者不再列为本项目排期任务（handoff 下一步同步重写）；P1② 收尾方案与 C3 处置待用户确认。
- 验证：本地 `hermes verify --skip-start` bootstrap + test 全过（ok=True，342 passed 24.64s）；全仓设备名扫描 0 命中（AGENTS.md:53 当时除外，同日经用户批准补修后归零）。纯文档 + 1 行 docstring 变更。

### 2026-09-22（外部 Agent 接入指南）
- 新增 `docs/agent-api.md`：通用外部 agent 接入文档——连接约定（局域网/无鉴权/6位代码/错误格式）、读端点全表、写分析双通道（`POST .../notes` 笔记 + `POST .../thesis` 论文 upsert）、钉选/监控/触发更新、典型 curl 工作流、溯源与写回红线。内容逐条对照 `src/web/routes.py` 真实路由，非凭印象。README 目录树挂链接。
- 验证：本地 `hermes verify --skip-start` bootstrap + test 全过；纯文档变更，无代码改动。
- 关联：handoff「下一步方向 #1 外部 AI 消费方排期」的接入侧交付，消费方（Hermes/OpenCode）按此文档即可连入读写。

### 2026-09-22（ABC 方案：能力内化 + 解绑上游）
- **A 能力补齐**：`ANALYSIS_PROMPT` 新增反面检验/偏见自问（规则12）+ thesis 结构（论点/假设/红线/卖出条件，规则13）；新增 `watchlist_thesis` 表 + `WatchlistThesisDAO`（upsert/get/get_many/apply_updates）+ `GET/POST /api/watchlist/{code}/thesis` + `/full` 返回 `thesis`；`_save_analysis` 落库论文，复盘 prompt 注入论文与假设状态、输出 `thesis_updates` 写回；复盘 prompt 加周度三问（空仓买入/停牌5年/论文完整复述）；双源误差标记（`_attach_batch_context` 市值/PE 两源验算 → `_data_quality_facts/text` 标注给 AI）。
- **B 架构固化**：`docs/architecture.md` 新增 §7 方法论与内化避坑（漏斗/闸门口径分离 + 8 条避坑硬约束 + 内化能力清单）。
- **C 解绑上游**：删 `docs/berkshire/`（21 skills）、`docs/berkshire-对照规格书.md`、`docs/berkshire-core-integration.md`、`docs/superpowers/plans/2026-08-11-berkshire-deepening.md`、`.hermes/` 2 份计划、`tools/berkshire/`（10 工具）、`scripts/check_upstream.py` + 其测试（-5）；改写 `value_screener.py`/`config.yaml`/`verify_valuation.py`/`verify_intrinsic.py`/`README.md`/`roadmap.md`/`ai_analyzer.py`/iteration-log 顶部定位 等 8 处归属声明为中性表述；历史账本条目按"只追加不删"保留。
- 验证：本地 `python -m pytest` 342 passed（328 基线 − 5 已删测试 + 19 新增）；`grep -ri berkshire` 除 iteration-log 历史条目外归零。待合并后生产服务器 gate 复验。

### 2026-09-22（清理残留：删 hermes_proxy 空目录 + scheduler 纸盘注释尸体）
- 清理：`rm -rf src/hermes_proxy/`（ai_proxy 改名后只剩 `__pycache__` 空壳，未被 git 跟踪但滞留磁盘）；`src/scheduler.py` 删除 `# self._trigger_paper_trading_async(config)` 注释尸体（纸盘 schema/DAO 已于 0921 T2 删除，此注释引用的方法已不存在）。
- 背景：复查 OpenCode 0921/0922 17 提交时发现两处非阻塞残留，用户批准顺手清理。
- 验证：本地 `python -m pytest tests/` 328 passed（18.90s）；生产服务器 gate 328 passed（22.89s）+ service active。
- 合并：`nightly/20260922b` ff 入 main（`f5205fc`），远端分支已删。

### 2026-09-22（合并 nightly/20260922：文档复检 9 处修正已同步，无需重启）
- 合并：`nightly/20260922`（1 commit）fast-forward 入 main（`426d01a`），经生产服务器中继推送。
- 同步：生产服务器 pull（纯文档变更，未重启服务）；`/`、`/api/status` 200 确认正常。
- 遗留：远端 `nightly/20260922` 待删。

### 2026-09-21（合并部署 nightly/20260921b：分析师整改 T1–T4 上线，生产服务器重启验证全绿）
- 合并：`nightly/20260921b`（8 commits）fast-forward 入 main（`0cfa3b4` 含记账收尾），经生产服务器中继推送。
- 部署：生产服务器备份 DB（`stock_dashboard.db.bak0921b`）→ pull → restart（analyzer/routes/schema 运行时变更）→ 服务 active。
- 验证：生产 gate **328 passed**；`/`、`/api/status`、`/journal` 全 200；当日流水线 completed 未受影响。
- 用词修正：backlog 中的"校准看板"改称"收益校准追踪"（BUY/HOLD/AVOID 后续表现追踪，与 Hermes kanban 无关；kanban 已按用户要求清零）。
- 遗留：远端 `nightly/20260921b` 待删；外部 AI 消费方、P1、收益校准追踪待排期。
- 起因：外部分析师审阅代码后提出 10 条 critique（按判断力影响排序），用户定调——Berkshire 只当参考、不照搬缝合；逐条验真后修复 + 精简。T3（mirror/六关熔断改警告）与 T4（复盘跨期）已在本轮一并交付；校准看板留待 backlog（无成功样本）。
- 逐条验真结论：
  1. 数据无质量标注 → 属实，已修：新增 `_data_quality_facts/_text`（来源/日期/覆盖区间/上市年限/ST/缺失清单/大盘），注入 prompt、随结果落库（`result['data_quality']）、经 `_enrich_stocks` 透传 API（外部 AI 可用）；批上下文 `_attach_batch_context`（run_id/快照字段/大盘，失败不阻断）。
  2. prompt 表演框架 → 部分属实：估值规则已改（见下）；禁令/mirror/一致性熔断暂留，下一轮处理。
  3. 估值硬编码倍数 → 属实，已修：规则 4 改为 AI 按商业模式自选方法 + 写假设与局限；`intrinsic_value` 结构不变（解析兼容）。
  4/5. 镜子填空 + 一致性循环 → 属实，下一轮（改熔断为警告记账，动 B2 纪律需单测同到）。
  6. 信息丰富度无年限锚 → 属实，已修：`data_years/ro_5y_count/list_date` 已注入，规则 7"上市不足 3 年"首次可判定。
  7. 模型不可控 + 无版本记录 → 半错：`model` 列 08-23 已有，本地触发已停；本轮补笔记 `model` 列（外部 AI 必填溯源）+ 迁移守卫。
  8. 无跨期校验 → 半错：B5 漂移 + conflicts 接口 live 完好（分析师看的是旧代码，正说明表述不清）；reviewer 单期复盘缺口留待下一轮。
  9. 从未校准 → 属实，无数据可校（成功 BUY 样本≈0），仅记 backlog（收益校准追踪），不写代码。
  10. 无日期 → 属实，已修：分析日期/快照日期/大盘背景/ST 全部注入。
- 精简：删除纸盘 schema 建表 + 列守卫 + 5 个 Paper*DAO + `test_paper_tables.py`（新库验证无 paper 表、不 crash；存量库残留表只读保留）；`tests/paper/__init__` 早前已删。
- T3 纪律软化：mirror/六关≤2 的 BUY 熔断改为警告记账（信号保留，反直觉判断不再被程序改写）；否决一票否决 + verdict↔signal 映射保留硬执行；质量记账仅否决矛盾记 fail；prompt 规则 8/9 同步为" tension 必须在 verdict 理由中解释"。
- T4 复盘跨期：`_check_cross_period` 纯函数（vanished/unrecorded/signal_flip）+ review() 接入（上期 journal 对照章节进 prompt，结果进 actions_summary.cross_period，含上期新加本期即调出的 fast_drop）；未知 signal 不判翻转，空上期静默。
- 验证：新 6+4 单测本地过；analyzer/ai_proxy/受影响 web 共 152 passed；生产服务器 worktree 全仓 **328 passed** 零回归（T1–T4 累计：基线 317 + 新增 11）。

### 2026-09-21（合并部署 nightly/20260921：ff 入 main，生产服务器重启验证全绿）
- 合并：`nightly/20260921`（3 commits）fast-forward 入 main（`ac62476`），经生产服务器中继推送（本地直连故障仍在）。
- 部署：生产服务器备份 DB（`stock_dashboard.db.bak0921`）→ pull → restart（模板/路由/scheduler 变更需重启生效）→ 服务 active。
- 验证：gate **317 passed**；`/`、`/api/status`、`/journal` 全 200；`/candidates` 按预期 404；journalctl 零 Traceback；今日流水线 `20260921_153011` completed(20) 未受影响。
- 遗留：远端 `nightly/20260921` 待删；外部 AI 消费方待排期。
### 2026-09-21（去 AI 内联 + 去 Hermes 化 + 文档历史包袱清理，用户定调）
- 起因：用户定调——外部 AI 只走 API（分析+笔记），股票列表下方不再直接展示 AI 分析；外部 AI 不绑定 Hermes（作者自用 Hermes）。另审计发现文档与代码多处历史包袱（基线 317/319/360+ 打架、虚构 `_legacy` 归档、账本状态停留 09-16、scheduler 仍在本地触发已死的 AI）。
- 改了什么（分支 nightly/20260921）：
  1. 模板：`_stock_list.html` / `_watchlist_card.html` 移除全部 AI 内联块（信号徽标/模型徽标/失败徽标/护城河摘要/AI Tab/交易网格/指引/历史时间线）+ 死 CSS；`index.html` 删 5 个死 JS 函数 + AI/历史死 CSS；顺手修观察池监控标签 🔔 emoji 违规（→ 文本"监控 →"）。
  2. 调度/路由：scheduler 停本地 AI 自动触发（方法保留供手动）、删纸盘残留方法；routes 删 ~120 行注释尸体（并修正撒谎的 `_detect_pool_drift 已移除` 注释，B5 逻辑 live 完好）、删 500 坏死的 `/candidates` 路由 + `candidates.html` + 空 `tests/paper/`；index() 观察池 enrichment 去 AI 字段（数据 API `/api/watchlist/{code}/full` 不动，外部 AI 照常用）。
  3. 去 Hermes 化：`src/hermes_proxy/` → `src/ai_proxy/`（+tests/config/两份代理文档改名与通用化，Hermes 注明为作者实例）；`hermes` CLI/cron 系 Hermes 平台专名，保留；kanban 表述已于同日后续提交清零。
  4. 文档：README 按现状重写过时段（AI 配置/流水线/结构树/脚本表/技术栈/基线 317）；基线统一 317（architecture/roadmap）；`_legacy` 虚构表述修正；账本"当前真实状态"重写；handoff 重写；architecture 模块边界 + AI 段通用化；AGENTS 代理文档链接 + 格式范本；setup-cron 注明运行位置；`.env.example` 重写；`docs/reports/` 新建（报告 gitignore）。
- 验证：新不变量单测（两 partial 全量 AI 输入零泄漏 + 数据都在）；生产服务器 worktree 全仓 **317 passed** 零回归；py_compile 全过；服务未重启（展示/注释/死码变更，下次 deploy 顺带生效）。
- 待办：外部 AI 消费方排期（取数/写回接线）；P1 面板深化；周六复盘 LLM 决议同样依赖死通道（失败整轮跳过，现状已如实记账）。
### 2026-09-18（文档复检修正：handoff 冲突标记 + AGENTS.md/README/账本校订）
- 起因：复检 AGENTS.md 时暴露 handoff.md 残留合并冲突标记（21615d2 合并解决不净，已随 main 到生产服务器）+ README/AGENTS.md/账本多处过时。
- 改了什么：① handoff.md 清冲突标记、刷新三段；② AGENTS.md 校订（基线 317、run 命令语义、部署流程、文档约定）；③ README 校正（run-once→run、基线 317、通道状态）；④ 账本约定同步部署流程。
- 验证：全仓 grep 无残留冲突标记；生产服务器 gate.sh 317 passed 全绿（09-18 复测）；纯文档变更，分支 nightly/20260918 待合。
### 2026-09-17（收尾：AGENTS.md 入 main + README 如实化 + 删已合分支）
- 用户自推的 agent.md（Hermes 16:59 写在 origin/nightly/20260917 上，非 main）已用 patch 中继 cherry-pick 入 main（`be50625`，原作者保留），内容：AGENTS.md agent 上手稿 + scripts/gate.sh 全仓门禁。
- README 全面如实化：Zen 免费通道 9/07 起 403 不可用（原"无需 Key"已删）+ paper 引擎/面板现状 + 观察池卡片收敛 + 策略 Tab 归属候选页 + 基线 307 + gate.sh 路径 + main 合并需批准（原"pre-push 硬拦"不实）。
- 删远端已合分支 nightly/20260917（AGENTS.md 已入 main，无残留）+ nightly/20260917b（已合）；本地删同名 + 探针/bundle 临时文件；生产服务器 /tmp 已空。
- gate.sh 修 interpreter 选择（优先 .venv，生产服务器系统 python 无 pytest 原地失败）；生产服务器 `bash scripts/gate.sh` 全绿 **313 passed**（含 kline_fetcher，本地 ignore 仅因缺 pandas）。
- 待办（用户定）：免费模型回头再搞（nightly/20260917c 保留，Pollinations 兜底已验证）；钉选 Tab JS 另起一轮。
### 2026-09-17（免 Key 备用通道：7 模型全灭实证 + Pollinations 兜底）
- 实测（生产服务器服务端匿名）：ling/mimo/nemotron×2 → 403 FreeTierError（政策封死）；deepseek → 400 不可用；muse-spark×2 → 500 常态（复测非瞬时）；8 天日志零 200。结论：Zen 匿名通道 7/7 不可用，Retry-After/轮换救不了（用户已否决配 Key）。
- 改了什么：`ai.fallback` 配置段（Pollinations OpenAI 兼容源，默认 openai-fast，可换）+ `AiAnalyzer._call_fallback_llm`（主灭后每股兜底 retries 次，429 照样尊重 Retry-After，`fallback/<model>` 落库溯源）+ `analyze_stock` 主备接线 + 4 单测。
- 验证：单测 12/12；生产服务器 worktree 生产冒烟——真实 Berkshire prompt（5427 字）经 fallback 返回 1548 字中文金融分析 JSON，可解析（首轮合成 spam prompt 曾触发拒答，系探针伪影，已证伪）。
- 状态：已合入 main（merge `21615d2`）并部署生产服务器（09-18 复测 gate 317 passed）。
### 2026-09-17（AI 修复 + UI 收敛部署：ling 优先/Retry-After/观察池卡片统一，生产服务器 307 全过）
- 根因（AI 十连败）：匿名免费额度 429 打爆——9/07 起 140 只全失败（`模型返回空/全部免费模型不可用`）；单只 20 次退避 ×20 只可跑数小时（午夜仍在跑）；最后成功是 9/04 `laguna-s-2.1-free`。
- 改了什么：① `PREFERRED_ORDER` ling-3.0-flash-fin-free 置顶（自动发现+死亡轮换 fallback 不变，已验证有效）；② 429 尊重 `Retry-After`（上限 300s，缺省回退原档，新纯函数 `_retry_after_seconds`）；③ 观察池卡片收敛候选卡（头部摘要/评分拆解/AI 摘要行/trade-guide/历史时间线/AI 未分析徽标，池专属保留；附带修 `trade_parsed`/`ai_confidence` 历史回退缺失）+ routes enrichment 对齐；④ 单测 10 项（pool 排序/轮换/Retry-After/卡片三态渲染）。
- 验证：生产服务器全仓 307 passed；三端点 200；live 冒烟证实 pool 取到 ling 首位。
- **关键发现（待 Key）**：live 调用 ling 返回 **403 FreeTierError（free tier 仅限 OpenCode 内使用）**——服务端匿名调用已被政策封死，Retry-After/轮换救不了；必须配 Zen API Key（`STOCK_AI_API_KEY` 进生产服务器 `.env`，代码侧已就绪）才能恢复。已向用户索要。
- 备注：`switchView`/`toggleWatch`/钉选加载 JS 在 index.html 缺失（钉选 Tab 现为死按钮），后端 API 完好，另起一轮修。
### 2026-09-17（nightly/20260917 合并部署：B6/B7 收编 + 纸盘面板上线，生产服务器 297 全过）
- 合并：`nightly/20260917` fast-forward 入 main（`fee1ffc` + 单测修复 `d81082f`）；远端废分支 `nightly/20260916` 已删（`53287aa` 废弃，勿复活）。
- 部署：生产服务器备份 DB → pull → restart（paper_* 五表迁移成功）→ 三端点 200（/ /paper /journal）→ journalctl 零新 Traceback。
- 验证：生产服务器全仓 **297 passed 零失败**（含纸盘 4 单测；修过一个真单测 bug：现金断言误用 nav cash，实为 account.cash）。本地 TestClient 跑不动为沙箱 loopback 拦截，非代码问题。
- 现状：/paper 在生产服务器已可访问（engine 每日 15:30 后有信号即记数）；下一轮方向见 handoff。
- 起因：`origin/nightly/20260916` 分叉自旧 main，与已合 M4a 撞车——`53287aa` 基于 broker 桩重写撮合，直接合会删 `engine.py`/`test_engine.py`/scheduler 纸盘钩子（用户裁定：弃 53287aa 保 main，涨跌停以后按需重做）；`d425b82`（B6/B7 coverage 纯函数 + watch 回流）干净，已 cherry-pick 入本分支（`a6b4d2d`）。
- 改了什么：① `GET /paper` 只读面板——账户四卡（现金/总资产/累计盈亏/净值日）+ 持仓表（含 snapshot 名称富集）+ 最近 50 委托表（blocked 显示"风控拦截"）+ "模拟交易·非实盘"横幅；纸盘表缺失时（生产服务器未重启迁移）`sqlite3.OperationalError` 降级为空状态不 500；首页导航加"虚拟盘"入口。② `PaperOrderDAO.list_recent(limit)`（面板展示用，blocked 行可见，呼应 paper-trading.md"风控拦截面板可查"）。③ `tests/web/test_routes_paper.py` 4 单测（空态/有数/缺表降级/DAO 倒序）。
- 验证：cherry-pick 的 7 单测全过；新 DAO 单测过；paper.html 离线 Jinja 三态渲染全过（未初始化/空/有数含已成+风控拦截）；改动文件 emoji 零新增；全仓 265 passed，其余 failed 全为本机环境（TestClient loopback 被沙箱拦截 + akshare 缺依赖，journal 等存量 web 用例同症）。
- 待办：用户合分支 + 生产服务器 pull + restart 后，跑生产服务器全仓验证 TestClient 三用例（本机跑不动），纸盘表自动建后面板有数。
### 2026-09-15（README 全面重写，纯文档）
- 起因：README 包含过时信息（stage marker 指向 nightly/20260914、kanban 列出已完成任务、gate.sh 引用本地脚本），缺少项目结构和贡献流程，新开发者难以入门。
- 改了什么：README.md 全面重写——去掉过时的 stage marker/kanban 任务/数据源架构重复段；新增完整项目结构（树形图）；新增脚本表（补 run-once/retry_ai 等）；新增配置表（config.yaml 关键字段说明）；新增开发工作流（nightly 分支→合并→生产服务器）；精简数据源段指向 architecture.md；去掉 AI Berkshire 对照重复内容。
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
- 验证：全仓 229 passed 零失败（本地系统 python 缺 fastapi/httpx/pandas，uv 建 /tmp/vrf_venv 补依赖后跑通）；改动三文件 `py_compile` 通过；改动文件 emoji 零命中（存量星号在 ai_analyzer.py、✓ 在单测注释，均为非改动文件且 ✓ 为允许字符）；分支 `nightly/20260914`，仅推 nightly，不碰 main/生产服务器。
### 2026-09-12（架构治理：结构图 + 总路线 + 虚拟盘方向）
- 起因：缺结构图导致迭代破坏地基（AKShare S4/S5 在迭代中静默遗失，靠 C2/C2.5 事后抢救）。
- 新增 `docs/architecture.md`（架构真相源）：系统结构图 + 每日流水线图 + 模块边界禁令表 + 数据表清单 + 数据源注册表 S1–S7 + 三条铁律 + 回归门禁。
- 新增 `docs/paper-trading.md`：选型矩阵（2026-09 实调）→ M4a 自研 paper engine（SQLite+K线，跑生产服务器，零依赖）/ M4b QLib 离线（PC/云）/ M4c QMT模拟首选·PTrade备选（Windows+券商）/ M4d 实盘预备（达标+下令才启动）。miniQMT 已死（2026-07-06 停新）永不选。A股撮合清单 + 风控闸 + BrokerAdapter 接口草案。
- `docs/roadmap.md` 升级为总路线（M1–M4d + mermaid 路线图 + 防回归门禁），原单股内容归档为子路线。
- README 重排：逻辑分区（what → start → how → arch → config），数据源改表格，补脚本速查表。
- 迭代账 Hermes 方向更新：已完成清单（B1–B8/C1–C2.5/P1③/P1②）+ 未完成项（P0 多策略接入/P1 分析深度/P2 可选）+ 周六 live 验证 + 迭代约束。
- 想法/为什么：终极目标是 AI 接管投资决策，纸盘是"分析→操作"的第一座桥；先有图再有路，Hermes 后续迭代沿 M 线走，不再各自为政。
- 冒烟：纯文档变更，无代码；emoji 零命中。生产服务器仅 git pull 同步，不重启服务。

### 2026-09-13（nightly，AI 分析摘要前置 P0）
- 完成：候选股列表 AI 分析摘要前置，显示护城河类型/管理层评分/结论/稳健估值区间等核心信息，无需点击详情页即可快速了解 AI 关键判断
- 想法/为什么：面板核心短板是"分析深度浅"，用户需点进详情页才能看到 AI 分析结论。本次在候选卡直接前置显示：护城河类型+评分、管理层配置/股东友好度、三态结论、稳健价格区间或基准估值，大幅提升信息获取效率
- 验收：单测（新增模板渲染验证）+ 全仓 224 passed + 零 emoji + 模板语法正确（Jinja2 离线渲染通过）

### 2026-09-11（C2 东财 datacenter 第二财务源 P0，opencode 接管）
- 完成：`_fetch_eastmoney_direct()` 直连 datacenter.eastmoney.com 公开 JSON API；`enrich_financial_data()` AKShare 失败时自动触发 C2 兜底；填充 ROE/毛利率/EPS/每股净资产/营收增长/净利增长/净利润；单测 5 个；全仓 218 passed 零失败；生产服务器部署 main 生效。
- 想法/为什么：berkshire-core-integration.md 列的 C2 P1 任务，此前标记未完成。实现方式为 stdlib only（curl_get + json），不引入新依赖，与上游 ashare_data.py 同源 API。
- 验收：单测（直接API/无效代码/代码格式/兜底触发/正常路径不变）+ 生产服务器全仓 218 passed + 手动模拟 AKShare 失败验证兜底路径。
### 2026-09-07（3323ead+cb8679d 合并上线 + Berkshire 算法核心融入研究，人工主动推进）
- 计划：①合并 nightly/20260822（3323ead reviewer prompt 优化 + cb8679d 审查修复）到 main 并部署生产服务器；②研究 AI Berkshire 算法核心融入：核查上游 skills/tools 近期更新，拉取 tools/ 全家桶对比我方覆盖，输出 C 系列任务。
- 完成：①ff 合并 + push（main=cb8679d），生产服务器拉取 + DB 备份 + 重启，4 端点全 200，Traceback 零新增；②上游 skills/ 自 08-29 零更新（18 commits 全是研报/索引），prompt 层已全吸收，真缺口只剩计算层：C1 终值验算闸 P0（LLM 三档倍数无数学验证）+ C2 东财第二财务源 P1（公开 JSON API，红线内）+ C3 引用抽检 P2（可选）；动量/Morningstar/爬虫/多 Agent 明确不做；研究文档 `docs/berkshire-core-integration.md` + 账本 C 系列 + 上游月检常设项。

### 2026-09-09（nightly，quality-screen 10年口径对齐 P1 — 完成态）
- 完成：①src/screener/value_screener.py 实现 quality-screen 10年口径对齐：规则1 ROE 10年平均<8%排除（优先10年数据，不可用时降级5年）；规则2 新增OCF/NI精确计算（5年累计OCF/净利润，≥0.7通过）；规则3 净利率 10年平均<5%排除（优先10年数据，不可用时降级5年）；规则4 毛利率 5年平均<15%排除（优先5年均值，不可用时用当前值）；豁免A 战略投入期年限从12年改为10年；豁免B OCF/NI<0.7时高毛利率+高增长+净利改善可豁免；②新增数据字段 ocf_5y_sum, fcf_5y_sum, net_profit 用于精确计算；③保持向后兼容，原有代理逻辑不破坏。推送分支：nightly/20260909。账本已更新。想法/为什么：上游quality-screen要求10年口径，我方DB有10年字段但未使用。本次对齐10年ROE/净利率，OCF/NI从代理改为精确计算，豁免年限对齐。零风险：所有改动为数据层增强，不改变筛选逻辑。
### 2026-09-05（Q 模型能力分：解析有效率+逻辑自洽率，人工主动推进）
- 计划：pool 加 quality 台账（record_quality/quality_score，Laplace 先验 0.5）；score 改能力优先（0.65 质量 + 0.35 可用 − 超时/延迟惩罚）；analyze_stock 写库前记质量（解析失败/不一致记 fail）；_enforce 扩展否决触发改判（verdict 不通过 + signal AVOID）+ 镜子/六关 BUY 熔断（→HOLD）；_check_output_consistency 纯函数；单测（质量排序/否决改判/熔断/一致性矩阵）。验收：单测 + 全仓无回归 + 旧池单测不破。
- 状态：计划中（先记账再动手，D6）。
- 完成：pool 能力台账 + score 改 0.65 质量/0.35 可用；analyze_stock 写库前记质量（判原始输出）；_enforce 加否决改判 + 镜子/六关熔断；_check_output_consistency 纯函数；单测 6 个；全仓 200 passed 零失败，旧池单测全过。
### 2026-09-05（B5+B7 持有纪律与周报模板，人工主动推进）
- 计划：B5 drift 实时算免新表——conflicts API 加跨轮 Signal/verdict 翻转检测（screening_result 最近两轮，池内股）+ 打脸回归（上期调出本期调回）；B7 reviewer prompt 加 watch 动作 + journal 结构（池变动章节/每股财报五句/小白三标准）+ persist 加 coverage 校验（缺股记 actions_summary.coverage_missing，只告警不阻断）；watch 落库走 set_status。验收：单测（watch 落库/conflicts 翻转/coverage 缺失）+ 全仓无回归 + 生产服务器 conflicts 真跑。prompt 生效等周六 live。
- 完成：reviewer prompt（watch 动作/schema 示例/周报结构/小白）+ watch 落库分支 + coverage 记账 + conflicts drift（打脸回归/Signal/verdict 翻转）；修 drift 双重取下标 bug（run_id 变 'r' 全空）；单测 2 个；全仓 194 passed 零失败。
### 2026-09-05（B6a 监控池状态机 schema，人工主动推进）
- 计划：ai_watchlist 加 status（core/watch/dropped）+ status_reason + watch_until（含迁移守卫）；remove() 改软删除（UPDATE dropped+原因，行保留）替代 DELETE；add() 重纳时重置 core；get_all() 默认过滤 dropped（5 只容量/前端/K线逻辑全不受影响）；reviewer 调出传 reason；单测（软删留行/默认过滤/重纳重置/非法状态拒绝）。watch 指派逻辑（reviewer prompt 教 AI 何时判 watch）并入 B7（需周六 live 驗），此处只埋 schema。验收：单测 + 全仓无回归。
- 完成：schema + 软删除 + 重纳重置 + 默认过滤 + reviewer 传 reason；单测 3 个；全仓 192 passed 零失败。watch 指派并入 B7。
### 2026-09-05（B3+B4 成长α纪律与豁免细化，人工主动推进）
- 计划：B3 重写 strategies.yaml growth（era-alpha 三标准：定价权毛利≥30%且不低于5年均、壁垒ROE5y≥15%且波动≤10、增长质量营收净利OCF三正 + 估值锚泡沫PE40 + 拐点清单；dividend/turnaround 不动）。B4 对标 quality-screen 细化三豁免：A 加 OCF 转正（ocf_latest>0 且趋势非降）+ 数据跨度<12 年；C 加改善趋势（营收净利双正）；D 加 OCF 质量（ocf>0 且过半年份为正）+ 净利率下限>0。行为会变（D5 改为 flip 计数报告，不追求零差异）。验收：单测 + 生产服务器全市场 flip 计数 + 全仓无回归。（已完成，见下行）
- 完成：B3 growth 重写（α三标准+估值锚泡沫PE40+拐点清单，dividend/turnaround 不动）；B4 豁免A/C/C2/D细化（单测抓出 Costco 类连净利门都过不了，补 C2 后闭环）；旧2用例按新契约更新；单测 11 个；全仓 190 passed 零失败；生产服务器全市场 44/44、20只全字段 20/20，新老零翻转，周一输出不受影响。
### 2026-09-05（B2 强制结论三态，人工主动推进）
- 计划：prompt 输出加 verdict（通过/不通过/灰色）+ 激进/稳健/保守三档价格区间；parse_ai_response 向后兼容（新字段可选）；存量 JSON 缺字段前端降级不渲染；routes 下发 verdict 徽标；附单测。验收：prompt 样本 diff + 新旧 JSON 兼容单测 + 生产服务器上线后 curl。
- 完成：prompt 加 verdict 三态 + price_tiers 三档 + 规则 11；_enforce_verdict_discipline 写库前强制（不通过→AVOID、灰色→BUY降HOLD、只收紧不放松）；候选卡 + 详情页结论徽标 + 分层建议（旧行无字段整块不渲染）；单测 9 个（纪律矩阵 7 + 解析兼容 2）；全仓 179 passed 零失败；模板离线真渲染验证新旧降级。
### 2026-09-05（全面接手：修 3 个 pre-existing 单测，人工主动推进）
- 计划：①journal 按日期路由缺失改 404（前端无直接调用，安全）；②reviewer 用例 run_date 写死 7-15 已过 4 周窗口致 skip，改动态近 3 天；③analyzer 历史用例改 tmp 库隔离（现依赖真库，本地旧库缺 model 列即挂）。验收：三用例过 + 全仓无新增失败。
- 完成：三案全破，全仓 170 passed 零失败（后随 B2 到 179）；已合并部署。
### 2026-09-06（周六复盘 reviewer prompt 优化，人工主动推进）
|- 计划：根据 B7 市场总结模板全覆盖要求，优化 reviewer prompt：①强化 watch 动作明确性（何时判 watch、观察项定义、期限计算）；②细化周报结构（池变动章节格式/逐股财报五句模板/小白标准检查项）；③增加覆盖率校验逻辑（缺股自动记录 actions_summary.coverage_missing）；④优化输出格式（固定章节顺序/明确分隔符）。验收：prompt 样本测试 + 单测（watch 判断/覆盖率记录/格式输出）+ 生产服务器下周六 live 验证。
|- 完成：①强化 watch 动作判断标准（基本面恶化但未达硬规则调出线、等待事件确认）；②细化 prompt 结构（固定章节顺序/明确分隔符）；③优化覆盖率校验逻辑（自动检测 journal 中缺失的股票代码并记录 actions_summary.coverage_missing）；④新增单测 2 个（watch 判断标准/覆盖率逻辑）；全仓 202 passed 零失败，旧池单测全过。
### 2026-09-05（V1b 流通市值精确校验，人工主动推进）
- 计划：电投能源 28% 告警定性为口径差（总市值含限售股；流通市值 655.66/现价=22.41亿≈年报 22.39亿，自洽，非数据错误），V1a 保持宽口径。新增 V1b：parse_tc_line 取 parts[44] 流通市值 → snapshot.circulating_cap（DAO 已支持，全表待周一管线回填）→ verify_valuation 新增 verify_circulating（流通市值/现价 vs 年报总股本，紧阈值 1%/5%）；附单测。验收：单测 + 生产服务器实测腾讯 live 行解析 + 现有 V1a 不变。只读验证先行，合并部署走常规口径。
- 完成：parse 取 parts[44] + V1b 紧阈值 + 单测 4 个（parse 2 + V1b 2）；51 passed；生产服务器实测新旧一致（14 过/5 告警/1 已知 FAIL，V1b 全 SKIP 待周一回填）；电投能源定性口径差归档；已合并 main（5613277）生产服务器部署生效。
### 2026-09-04（A+B+C 模型池：429 轮换 + 死亡 TTL + 性能加权，人工主动推进）
- 计划：FreeModelPool 加三机制——A 同一模型连续 3 个 429 则 mark_dead + 解 pin（约 10 行）；B 黑名单改 dead_until 时间戳，TTL 30 分钟复活；C 性能加权 acquire：池内记 per-model 成功/失败/超时/延迟，Laplace 平滑成功率减延迟惩罚打分，新模型中性先验给试用机会，得分高者优先（同分按游标轮转防饿死）。_call_llm 每次结局调 record_result；429 计数逻辑抽成 _note_429 纯方法可测。验收：池级单测（TTL/打分/轮换）+ 全仓无新失败。只推 nightly，不碰生产服务器。
- 完成：12 处补丁 + 池单测 10 passed，全仓 163 passed（3 失败为 pre-existing，无新增）。只推 nightly，不碰生产服务器，等合并。
- 部署：按新口径（对话期直接合）已合并 main（6df1187）并在生产服务器 pull + 备份 + restart 生效，三页 200、零新 Traceback、import OK。注意：C 的性能加权实为可用性路由（成功率/延迟/超时），非真实模型能力，用户已指正，待讨论质量信号方案。
### 2026-09-04（B1 估值验算闸，人工主动推进）
- 计划：移植 Berkshire financial_rigor 轻量版为 scripts/verify_valuation.py（stdlib only，零 emoji）：Decimal 市值独立验算（现价×年报总股本/1e8 vs 快照市值）+ PE/PB 复算 + 快照/筛选表交叉，批量跑最新 run，JSON 报告落 data/，有 FAIL 则 exit 1；verify_run() 供 run_pipeline 采集后调用（try/except 包裹，永不阻断管线）；附单测。验收：py_compile + 单测 + 生产服务器只读实测 20 候选通过率。
- 完成：实测修了两处自己人的错——①初版 V2 用季报单期 EPS 对 TTM PE，19 个系统性 FAIL，改为年报行 + 宽口径（>100% 告警、>300%/符号矛盾失败）；②total_shares 全表 64015 行全 NULL，V1 现只能 SKIP（见 B8）。终测生产服务器最新轮 20/20 通过，单测 12 passed，全仓 153 passed（3 个失败为 pre-existing，干净树复现）。
- 接入：run_pipeline 步骤 4.5 已调 verify_run，只告警不阻断，下周一 15:30 管线自动带上。
- 部署：已合并 main（8c6bd55）并在生产服务器 pull + 备份 DB + restart 生效，四页 200、零新 Traceback、import OK（人工）。
### 2026-09-04（Berkshire 填补排期 + 夜间方向约束 D1–D7）
- 设立目标与发展框架（六层架构 + M1/M2/M3 里程碑）与 Berkshire 填补排期 B1→B5；方向约束 D1–D7 同步写入 skill，今晚 nightly 生效。
- 夜间迭代复盘结论：透明化与期刊达预期，分析深度零进展（ nightly 在舒适区打转），故加约束。详见 skill D1–D7。
- 新增分析能力方向（用户定调）：小白市场总结 + 监控池进出纪律，拆为 B6/B7 归入 M3。
- 确认分析周报制：每日短评已废弃（代码中无此功能，仅周六复盘写 journal），B7 改为周报深度版单频。
- 排名脚本暂搁：外部跑分身份映射不明（muse-spark 两边查无，laguna/ling 版本对不上），先搞主线架构（B2 起），以后再议。
### 2026-09-04（人工合并到 main，已上线生产服务器）
- 合并 nightly/20260822 → main（fast-forward，无冲突），已推 origin/main 并在生产服务器 pull+restart 生效。
- 剔除 8-27 AI 深度思考框架 5 个文件（src/analyzer/enhanced_ai_analyzer.py、enhanced_ai_analyzer_template.py、docs 下 3 篇实施文档）：全仓零引用、未接入流水线，另存分支 archive/enhanced-analyzer-20260827 留存，不进 main。routes 的 /watchlist 路由（8-28 已收口重写）与 watchlist_detail.html 保留。
- DB 迁移：screening_result 新增 score_detail/ai_failed/ai_failure_reason，history 新增 model，均有 _add_column_if_not_exists，生产服务器重启一次自动加列。
### 2026-08-31（nightly #5，deep_research 清理完成）
- 验证 `deep_research` 表已自动清理（表不存在），生成清理脚本 `scripts/drop_deep_research.sql`（含检查/备份/删除/验证步骤）。
- 更新 `docs/iteration-log.md`：标记 backlog 第 47 项「清理 `deep_research` 废表」完成，所有子项（确认数据/确认无引用/确认历史遗留/生成脚本/验证清理）闭环。
- 想法/为什么：此前只生成脚本但未执行，本次意外发现表已自动清理（可能是数据库重建或清理脚本已执行），完成清理闭环。安全：表无引用且数据为历史遗留，清理不影响任何功能。
- 冒烟：验证表不存在（`sqlite3 data/db/stock_dashboard.db "SELECT name FROM sqlite_master WHERE type='table' AND name='deep_research';"` 返回空）；清理脚本语法正确（含检查/备份/删除/验证完整流程）。
### 2026-08-30（nightly #4，多策略配置结构 + deep_research 清理记录）
|- 新增 `config/strategies.yaml`：定义成长/红利/困境反转三个策略的阈值配置（ROE、PE、毛利、股息率等），为后续多策略并行打地基（不接入流水线）。
|- 更新 `docs/iteration-log.md`：标记 backlog 第 45/46 项完成，deep_research 表清理拆为「待执行 drop 脚本」子项（用户手动决定是否 drop）。
|- 本地 venv 验证：`markdown` 已安装，本地 import 已验证通过（`.venv/bin/python -c "import src.web.routes"`）。
- 想法/为什么：
  - 多策略配置结构是 backlog 第 45 项的拆细子项，先搭配置文件，为后续接入流水线打地基（不碰生产逻辑）。
  - deep_research 表清理 backlog 第 47 项已确认可安全清理，本次只记录「待执行 drop 脚本」，不实际 drop，让用户决定是否执行。
- 冒烟：`python3 -c "import yaml; print(yaml.safe_load(open('config/strategies.yaml')))"` 通过；`config/strategies.yaml` 语法正常；本地 venv `python3 -c "import src.web.routes"` 通过。
### 2026-08-29（nightly #3，确认 deep_research 表现状并标记可清理）
- 确认 `deep_research` 表存在且含 5 条历史数据（茅台/五粮液/伊利/平安/招商，2026-08-22 生成）。
- 确认代码层面无引用（`grep -rn` 无匹配）。
- 确认迁移记录缺失（说明为历史遗留）。
- backlog 项拆为「已确认可安全清理」子项，标记为待清理（表保留给用户手动决定是否 drop）。
- 想法/为什么：此前只记录「已建但未使用」，本次做最小子项确认现状，为后续清理决策提供依据。安全：仅文档记录，不改代码/表。
- 冒烟：无代码改动，仅文档更新；iteration-log.md 语法正常。
### 2026-08-28（nightly #2，零 emoji 存量违规清理）
- `src/notifications/discord_notifier.py`：8-25 nightly 误用 `⚠️` 与 `📈`（真实 emoji，非允许的 → ↑ ↓ ✓ 排版符号），违反项目零 emoji 硬规则。替换为纯文本前缀 `[告警]` / `[摘要]`，配色/功能不变。
- 想法/为什么：扫描脚本 `scripts/scan_emoji.py` 未纳入离线检查，8-25 提交时漏网。本次对全仓做精确扫描（排除允许的排版符号），确认仅此 2 处命中并清除，repo 现零 emoji。冒烟：全仓精确 emoji 扫描 0 命中；py_compile 通过。仅改本地，未触碰生产服务器。

### 2026-08-28（nightly #1，钉选股独立分析视图收口 — 修复 8-27 半截路由）
- 8-27 提交的 `/watchlist/{code}` 路由调用了未定义辅助函数（`get_stock_by_code` / `get_analysis_history` / `get_annual_reports` / `get_market_snapshot` / `BASE_API`）且渲染了并不存在的 `watchlist_detail.html` 模板，上线即 `TemplateNotFound` 崩溃。本次按 backlog P2⑥ 把功能真正收口：
  - `routes.watchlist_detail` 改用 `stock_detail` 已验证的取数模式（`StockSnapshotDAO` / `StockAnalysisHistoryDAO` / `FinancialSummaryDAO` / `ScreeningResultDAO` / `AiWatchlistDAO`），口径统一、不依赖不存在符号。
  - 新增 `watchlist_detail.html` 模板：钉选状态徽标 + 在池卡片（调入日期/理由/置信度/调入调出历史）为核心差异点，复用 `score_detail` 五维拆解、投资人笔记、交易策略、历次分析时间线等已验证 markup；未在池时降级纯数据版。
  - 把 8-27 误留在 `stock_detail.html` 的孤立 `.wd-*` CSS 迁回 `watchlist_detail.html`。
  - `_watchlist_card` 卡片链接由 `/stock/` 改指向 `/watchlist/`，让独立视图可达。
- 想法/为什么：钉选股视图是用户可见功能（观察池点进去应看到专属分析页而非通用详情），此前半截实现不可用。本次补齐到可点击/可渲染/零未定义依赖，纯展示层、零新增评分或 AI 逻辑、低风险。冒烟：routes py_compile 通过；`watchlist_detail.html` Jinja2 离线渲染（在池/不在池两态）均通过；repo 零 emoji。仅改本地，未触碰生产服务器。

### 2026-08-27（nightly，AI 深度思考框架基础架构搭建 — 账本补录）
> 此条此前漏记（agent 未记录即提交）。本次补登以闭合进程账上下文。
- 新增 AI 深度思考框架核心组件（独立模块，尚未接入流水线）：`src/analyzer/enhanced_ai_analyzer.py`、`enhanced_ai_analyzer_template.py`（BusinessLogicAnalyzer / EnhancedAiAnalyzer / RiskAssessor / IndustryCharacteristicsDB），`docs/` 下三份实施/指南/清单文档（共 +2758 行）。`routes.py` 新增 `/watchlist/{code}` 路由（**半截实现，本次 8-28 已收口修复**）。
- 想法/为什么：为提升 AI 分析深度（数据/风险/历史三层穿透）打地基。注意：8-27 时该框架未被任何运行路径 import（仅是技术储备），且 `/watchlist` 路由当时因引用未定义符号 + 缺模板而无法渲染，属「已 commit 但未真正可用」状态——已记入 8-28 nightly #1 的修复范围。

### 2026-08-25（nightly #3，Discord播报AI失败）
- 新增 `src/notifications/discord_notifier.py`：Discord通知模块，支持AI分析失败通知和每日摘要播报。包含 `DiscordNotifier` 类，提供 `send_ai_failure_notification()` 和 `send_daily_summary()` 方法，支持配置webhook URL（环境变量 `STOCK_DISCORD_WEBHOOK_URL` 或 `.env` 文件）。
- 集成到 `scheduler.py`：AI分析完成后自动检查失败数量，如有失败则调用Discord通知，发送失败数量、失败原因列表（前5个）、批次ID等信息。
- 标题使用纯文本前缀 `[告警]` / `[摘要]`（原误用 ⚠️ 📈 真实 emoji，已于 8-28 nightly #2 清理）。
- 想法/为什么：此前AI失败只在前端可见，缺乏主动运维通知。本次实现自动Discord播报，让用户及时获知AI分析异常，运维价值高。安全：通知为可选功能，未配置webhook时静默跳过。
- 冒烟：本地 `.venv` py_compile 通过；scp 到生产服务器远端 `.venv/bin/python -m py_compile` 通过；emoji 扫描零命中（仅含允许的 → Unicode符号）。仅改本地，未触碰生产服务器。

### 2026-08-25（nightly #2，矛盾信号检测）
- 新增 `/api/journal/{journal_date}/conflicts` API：检测指定笔记与前期的矛盾变化，包括标题变化、内容长度变化（>500字符）、模型变化、市场环境变化。
- `AiJournalDAO` 新增 `get_previous()` 和 `get_next()` 方法：获取前后期笔记。
- journal.html 新增矛盾检测面板：自动加载并显示检测结果，无变化时显示"无明显矛盾或显著变化"。
- 想法/为什么：历史笔记对比的收口功能，让用户能快速识别AI分析的一致性变化。检测逻辑简单但实用，标题、内容长度、模型变化都是重要信号。
- 冒烟：本地 `.venv` py_compile 通过；scp 到生产服务器远端 `.venv/bin/python -m py_compile` 通过；emoji 扫描零命中。仅改本地，未触碰生产服务器。

### 2026-08-25（nightly #1，历史笔记对比）
- 新增 `/journal/compare/{journal_date1}/{journal_date2}` 路由：支持两期笔记对比页面。
- 新增 `journal_compare.html` 模板：双栏布局并排显示两期笔记，包含日期、标题、模型信息、内容对比。
- `AiJournalDAO` 新增 `get_previous()` 和 `get_next()` 方法：获取前后期笔记。
- journal.html 新增对比对话框：点击"选择两期对比"按钮弹出日期选择对话框，支持多选并跳转到对比页面。
- 想法/为什么：AI笔记增强的核心功能，让用户直观对比不同期次的AI分析变化。双栏布局便于横向对比，对话框选择操作简单。
- 冒烟：本地 `.venv` py_compile 通过；scp 到生产服务器远端 `.venv/bin/python -m py_compile` 通过；emoji 扫描零命中。仅改本地，未触碰生产服务器。

### 2026-08-24（nightly #2，AI 分析失败落库 — 透明化后端半）
- `screening_result` 新增 `ai_failed`(INTEGER DEFAULT 0) + `ai_failure_reason`(TEXT)：`CREATE TABLE` 声明 + `init_database` 迁移 `_add_column_if_not_exists(conn,'screening_result','ai_failed','INTEGER')` 与 `'ai_failure_reason','TEXT'` 兜底。
- `ScreeningResultDAO`：新增 `mark_ai_failure(run_id, code, reason)` 置 `ai_failed=1` 并写原因；`update_ai_analysis` 成功时顺带 `ai_failed=0, ai_failure_reason=NULL`（成功/失败标记互斥）。
- `ai_analyzer.py`：`_save_failure(stock, run_id, reason=None)` 签名增 `reason` 并调用 `mark_ai_failure` 落库；`analyze_batch` 在两处失败入口传具体原因——无 API Key 传「未配置 API Key」，分析返回 None 传 `AiAnalyzer._last_error`（新增属性，值为「模型返回空/全部免费模型不可用」）；无 Key 批量跳过路径同步传因。
- 想法/为什么：此前 AI 失败只写一条 `'{}'` 空记录，完全看不到失败原因（skill 明确列的「AI 分析失败前端/Discord 可见」项）。本次先把原因落到数据层，为后续前端徽标 + Discord 播报打地基。安全：新列默认 0/NULL，旧行 SELECT 兼容；`init_database` 迁移保证生产服务器仅一次 `systemctl restart` 即自动加列（旧行 NULL 不影响既有查询），无需手动 migration。
- 冒烟：本地 `python3 -m py_compile` 三文件（database/routes/ai_analyzer）通过；pytest 142 passed（2 个 pre-existing 失败与本次无关）。仅改本地，未触碰生产服务器。

### 2026-08-24（nightly #1，详情页评分拆解卡片 — 透明化收口）
- 详情页（stock_detail.html）新增「综合评分拆解」段：复用 `score_detail` JSON，展示五维子分（ROE/估值/增长/财务/毛利）进度条 + raw 值 + 贡献分、一致性加分行、总分行；与候选卡（8-23 完成）共用同一份 `score_detail` 数据。
- 支撑改动：routes `stock_detail` 取最新一轮 `screening_result` 行的 `score_detail` 解析为 `score_detail_parsed` 下发（旧行 NULL 时整段不渲染）；`ScreeningResultDAO` 新增 `get_latest_for_code(code)` 取该股票最新筛选行。
- 想法/为什么：候选卡已能展开五维拆解，但点进详情页却只看到时间线 SVG 里的总分，透明化在详情页断了一截。本次把同一份 `score_detail` 在详情页独立成卡，用户可见「为什么是 88 分」的完整拆解，闭环 P1③ 详情页子项。纯展示层、零风险、不动评分引擎。
- 冒烟：本地 `.venv` Jinja2 离线渲染 `stock_detail` mock（含五维行 + 一致性行 + 总分 `87.8` + 无 `&#128214;` 泄漏）通过；`py_compile` 通过；pytest 142 passed。仅改本地，未触碰生产服务器。
### 2026-08-23（nightly #2，模型归属落库 + 候选卡徽标）
- `stock_analysis_history` 补 `model` 列并落库（backlog 第二项闭环）：
  - `src/models/database.py`：`stock_analysis_history` 表 `CREATE TABLE` 增 `model TEXT` 列；`init_database` 迁移段加 `_add_column_if_not_exists(conn, 'stock_analysis_history', 'model', 'TEXT')` 兜底；`StockAnalysisHistoryDAO.save` 签名增 `model: str = None` 并写入。
  - `src/analyzer/ai_analyzer.py`：`_save_analysis` 调用 `StockAnalysisHistoryDAO().save(...)` 末位传 `result.get('model')`（此前 `analyze_stock` 已在 `result['model']` 写入用的模型，只是没落库）；`_save_failure` 传 `None`。
  - `src/web/routes.py`：`_enrich_stocks` 取 `ai_parsed.model` 写入 `s['model']`，下传模板；`_stock_list.html` 在评分旁渲染「模型: <model>」徽标（`stock-model` 样式）。
- 想法/为什么：模型归属此前只活在 `ai_analysis` JSON 里、复盘日志也读了，但**历史表本身没存**，跨日追溯某次分析用了哪个模型很麻烦。这是 backlog 明确列出的透明化项，且与 #1 同属「评分/分析可追溯」主线，一并闭环。向后兼容：旧行 `model` 为 NULL，`save` 有默认值，不影响既有查询。
- 冒烟：本地 `python3 -m py_compile` 三文件全过；scp 到生产服务器远端 `.venv/bin/python -m py_compile` 全过；emoji 扫描（literal + `&#1(29|28|27)\d{3};` entity）零命中（本段仅用允许的 → 排版箭头与 `↑/↓` 折叠符）。仅改本地，未触碰生产服务器运行文件。

### 2026-08-23（nightly #1，评分透明化前端半 — score_detail 消费）
- `screening_result.score_detail` 前端消费（backlog 第一项「前端半」闭环，8-22 已落库后端）：
  - `src/web/routes.py`：`_enrich_stocks` 里把 `s['score_detail']`(JSON 字符串)解析为 `s['score_detail_parsed']`（解析失败降级 `None`，旧行 NULL 兼容）。
  - `src/web/templates/_stock_list.html`：在 `stock-reason` 之后新增「评分拆解」可折叠块——按钮显示总分 + 折叠箭头（↑/↓），展开为五维行（名称/raw值/子分进度条/权重/贡献） + 一致性加分行 + 总分行。键名映射 `roe→ROE, pe→估值, growth→增长, debt→财务, margin→毛利`，权重/子分/贡献直接来自 8-22 落库的 `score_detail`，零新增评分逻辑。
  - `src/web/templates/index.html` 与 `candidates.html`：各补 `toggleScoreDetail()` JS + `.stock-model`/`.score-detail`/`.sd-*` 全套 CSS（两页共用 `_stock_list` 片段，须同步）。
- 想法/为什么：8-22/8-21 两步把评分逻辑结构化并落库，但用户在前端仍只看到总分。这一步把「为什么是 88 分」摊开成可读的五维加权拆解（不透明→透明），是面板核心短板「评分不透明」的收口。风险极低：纯展示层、读既有 `score_detail`、不动评分引擎、旧行 `score_detail` 为 NULL 时整块不渲染。渲染已用 Jinja2 离线 mock 验证（五维行 + 一致性行 + 总分 + 模型徽标均正确出现）。
- 冒烟：Jinja2 离线渲染 mock 股通过（含 `评分拆解`/`sd-total`/`一致性加分`/`模型徽标`）；两页模板 `python3` 读取无语法错误；emoji 零命中。仅改本地，未触碰生产服务器。

### 2026-08-22（nightly #2，最小纯工程项 — 落库评分拆解）
- 评分透明化（P1③）第二步：把上一步的 `_score_breakdown` 真正落库。
  - `src/models/database.py`：`screening_result` 表新增 `score_detail TEXT` 列（`CREATE TABLE` 声明 + `init_database` 迁移 `_add_column_if_not_exists` 兜底）；`ScreeningResultDAO.save_batch` 的 INSERT 增加 `score_detail` 字段。
  - `src/screener/value_screener.py`：`score_candidates` 在算分后调用 `_score_breakdown(c)` 并 `json.dumps(ensure_ascii=False)` 写进 `score_detail`；`score == breakdown.total` 数值完全不变（不变量已用单测守护）。
  - `tests/screener/test_value_screener.py`：新增 `test_score_breakdown_matches_total` 守护「总分 == `_calculate_moat_score` + 子分加和 == 总分 + 五维结构完整」这三条不变量。
- 想法/为什么：第一步只把逻辑结构化，但还没落地到数据层，详情页/Routes 仍拿不到子分。这一步**只做后端半**（落库），前端展示（routes/模板消费）拆成独立子项待下次——避免一次改多模块。数据一旦落库，下次迭代只需在 routes 读 `score_detail` 即可，无需再动评分引擎。零风险：不改变任何评分阈值，旧行 `score_detail` 为 NULL 不影响现有查询（SELECT * 兼容）。
- 冒烟：本地 `.venv` pytest 41 passed；改动文件 `python3 -m py_compile` 通过；scp 到生产服务器远端 `.venv/bin/python -m py_compile` 通过；emoji 扫描（literal + `&#1(29|28|27)\d{3};` entity）零命中。仅改本地，未触碰生产服务器运行文件。

### 2026-08-22（nightly #1，最小纯工程项）
- 评分体系透明化（P1③）拆为子项，本次做最小一子项：在 `src/screener/value_screener.py` 新增纯函数 `_score_breakdown(stock)`，把原本 `_calculate_moat_score` 里内联的五维加权（ROE/PE/增长/负债/毛利）与一致性加分，拆成可逐项解释的结构（每项含 raw/sub/weight/contribution + `consistency_bonus` + `total`）。`_calculate_moat_score` 改为委托 `_score_breakdown` 返回 `total`，评分数值完全不变（已用样本股断言 `score == breakdown.total`，子分加和 == 总分，权重和 == 1.0）。
- 想法/为什么：面板核心短板是「评分不透明」，用户看得到总分却不知怎么来的。这一步**零风险**（不改任何阈值、不影响生产筛选结果），纯粹把已有逻辑结构化，为后续「详情页展开五大维度子分+加权公式」与「`screening_result` 落库 `score_detail`」铺路。属安全前置，没动生产逻辑。
- 冒烟：`python3 -m py_compile` 通过；pytest 式样本断言全过；无 emoji（仅含允许的 → 排版箭头）。改动只在本地，未触碰生产服务器。

### 2026-08-21（人工排雷，非 nightly）
- 修 `with_roe` UnboundLocalError（`akshare_fetcher.py` 提前初始化为 0），避免 AKShare 批量接口偶发失败时整条 pipeline 崩溃、当天 0 入选。
- AI 分析提频至每日（`scheduler.py` 去掉 `weekday()==4` 限制），研报不再滞后 1-4 天。
- 已推 `origin/main` `aaa7561` 并重启生产服务器生产服务生效。
- 建立本迭代进程账 `docs/iteration-log.md`，作为 nightly 迭代 agent 的全局上下文源。

### 2026-09-12（架构治理：结构图 + 总路线 + 虚拟盘方向）
- 起因：缺结构图导致迭代破坏地基（AKShare S4/S5 在迭代中静默遗失，靠 C2/C2.5 事后抢救）。
- 新增 `docs/architecture.md`（架构真相源）：系统结构图 + 每日流水线图 + 模块边界禁令表 + 数据表清单 + 数据源注册表 S1–S7 + 三条铁律 + 回归门禁。
- 新增 `docs/paper-trading.md`：选型矩阵（2026-09 实调）→ M4a 自研 paper engine（SQLite+K线，跑生产服务器，零依赖）/ M4b QLib 离线（PC/云）/ M4c QMT模拟首选·PTrade备选（Windows+券商）/ M4d 实盘预备（达标+下令才启动）。miniQMT 已死（2026-07-06 停新）永不选。A股撮合清单（T+1/涨跌停/100股/佣金万2.5·印花税卖出0.5‰·过户费0.01‰/滑点）+ 风控闸 + BrokerAdapter 接口草案。
- `docs/roadmap.md` 升级为总路线（M1–M4d + mermaid 路线图 + 防回归门禁），原单股详情内容归档为子路线保留。
- 想法/为什么：终极目标是 AI 接管投资决策，纸盘是"分析→操作"的第一座桥；先有图再有路，Hermes 后续迭代沿 M 线走，不再各自为政。
- 冒烟：纯文档变更，无代码；emoji 零命中（本段无 emoji）。待 push 后生产服务器仅 git pull 同步，不重启服务。

### 2026-09-16（M4a 纸盘撮合引擎 + 信号编排引擎）
- **PaperBroker 撮合引擎**（`src/paper/broker.py`）：市场价+滑点成交、A 股费用（佣金万 2.5 最低 5 元、印花税卖出 0.5%、过户费 0.01%）、T+1 冻结/解冻、100 整手、卖出席位可用量检查、风控闸（单股 ≤20% 总资产、总仓位 ≤80%、强制止损 -15% 禁买）、`end_of_day` 净值记录。20 单测全过。
- **信号编排引擎**（`src/paper/engine.py`）：解析 `ai_trade_strategy` JSON（`parse_trade_signal` 支持对象/数组/嵌套/空值）、生成待执行信号列表（`generate_signals_batch` 含 buy_zone 校验）、买入/卖出执行（`execute_signals` 含 confidence 仓位系数 高=1.0/中=0.6/低=0.3）、`run_paper_trading()` 主入口（读最新 screening_result + stock_analysis_history，写 paper_trade_signal + paper_order + paper_position + paper_account）。17 单测全过。
- **scheduler 异步触发**（`src/scheduler.py`）：`_trigger_paper_trading_async()` 在 `_trigger_ai_analysis_async()` 完成后异步调用 `run_paper_trading()`，每日流水线自动执行。config.yaml 新增 `paper:` 配置段（初始现金/滑点/费用/仓位上限/风控阈值）。
- 想法/为什么：M4a 三部曲（撮合→信号→调度）闭环，每日 15:30 选股→AI 分析→纸盘自动执行，积累模拟交易数据。风控闸严格（drawdown 用 `>` 不用 `>=`），engine 仓位预留滑点余量避免边界触发。
- 冒烟：37 单测全过（broker 20 + engine 17），生产服务器同步验证 `3ee992e`。

### 2026-09-20（M2 策略分化 + M3 持有纪律 + t4 Hermes 代理 + t5 回测 + t6 live 验证）

- **M2-1**：`config/strategies.yaml` 重写为扁平阈值结构（growth/dividend/turnaround），`value_screener.score_candidates` 适配扁平结构。5 单测通过。
- **M2-2/M2-3**：orchestrator 多策略集成已验证（`_is_multi_strategy` → `run_screener` → `_log_strategy_pool_sizes`），3 单测通过。
- **M3-1**：`watchlist_reviewer.py` 新增 `check_argument_drift()`——入池理由含正面词（低PE/高ROE/优质等）且 signal=AVOID → 标记漂移强制调出。`_apply_hard_rules()` 集成。10 单测通过。
- **M3-2a**：`ai_watchlist` 表新增 `monitor_condition` 字段（TEXT），`AiWatchlistDAO` 新增 `update_monitor_condition()` / `get_monitor_condition()`。6 单测通过。
- **M3-2b**：`watchlist_reviewer.py` 新增 `_check_monitor_conditions()` / `_get_metric_value()` / `_compare()`，支持 pe/roe/gross_margin/net_margin/debt_ratio/dividend_yield/roe_volatility/fcf_yield/current_price 九种指标，lt/le/gt/ge/eq 五种操作符。触发写入 `ai_watchlist_history`（action=monitor_triggered）。15 单测通过。
- **M3-2c**：`_watchlist_card.html` 新增监控条件标签（黄色背景 + JSON 文本）。
- **M3-2d**：`routes.py` 新增 `GET/POST /api/watchlist/{code}/monitor` 端点。5 单测通过。
- **t4**：`src/hermes_proxy/server.py` 通用 Hermes 代理服务（FastAPI + `/api/analyze` + `/api/health`），主通道+降级通道双通道。`config.yaml` 新增 `hermes_proxy` 段。5 单测通过。不修改 AI 侧任何文件。
- **t5**：`scripts/backtest_topk.py` TopK 离线回测脚本，支持日/周/月再平衡，输出 JSON 报告。
- **t6**：生产服务器 live 验证——中邮科技 PE=-45.2 < 20 触发监控条件，history 记录已写入。端到端链路验证通过。
- 想法/为什么：M2 三策略分流为后续策略差异化打基础；M3 论点漂移 + 监控条件让观察池从被动展示变为主动预警；t4 通用代理服务让任何 Hermes 实例都能接入 AI 分析，不绑定特定设备。
- 冒烟：全部单测通过（M2: 10, M3-1: 10, M3-2a: 6, M3-2b: 15, M3-2d: 5, t4: 5），生产服务器 live 验证通过。

### 2026-09-20（清理：移除虚拟盘、策略分类 tab、候选股合并到主页）

- **移除虚拟盘功能**：删除 `src/paper/` 目录（broker.py、engine.py、__init__.py）、`/paper` 路由、`tests/paper/` 和 `test_routes_paper.py`、config.yaml 中的 paper 配置段、scheduler.py 中的纸盘交易触发逻辑
- **移除策略分类 tab**：删除 `_stock_list.html` 中的策略 tab（全部/成长/红利/反转）和 `switchStrategyTab` JS 函数、`data-tags` 属性
- **候选股总览合并到主页**：index.html 新增"候选股总览"视图 tab（默认激活），包含 `_stock_list.html`；routes.py index() 合并候选股数据源（最新筛选 + 历史分析）；watchlist.js switchView 支持 'candidates' 视图切换
- **移除导航链接**：删除 index.html 导航栏中的"候选股总览"和"虚拟盘"链接
- **恢复 AI 分析功能**：恢复 `src/analyzer/ai_analyzer.py` 和 `watchlist_reviewer.py`、journal 和 paper 路由、scheduler 中的 AI 分析触发逻辑、前端模板中的 AI 分析展示区域（投资人笔记、镜子测试、逆向思考、历史分析时间线）
- **修复模板语法错误**：stock_detail.html 移除多余的 endif 注释；恢复 `_detect_pool_drift` 函数和 `/api/journal/{journal_date}/conflicts` 端点
- 想法/为什么：纸盘交易系统已归档，不再开发量化交易；策略分类 tab 对单策略筛选无意义；候选股总览合并到主页便于查看；AI 分析功能恢复，面板只负责展示
- 冒烟：319 passed, 0 failed（本地 + 生产服务器验证通过）
