# Stock Dashboard — 班次交接 (Handoff)

> 账本 `iteration-log.md` 是真相源，本文件是下一班开工的 10 行速览。
> 每班收尾必须重写本文件（"最后状态"+"下一步方向"+"已知隐患"三段重写，"历史交接区"只追加不删），写完再 push + 简报。后一班只读它 + 账本当日条目就能接上。

## 最后状态（2026-10-23 母项目 v2.0.0 已发布；vendor v0.6.7 修复版已合并；价值投资方法论完善长线迭代中）

> **长线主线**：`docs/value-investing-methodology.md`（完善方案）+ `docs/tasklist.md`（TODO 进度）。Phase 0 已完成；当前进行：Phase 1 数据底座（P0-1 股息回填起）。
- **候选股颜色表达统一走红绿开关**：候选卡指标 `good/bad`、评分条/贡献/总分、`signal-avoid`，详情页评分贡献与信号/评级/镜子底色，观察池详情页在池状态/评分贡献/信号买入，全部由 `--green/--red`（及硬编码 rgba）改走 `--color-up / --color-down(-bg)`；中性档（AVOID 灰、HOLD 金）与状态类 UI（流水线状态、AI 消息 ok/err）保持不动。+5 回归用例。
- **CI 动作升级**：`actions/checkout@v6` / `actions/setup-python@v6`（清 Node20 废弃警告；v7 为最新主版，本次保守停在 v6）。
- **生产残留清理**：`/home/pi/sd-nightly` worktree（`nightly/20261021`）已删，Pi 上只剩 `main` + `sd-verify`；本地 Pi 侧仍有大量旧 nightly 分支（无害）。
- **下一步规划入档**：`docs/roadmap.md` 新增「下一阶段规划（2026-10-23 起）」，分 P1 验证收口 / P2 数据正确性 / P3 体验（须拍板），逐项带验收判据。
- **验证**：生产 worktree gate **773 passed**（基线 768→773，含新增 5 例；模板改动正确触发主服务重启）→ 合 main → 生产同步 → 健康检查第 1 轮 `active http:200 / active http:200`；`scripts/gate-baseline.txt` 已更新为 773。
- **修手动钉选卡信号文字恒黑**（`bf82acc` + `869fbf5`，基线 773→774）：信号值带 `value signal-*` 类，`.metric-cell .value`（0,2,0）压过 `.signal-*`（0,1,0）→ 买入/回避/持有/无 永远黑。规则提到 0,3,0；无信号改用 `signal-none`（中性灰）不再借用金色 HOLD；升 `watchlist.js` 缓存戳 `20260928c→20261023a`（否则浏览器缓存旧 JS 不生效）。生产浏览器实测：7 张钉选卡信号文字均为 `signal-none` 中性灰，BUY/AVOID 合成元素随开关翻色。
- **手动钉选卡指标同口径上色**（`03c995c` + `efb2641`，基线 774→777）：按好/中/差 → 红/金/绿（随涨跌开关）给指标上色，与候选卡同口径；`/api/watchlist` 从 `screening_result` 补全毛利率/净利率/OCF（这三列不在 `stock_snapshot`，缺失时保留快照值）；卡片补这三列让"差"档绿色出现；升缓存戳 `20261023b`。生产浏览器实测：7 张卡指标均带 good/warn 类并随开关翻色（CN good=红、US good=绿；合成 `bad` 档 CN=绿/US=红），新增三列已在 5 张卡出现。
- **个股详情页细节优化**（`9d827e7`，基线 777→783）：四项——(1) 快照条指标按好/中/差上红/金/绿（主详情 + 钉选股分析两页，随开关）；(2) 空值占位统一为 `--`（去掉 `--%` / `--亿`）；(3) 硬编码颜色主题化（grade-b 金底金字、signal-hold、K线/SVG 灰、提问按钮白字）；(4) 间距线框统一（区块间距 40→32px；六关卡片去外框线改浅灰底色块；行分隔虚线改实线浅灰）。浏览器实测：CN good=红 / US good=绿，缺失值 `--`，六关 `border 0px + bg #f0f1f3`，行线 solid。
- **验证**：生产 worktree gate **777 passed**（774 +3）；routes/JS/模板改动正确触发主服务重启；生产 4 路 200、双服务 active；CI 绿。
- **验证（详情页）**：生产 worktree gate **783 passed**（777 +6）；模板改动正确触发主服务重启；浏览器实测四项全部生效；`scripts/gate-baseline.txt` 已更新为 783。
- **vendor v0.6.7 暂缓**：已尝试 `subtree pull v0.6.7`，但 v0.6.7 的 `run()` 类用例不隔离新闻抓取（`llm_context` → `fetch_market_news` 打真实 akshare），在 Pi 上网络 stall 无超时 → 母门禁挂死。已回退 vendor 到 v0.6.5，并提 Issue [#1](https://github.com/oldfunk/paper-trading/issues/1)（域内 bug，母方不修）；等子方 tag 修复后再 vendor 验收。
- **DB 锁修复**（`95e42e7`）：`get_connection()` 加 `busy_timeout=5000` + 仅非 WAL 时设置 `journal_mode`；消除 `/api/indices` 轮询撞调度器写库时的 `database is locked`（近 2h 出现 2 次）。
- **vendor v0.6.7 修复版已合并**（`f8fa260`，tag 重指 `a0dd2ec`）：`fetch_market_news` 加硬超时（守护线程 + `agent.news_timeout_sec` 默认 15s）+ `tests/conftest.py` 全局离线打桩（Issue #1 已闭环）。因先前 revert 造成合并基线错位，改用 `read-tree --prefix=paper_trading/` 整树重读为 v0.6.7；契约核对通过（integration 零写母库、engine 默认关、CLI 兼容）。**gate 810 passed**（785 +25 vendor 用例）；`NEED_PARENT=1` 正确重启纸盘面板。
- **验证（本批）**：生产 worktree gate **785 passed**（783 +2 DB 用例）；`src/`（DB 修复）改动正确触发主服务重启；健康检查 `active http:200 / active http:200`；`scripts/gate-baseline.txt` 已更新为 785。
- **母项目 v2.0.0 已发布**：tag `v2.0.0`（annotated）+ GitHub Release（功能盘点：AI 分析复活 / 纸盘合流 / 专业化三件套 / UI 净化 / 流程自动化 / 数据与修复；门禁 785；部署三行；已知限制；MIT）。

## 下一步方向
1. （Phase 0）重新 `subtree pull v0.6.7`（tag 重指 `a0dd2ec` 修复版）+ 契约核对 + 生产 gate（预期不再挂死）
2. （Phase 1）P0-1 股息率/分红率回填（数据已有，783 → >3000）→ P0-2 分红历史 → P0-3/4 三表关键字段 → P0-5 历史价格 → P0-10 披露日期
3. （Phase 2）P0-6 可复现估值模型（核心）+ P0-7 同业分位
4. （Phase 3）P0-8 look-ahead-safe 回测 + P0-9 买入 MOS 预测力验证
5. （择要）E1 财务同环比高亮 / E2 搜索排序；C1 对齐子项目 M2；C2 观察 v0.6.5 runtime tick 日志

## 已知隐患
- **生产 SSH 对连续建连敏感**（新版 OpenSSH per-source penalty）：批量脚本必须限速；deploy.sh 已加 `ssh_do` 1s 间隔 + 健康检查单连接，但同类自建脚本仍需遵守，否则会复现「Connection reset」误报
- **CI 环境非生产等同**：ubuntu runner 与生产 Pi 不完全等同——CI 是记录与信号，权威门禁仍是生产 gate，CI 红不等于生产坏；本轮动作升到 v6 后需观察 CI 是否仍绿
- **回测前视风险**：`financial_history` 无披露日期，`scripts/backtest_topk.py` 仍坏（`no such column: code`）——见 roadmap P2
- **静态 JS 改后必须升版本戳**：`/static/watchlist.js` 在 `index.html` 用 `?v=YYYYMMDDx` 引用；改了 JS 不升戳，浏览器缓存旧文件、修复不生效（2026-10-23 signal-none 实证）。同理 `news.js`
- （继承）交易日历缓存新接缝：母树 `paper_trading/var/trading_calendar.json` 由 cron 每周一刷新，失效则回退"周末+硬编码"判断
- （继承）v0.6.5 弃用旧 schedule gate（部分入口 410）；`engine.enabled: false` 是安全线；独立克隆 `/home/pi/paper-trading` 两套 cron 并存（单交易员原则）
- （继承）股息/收益口径三条：个股 qfq 含分红 vs 基准价格指数不含分红（超额略偏乐观）；重叠窗口非独立样本；120 日窗口 2027-01 起才有数据

## 历史交接区（追加，不删）
- 2026-10-23 vendor v0.6.7 修复版合并（Issue #1 闭环；整树重读；gate 785→810；NEED_PARENT 验证）+ 价值投资方法论完善方案/TODO List 入档
- 2026-10-23 母项目 v2.0.0 发布（tag + GitHub Release）；vendor v0.6.7 暂缓 + DB 锁修复
- 2026-10-23 vendor v0.6.7 暂缓（run() 用例打真实 akshare 挂死 gate，回退 v0.6.5 + 提子项目 Issue #1）+ DB 锁修复；发版 v2.0.0
- 2026-10-23 个股详情页细节优化（快照条指标上色/空值占位统一/硬编码颜色主题化/间距线框统一；基线 777→783）
- 2026-10-23 手动钉选卡指标按好/中/差上红/金/绿（与候选卡同口径；/api/watchlist 补全毛利率/净利率/OCF；基线 774→777）
- 2026-10-23 修手动钉选卡信号文字恒黑（`.signal-*` 优先级被 `.metric-cell .value` 压过；无信号改中性灰 `signal-none`；升 watchlist.js 缓存戳；基线 773→774）
- 2026-10-23 候选股颜色全覆盖红绿开关 + CI 升 Node24 + 下一步规划入档 + 生产残留 worktree 清理（基线 768→773）
- 2026-10-23 对齐上轮审计（PR #3 术语表合入 `3240cec`；术语表口径修正；基线单一事实源补齐）+ deploy.sh SSH 限流修复并实弹复验（gate 768 / 137.33s；健康检查单连接）
- 2026-10-22 审计二轮落地：P1 deploy.sh 一键部署 + P2 基线自动强制 + P3 死脚本清理（CI 表述修正 / 协议补关 Issue 规则 / Issue #1 关闭）
- 2026-10-22 流程自动化审计 + CI 落地（主项目首个机器门禁记录；PR 红验证闭环；P1-P3 缓做）
- 2026-10-22 UI 净化 + vendor v0.6.5（评分拆解去深色线框；子项目跨两个大版本合流；基线 646→768）
- 2026-10-22 修三个 UI bug（评分拆解跳转/历史笔记 500/红绿开关不跟随；基线 638→646；股息流水线闭环 18/20）
- 2026-10-21 收益验证 + 真实股息 + 新闻事件上下文（基线 577→638；生产 780 行收益、3620 只分红，冒烟全 200）
- 2026-10-20 新闻双源（东财个股 + 财新市场）+ 后台轮询/去刷新/定速滚动（基线 567→577）
- 2026-10-19 新闻舆情首版：双次 LLM 调用、news_item/news_digest、滚动条+日报（基线 541→567）
- 2026-10-16 v0.4.0 vendor（AI 自主提名全市场；cron 改 auto；基线 541）
- 2026-10-16 K线按需构建 + 矛盾检测修复（基线 516→526；测试事故已处置）
- 2026-09-30 僵尸锁 hotfix（基线 512→516；待 backport 上游）
- 2026-09-30 每行执行按钮上线（例外跟 main；fire-trade 无视闸已披露；协议补版本条款）
- 2026-09-30 自动交易已开（母价值 16:45 tick；首真跑下个交易日）
- 2026-09-30 v0.3.0 vendor（定时动作队列；基线 501→512；定时交易仍待批）
- 2026-09-29 删两处防御从句（审稿分支部分采纳）
- 2026-09-29 README 子项目专区外链（引流）
- 2026-09-29 README 全文审计（徽章+过时5修+全验）
- 2026-09-29 截图精简 5→2（用户定稿）
- 2026-09-29 README 实机截图（5 张 live + Key 涂抹）
- 2026-09-29 公开后复扫（零泄漏；README 门面 4 修 + topics）
- 2026-09-29 公开 + v1.0.0 发出（子 Release 待子方；公开后规矩落档）
- 2026-09-29 SECURITY.md 补件（翻公开+Release 待拍板）
- 2026-09-29 v0.1.0 vendor 建基线（协议正式运转；待翻公开开关）
- 2026-09-29 协作机制落档（协议模板 + tag 制；待首 tag 建基线）
- 2026-09-29 公开就绪判定（realtime 对账闭环；vendored 故意落后 3 提交；待翻开关）
- 2026-09-29 公开必做项（MIT LICENSE + Quickstart；翻公开待子项目协商）
- 2026-09-29 flake 修好三分支全合（测试隔离+3 行；盘中三连绿；基线 486→501；子项目分支待审）
- 2026-09-29 手动 AI 复盘轮换（旧池硬规则全调出；新 5 只上线；journal 偏短如实记；黄框改动因上游盘中 flake 暂缓合）
- 2026-09-28 子项目更新合流（宇宙中文显示；回推修复被上游合入=审过闭环；生产 gate 486；等用户定下一步方向）
- 2026-09-28 定性纠偏 + allow_buy 补漏（切换 403 定性非 bug；scheme list 真 bug 补完；生产 gate 486；子项目分支已更新）
- 2026-09-28 修子项目 bug + agent 真测（Scheme.source + 切换接管；推分支给子项目审；母策略 AI 决策跑通；生产 gate 486）
- 2026-09-28 子项目 8 提交合流已合（母策略参照/方案三分法/盘中实时/`cli.py` 改名；生产 gate 484；母策略 dry-run + llm ask 全通；`scheme list` 上游 bug 已报）
- 2026-09-28 纸盘 subtree 机制化已合（plain→subtree graft + 上游 3 提交 + 双向独立验证 + 文档全量；生产 gate 473；以后更新一条 pull）
- 2026-09-28 纸盘全量合并已合（整体迁入顶层 + 原生包删除 + /paper 改直达 :8081；生产 gate 469→470；pool screening/Key/MA/agent dry/llm ask 全通；:8081 面板已起；真 agent 决策待收盘）
- 2026-09-28 双 bug 修复已合（nightly/20260928c → main：KlineDAO 恢复 + 钉选卡跳转 + 静态版本戳；生产 gate 440；K 线表停更 7 天，明早流水线自动追平）
- 2026-09-28 AI 队列并发+UI 重组已合（nightly/20260928a → main：生产 gate 437；排队/N 并发/主页常驻/润色/大盘解盘；队列内存态重启丢任务已知）
- 2026-09-27 首只真分析跑通 + 0927e 已合（DAO 排除复盘轮；盐湖股份 once 1/1，用量 3337/5103；用户自改对模型名 glm-4.5-flash；生产 gate 408）
- 2026-09-27 用户报障修坑+卡片按钮+直接提问已合（nightly/20260927d → main=577b8e0：生产 gate 405；报障真因系 UX 坑非代码错）
- 2026-09-27 /llm 页参数收折叠（temperature/max_tokens/间隔默认+高级设置；docs 随 nightly/20260927c 合并）
- 2026-09-27 LLM 自带 Key 已合已同步（nightly/20260927a → main=45be212：生产 gate 396 + 重启三路 200 + /llm 页 live；用户长期授权直接合并/推送/生产调试，不再逐次请示）
- 2026-09-27 LLM 自带 Key 分析复活待合（nightly/20260927a：OpenAI-compatible + /llm 设置页 + 三触发 + 用量显示，生产 worktree gate 396；提示词/压缩不重建；端到端待用户 Key）
- 2026-09-23 deep_research 废表已删（用户批准"脏数据别留"；5 行备份后 DROP，表已不存在；08-31"表不存在"系误记，今日才真删；docs 随 nightly/20260923b 合并）
- 2026-09-23 维护模式收尾已合（nightly/20260923a → main：AI 零触发审计 + 备份清理约 480M + 文档冻结；停止投入，转维护模式）
- 2026-09-23 P0-2 修复已合已同步（nightly/20260922e → main=c6175d1：生产 gate 367 + 重启三路 200；用户授权由 agent 把关合并）
- 2026-09-22 四项拍板落地已合已同步（nightly/20260922d → main=1f0427c：生产 gate 360 + 重启 200 + sector 回填 2594 行 + 页面 16 行行业均值；同日 0922c/0922d 双合）
- 2026-09-22 四项拍板落地待合（nightly/20260922d：残留④ + 测试规则落档 + S8 新浪行业回填，生产 worktree gate 360 + live 2999 只映射 + 缓存落盘；同日 0922c 已合入 main=2f0384e）
- 2026-09-22 开发者身份清除待合（nightly/20260922c：23 文件占位符化 + deploy.local.md 本地化，本地与生产 worktree gate 均 348，身份扫描 0 命中）
- 2026-09-22 P1② 行业均值收口 + C3 取消已合已同步（远端 nightly/20260922f 已删；生产 pull + gate 348 + http 200；同日晚些 P0-1 重启令新代码全路由生效）
- 2026-09-22 误入内容清除 + 职责定位已合已同步（`e954b29`→`2ff32b1`，gate 342 全绿；AGENTS.md:53 经用户批准于同日补修，设备名全仓归零）
- 2026-09-22 Agent 接入指南已合已同步（`1499691`：agent-api.md + README 挂链，gate 342 全绿）
- 2026-09-22 ABC 方案已合已同步（`bb64119`：反面检验/论文追踪/复盘三问内化 + 架构方法论固化 + 解绑上游镜像与对照工具，gate 342 全绿）
- 2026-09-22 残留清理已合已同步（nightly/20260922b：hermes_proxy 空目录 + scheduler 纸盘注释尸体，生产服务器 328 全绿）
- 2026-09-22 残留清理待合（nightly/20260922b：hermes_proxy 空目录 + scheduler 纸盘注释尸体）
- 2026-09-22 文档复检修正已合已同步（9 处写错：AGENTS 工作流/基线 317/归档表述/代理契约，纯文档未重启）
- 2026-09-21 nightly/20260921b 已合已部署（分析师整改 T1–T4 + 去 kanban，生产 gate 328 全绿）
- 2026-09-21 分析师整改 T1+T2 待合（nightly/20260921b：数据质量标注/估值自选/纸盘 schema 删除/笔记 model 列，生产服务器 323 全绿）
- 2026-09-21 nightly/20260921 已合已部署（去 AI 内联 + ai_proxy 改名 + 文档清包袱，生产服务器 gate 317 全绿）
- 2026-09-21 去 AI 内联 + 去 Hermes 化待合（nightly/20260921：卡片去 AI/停本地触发/ai_proxy 改名/文档清包袱）
- 2026-09-18 文档复检：修复 handoff 残留冲突标记（21615d2 合并遗留）+ AGENTS.md 校订（基线 317/运行命令/交接段）+ README 校正（run-once→run、基线 313→317），分支 nightly/20260918 待合
- 2026-09-17 收尾（AGENTS.md 入 main + README 如实化 + 删 0917/0917b + 0917c 已合）
- 2026-09-17 备用通道 nightly/20260917c（Zen 全灭实证 + Pollinations 兜底 + 生产冒烟通过）
- 2026-09-17 AI 修复+UI 收敛已部署（ling 优先/Retry-After/观察池卡片统一，生产服务器 307 全过；发现 403 需 Zen Key）
- 2026-09-17 nightly/20260917 已合已部署（/paper 面板上线 + B6/B7，生产服务器 297 全过，删废分支0916）
- 2026-09-16 M4a 纸盘引擎：撮合+信号编排+scheduler 触发（37 单测，main 已合，生产服务器已同步）
- 2026-09-15 README 全面重写（项目结构/贡献流程/配置表）+ handoff 状态更新
- 2026-09-15 nightly/20260914 合并到 main（9 commits, +1463/-76, 22 files）
- 2026-09-15 账本对齐 P0#1-3/P1#5-6 标完成（纯文档，402cd5e/293008d/6256d58/44959c 注记 + Changelog）
- 2026-09-14 M2 多策略后端 + AI 摘要前置合并（nightly/20260914 + nightly/20260913 → main）
- 2026-09-14 M2 多策略筛选后端核心落地 + verify（strategy_tags + multi 开关 + 5 单测，全仓 229 passed）
- 2026-09-12 架构治理 + README 重排（architecture.md + paper-trading.md + roadmap 升级 + README 重构）
- 2026-09-11 C2 东财第二财务源 P0（全仓 218 passed）
- 2026-09-09 建交接文件，三任务串联启动
- 2026-09-09 白班试跑：豁免D夹具修复落袋（577cc83）
