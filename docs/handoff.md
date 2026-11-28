# Stock Dashboard — 班次交接 (Handoff)

> 账本 `iteration-log.md` 是真相源，本文件是下一班开工的 10 行速览。
> 文档分类（真相源 / 规程 / 设计 / 快照报告）见 `docs/README.md`——引用报告里的数字前先确认它是哪一类。
> 每班收尾必须重写本文件（"最后状态"+"下一步方向"+"已知隐患"三段重写，"历史交接区"只追加不删），写完再 push + 简报。后一班只读它 + 账本当日条目就能接上。

## 最后状态（2026-11-28 下午：F0 三份真实笔记；E2/B3/F1 代码就绪）

- **F0 收盘检阅完成（提前）**：11-25/26/27 三份真实新闻笔记已发布到 `ai_journal`（`note_type=close_review`）；引用 14/11/18 条、无效引用 0、买卖建议 0；NAV/成交按笔记日期截断。`/news` 页与首页"收盘检阅"卡可读。
- **F1 代码就绪**：15:45 首跑 / 18:30 重试 / **20:55 面板 asof 到当天时刷新当日笔记**（不覆盖人工批注）；新闻源扩到财联社/东财快讯；P0/P1 规则事件（30 分钟冷却、P0≤5/P1≤10）只落库提醒，不交易；P2 市场级阈值未做。
- **组合变量接实际收益**：收盘检阅优先 GET :8081 `/api/performance|overview|positions`，写入 total_return / 已实现盈亏 / 费用 / 胜率 / 最大回撤 / 暴露 / 今日盈亏 / 逐仓未实现；失败回退 E0 桥。11-28 dry-run 已让模型在第 4 节写出"截至 2026-11-27，累计 -2.87%，已实现 -2859.54，费用 40.68，MDD 4.17%，暴露 15.16%"。新增 `/api/paper/performance`。
- **E2/B3 完成**：`paper_execution_quality.py` 逐笔 VWAP/区间/收盘偏离（7 笔，2 笔在当日高低点外，记录子项目按昨收撮合）；`paper_weekly_audit.py` 11-20→11-27 兑现率 100%、费用 30.47 元、换手 18.36%。
- **E3 绩效页完成**：`/performance` + `/api/performance`：纸盘 vs 沪深300（共有交易日、累计超额），AI 信号前向收益按 BUY/HOLD/AVOID 汇总；已按子项目 issue #12 修正为**只统计所选基准、按 analysis_id 去重**（生产 570 条/60 日 264 条，不再是 1140/528）。
- **子项目 finding 已处理**：`oldfunk/stock-dashboard#12`（`/api/performance` analysis×benchmark 双计）修复并加回归；子项目只读、未改母码。
- **F1.5/F5 补齐**：市场级 P2/P0 事件（指数单日 ±3%，≥5% 升 P0；成交额 ±50%）已并入收盘检阅事件；`scripts/news_review_eval.py` 可查章节/引用/建议/面板 asof，带人工标注时算 precision/recall，`--check` 硬失败 exit 1；标注模板见 `docs/news-review-annotations-template.json`。
- **F2 周报**：`src/news_review/weekly.py` + `news_weekly` + 周六 09:30 调度 + `/api/news/weekly` + `/news` 周报区；本周（11-23→11-27）已生成，日间重复主题/事件/组合实际收益/下周关注/来源。
- **E4 分析上下文**：20 只单股分析 prompt 现在带只读组合上下文（实际收益/持仓/待执行计划，面板优先、桥回退、300s 缓存），并明确"不得据此给交易建议"。
- **R3 策略/归因**：`S-BASE-v0` 已登记（7 门+五维权重）；CLI `scripts/research_strategy.py` 支持 new/promote/retire/deploy/attribute/drift；部署 `D-20261128-001`（100,000，2026-09-28 起）；11-20→11-27 归因已落库（总收益 +1.31%、超额 -1.43%、费用 30.47、regime risk_off）；漂移写 `DL-20261128-001`。
- **数据质量与巡检**：`scripts/data_quality_report.py` 查出 2026-10 分析记录两边都缺、market_state `pe_percentile_5y` 近 10 日全缺、daily_close 近 10 日 ≥98%；`scripts/check_news_review.py` 可做收盘后硬检查。
- **收尾路线定稿**：`docs/value-project-roadmap.md`（Q0 稳定验收 → Q1 数据 → Q2 研究 → Q3 纸盘纪律 → Q4 产品 → Q5 接管设计）；真实账户接管不在本轮开工，终局目标不变。
- **数据决策（11-28）**：2026-10 分析缺口**不重建**（无原始文本，重建非 PIT，按“缺就缺”处理）；`pe_percentile_5y` 已按“5 年年报 EPS/PIT 全市场 PE 中位数”实现并回填，11-27 分位 **62.9%**、覆盖 **98.7%**；13 只活跃股无 K 线先归因/白名单，不硬凑 100%。
- **纸盘/数据**：NAV 97,125.32（-2.87%）；A2 目标池 20/20 当日；基准 5/5 到 11-27；三端口 200。
- 门禁母 **1609** / vendor **1107**；基线已更新；母套件超时下限 900s。
- **vendor v0.12.0 已合流（2026-11-28 晚）**：远端 main 的 `paper-trading v0.12.0`（squash 032b896..0091b9b）合入 nightly，无冲突；vendor 用例 996 → **1107**。v0.12.0 的 `test_audit_passes_local_secrets` 要求 secrets 0600，gate 仅对 vendor 套件用 umask 077，vendor 1107 全绿；母侧功能不受影响。:8081 生产进程仍归子项目，母方未重启/未改其配置。

> 以下为 11-24 及更早的历史状态，保留原文（只追加不删）；最新状态以上面为准。

- **vendor v0.10.1（2026-11-24）**：`git subtree pull --prefix=paper_trading paper-upstream v0.10.1 --squash`（单向 tag 流，无冲突），vendor 树与上游逐字节一致；vendor 用例 993 → **996**（基线已更新）。
  - **用户报障修复**：:8081 控制台切换投资方案反复索要 API Key。根因是第二实例与 :8080 不同 origin，localStorage 的 admin_token 不共享；内置方案切换只写 gitignored 的 `strategy.local.yaml`，本不该要求口令（实测我们的 secrets **有** provider Key，但 UI 仍走接管流程，体验上等于死循环）。现在内置方案（mother/general）且不改自定义指令时免口令；`custom` 或改指令仍需口令（指令会进 prompt）。
  - **顺带修复**：旧线实例没有 `rq_intents` 表，`/api/intents` 曾 500；现返回空列表 + `unavailable`。
  - 上游全量 **996 passed / 13 skipped**；生产 gate 与 :8081 冒烟在 deploy.sh 中执行。
  - **生产验证（2026-11-24 凌晨）**：gate 母 **1522** / vendor **996**；:8081 重启后 `/api/intents` **200**（空列表 + `unavailable`）、`POST /api/schemes/active {"name":"mother"}` 免口令 **200**、三端口 200。上游 v0.10.1 **只有 tag、没有 GitHub Release**（由子项目自行决定是否发布）。

- **子项目 v0.10.0 合流（2026-11-23；发布为母方越权，已纠正）**：
  - `git subtree pull ... paper-upstream main`（16+ 提交）→ 冲突 3 个文件（`agent/loop.py`、`agent/mother_strategy.py`、`tests/test_mother_strategy.py`；上游已合入 PR #6，以 theirs 解决）；vendor **993 passed / 13 skipped**。
  - 母方**越权**代改 `__version__`、打 tag `v0.10.0` + GitHub Release；2026-11-24 用户纠正，发布权归子方。子项目随后自行发 **v0.10.1**（仅 tag、无 Release）。
  - 内容：母策略闸门/AVOID 强制离场（PR #6）、**T96 计划新鲜度**（逐只覆盖率、持仓必须当日，修 #5）、卖→买→hold 执行顺序、面板 P1/P2（收益曲线/今日分时/FIFO 已实现盈亏/CSV）、daily-check 交易动作终态门禁。
  - **交互注意**：T96 要求持仓当日 K 线；母侧 16:30 计划依赖 `sync_data` 的本地当日 bar，若源未发布会 `no-fresh-data` 重试（17:00 二次刷新是 A2，待做）。

- **11-23（周一）收盘复核**：
  - **plan #19 执行**：买 **603979 金诚信 100@43.98**（母策略买入区间 40-45），佣金 5.00；持有 000792 400、000426 100。
  - **NAV 11-23 = 96,363.32（-3.64%）**：现金 81,186.32 + 400×21.53 + 100×19.65 + 100×46.00。
  - **plan #20（11-24）已生成**：卖 **000426 100**（母策略 AVOID，代码层强制离场）、持有 000792/603979。
  - 基准：`sh000300`/`000001`/`399001`/`399006` 到 **11-23**；`sh000922` 仍 11-20（来源滞后，标 stale）。当日纸盘 **+0.52%** vs 沪深300 **+0.09%**（日超额 +0.42pp）。
  - 流水线 run **870** `completed` 20/20（AI 17:15 完成）；kline 轮换 435/600、当日 K 线 0（腾讯晚发，次日自愈）；market_state pass。
- **A1b/A5b 兜底上线（cron 5 → 7 条）**：16:30 NAV 因 15:50 raw 未发布跳过（`day_no_today_bar=3`），新增 **20:50 `nav_close_mother.py`**（幂等补写）；16:04 指数首刷 0 行（东财断连 + 新浪未发布），新增 **20:45 `refresh_benchmarks.py`**。两条均已手动验证，明日观察自动执行。

- **修复工序开工（A1 + E0 + Issue #10）**：
  - **A1**：11-20 收盘 NAV 已复算并校正时间戳——`nav_history` id=13，total **95,867.37**（现金 85,589.37 + 持仓 10,278），`op_log[nav:close]` 留痕；根因（`daily_bars_raw` 为空）用 `scripts/fetch_mother_raw.sh` + **15:50/20:40** 两条 cron 补上（母侧 cron 由 3 条变 **5 条**）。周一 16:30 观察自动写入，连续 3 日达标才算 A1 关闭。
  - **E0**：新增 `src/paper_bridge/`（只读桥底座）——`mode=ro`+`query_only`、显式合并树路径（不自动发现 :8080）、缺库/缺列/被锁一律 `unavailable` 降级；8 条夹具测试；gate 母 **1513→1521**。
  - **vendor 计数说明**：周六凌晨 gate 的 vendor **914/918** 是已知墙钟 flake（`test_minute_supply_t59` 2 例 + `test_bar_source` 2 例；后者 fixture 的 `_weekday(-1)` 在周六会回退成与 `_weekday(0)` 同一天）。非回归，工作日复跑应回 918。
  - **Issue #10（子项目数据契约请求）已处理**：`benchmark_daily` 原仅周六刷新、实测 stale 约一周；调度器改为**每交易日流水线后** `refresh_benchmarks`（东财优先/新浪回退，失败只告警）。契约裸码 `000001/399001/399006` 提为 `CONTRACT_INDEX_SOURCES` 并补测试；文档锁定两套键、价格指数不复权、15:30 后可用的口径。`stock_snapshot.sector` 契约写入表清单（缺省 NULL 不回退）。门禁 **1521→1522**。

- **11-19 运行日复核（22:13）**：母项目 run **867** `completed`（15:30:17–15:42:00；5527 → 817 候选 → 20 只，ROE 覆盖 816/817 过闸）；AI 分析 **20/20** 成功（glm-4.5-flash，15:47–17:06），数字抽检 13 只命中（约 0.65 处/只）、自洽检查 1 例（002648 moat≤2 给 BUY）；kline 轮换补 **395/600**（1502s 预算用尽）、**当日 K 线仅 5 行**（腾讯 bar 晚发布，次日自愈），`daily_close` 当日 5527 全；`market_state` 11-19 快照已落。三服务 200、近 3h 零 ERROR。
- **母方案策略纠正（vendored 热修，已部署 :8081；上游 [paper-trading#6](https://github.com/oldfunk/paper-trading/pull/6)）**：发现 plan #15 买入 **601899**，而母项目自己的分析是 **HOLD、买入区间 8-12、现价 23.88**——根因是 `screening_detail()` 根本没查 `ai_trade_strategy`（上下文看不到母策略），参考信号仍是 MA5/20。修复：母方案参考信号换成母策略（signal/buy_zone/stop/评分）+ `MOTHER_RULES`；`_execute_actions` 加**买入硬闸门**（缺分析/过期/非 BUY/超区间即拒，拒因进 `ai:decide`，fail-closed）。
  - 验证：32 条单测；隔离副本复演 → 旧 plan #15 的买入被"母策略信号=HOLD"拒绝、AVOID 持仓 002192 被强制卖出；**新 plan #18**（卖 002192 + 买 000426）两笔都成交（卖 200@43.15、买 100@19.82，T+1/费用正确）；vendor gate **918**（基线 886→918）。
  - **计划已按母策略重生成**：11-19 23:45 用 `agent tick --force` 生成 **plan #18**（`pending`，11-20）；#15/#16/#17 均 `superseded`；计划 JSON 含 `actions_rejected` 与 AVOID 强制卖出，明天 09:30 执行（`check_paper_agent.py` 绿）。
  - **新发现（未修，记录在案）**：母侧 `nav_history` 从 10-22 起没有收盘 NAV——`_snapshot_close_nav` 要求当日 **raw** 收盘，而母侧 `data.db.daily_bars_raw` 为空（raw 由独立版的 `data fetch-raw` cron 供），`daily_close` 虽有当日不复权收盘但没有 integration 接口。要么补 raw 同步，要么加 `daily_close` 契约；**不能拿 qfq 价冒充 raw**。
- **母侧纸盘第一份真实计划（#15）**：16:31 首跳撞 zhipu **429（code 1302）**、16:36 重试成功；`plan_date=2026-11-20`、`asof=2026-11-19`、20 只标的，动作 = **买 601899 紫金矿业 100 股** + 持有盐湖/融捷。体检脚本绿；明天 09:30–10:00 执行。
  - **新发现（已提上游 [#5](https://github.com/oldfunk/paper-trading/issues/5)）**：`asof` 取所有标的的 **max**，20 只里只有 3 只有 11-19 K 线，买入标的 601899 的末根是 11-18——决策实际用的是昨天收盘，计划却标记为今天。执行侧只有 30% 偏离闸，防不住"差一天"。

- **母侧子项目（:8081）AI 交易员断档三周，已恢复（`751bb7f`）**：用户观察"10 月底后没跑过计划和交易"，取证确认——`agent_plans` 最后一条 **10-26 一直 pending**、`op_log` 最后一条 **10-23 16:47**、`fills` 停在 10-08；nec crontab 里只剩独立版 :8080 的任务。**根因**：母侧运行时配置只存在于 crontab，10-23 后一次重写把三条任务全丢了，面板仍 200 所以完全静默。
  - 修复：`scripts/install_mother_agent_cron.sh` 把三条任务（当时）写进仓库（幂等安装 + `--check`），已在 nec 安装并实测"删一条→check 报缺→install 补回→check 通过"；后扩为 7 条（raw/NAV/指数兜底）。
  - 路径/口径：CWD=`paper_trading/`、母 `.venv`、`--scheme mother --pool-from screening --pool-limit 15`；`calendar sync` 已跑（母库日历覆盖到 2026-12-31）；LLM 连通性实测（`llm models` 与一次 `llm ask` 均 ok）。
  - **回流上游**：同一套经验整理成通用版 `deploy/install-instance-cron.sh` + `docs/second-instance.md`，已提 [paper-trading#4](https://github.com/oldfunk/paper-trading/pull/4)（只加部署/文档、不改运行时；用假 crontab 验证了 check→install→check→幂等）。
  - **已观察到**：11-19 16:36 生成 plan #15（见上）；明日 09:30–10:00 执行（买入会被新闸门拒绝）。验收命令见 `docs/architecture.md` §9.7。

- **母侧纸盘两个窗口已用隔离库端到端预演（2026-11-19 凌晨）**：生产账本 `sqlite .backup` 到 `/tmp/pt-sim`，直接调用 `AgentTrader.tick` 注入时刻，不碰生产：
  - 计划窗口（模拟 11-19 16:45，`force` 越过"定稿到今天"检查）：**LLM 真实调用**，plan **#15** 落库、`plan_date=2026-11-20`、`asof=2026-11-18`，约 2 分钟。
  - 执行窗口（造 11-19 pending 计划，模拟 09:35）：`BUY 100 000792 @21.590` 成交，佣金 **5.00**（地板）、过户费 0.0216、印花税 0；持仓 400→500、**可用仍 400**（T+1）；现金 78955.79→76791.77。
  - 结论：计划/执行链路的代码路径已验证；真实 cron 的两个窗口仍需到点复核（用下面的体检命令）。
- **新增 `scripts/check_paper_agent.py`（一次性体检，不是守护进程）**：cron 三条是否齐全 + 收盘窗口后有没有下一交易日计划 + 开盘窗口后今天的计划是否 `done`；休市日只查 cron。只读账本、不调 LLM、不下单。实测：实际时刻/09:35 绿；`--now 10:30` 红（"今天没有任何计划"）、`--now 18:50` 红（"没有 2026-11-20 的计划"）——正好是本次停摆的形状。
- **`audit_no_data` 按任务语义归类（`ccbde24`，main）**：生产库 3209 条 no_data 里 **3193 条 unattributed** 全部是"有行情也有财务"，按形态永远分不出来。改为看 `backfill_progress.task`：
  - `price_depth` + 有行情 → **`no_early_history`**（2911 条）：早段窗口取不到更早行情（上市晚于目标起点/源无早段），限流会记 partial 重试。
  - 财务三任务（`em_deep_gap`/`em_full_gap`/`interest_coverage_backfill`）+ 有行情有财务 → **`financial_partial`**（282 条）：目标字段上游不提供。
  - 未登记任务仍 `unattributed` → **exit 1 不变**（fail-closed）。
  - 验证：真实库 **unattributed 0 / rc=0**（3209 = 2911 + 282 + 10 delisted + 6 ambiguous）；单测 29 passed；当批 gate 母 **1502 passed**（基线 1495→1502；当前基线见 `scripts/gate-baseline*.txt`：母 1522 / vendor 996）。
- **muse 分支审查（`origin/muse/stock-dashboard-fixes`，2 commit，均未合）**：
  - `fe4bfbd`（加"有行情缺财务"桶）**不改**：真实库 **0 命中**，退出码仍 1；它引用的"192 只"是 `coverage_gaps()` 里批10-c 的跨库缺口数（今天为 0），不是 no_data 计数。
  - `58ac76d`（`TradingBridge` CWD 错位拒绝建空库）**逻辑有效**（实测旧代码静默建 61KB 空库、新代码报错不落盘、正确 CWD 与全新安装不受影响），但改的是 vendored `paper_trading/`，按协议只能走上游 issue + tag；当前 :8081 用绝对路径、:8080 CWD 正确、cron 均 `cd` 在先，无现网暴露。
  - 附带实证：配置读失败时 `TradingBridge` **静默回退默认参数**（cash 100000 → 1000000），比空库更危险，待上游评估。
  - **收尾**：两条发现转上游 issue [paper-trading#3](https://github.com/oldfunk/paper-trading/issues/3)（含实测输出与提案补丁）；审查分支 `muse/stock-dashboard-fixes` 已删除，代码 0 行合入。

- **门禁加固（`5ef42f8`，已在 main 与 nec 双树）**：`scripts/gate.sh` 装上 `pytest-timeout` 后，母/vendor 套件都带 `--timeout=180`（`GATE_TEST_TIMEOUT` 可调），**挂起会报出用例名**而不是只报"卡在 96%"；插件缺失自动降级为整批 600s 兜底并提示。`pyproject.toml` 新增 `[project.optional-dependencies].dev`（pytest / xdist / timeout）记录测试期依赖，**不进运行时**。生产 venv 已装 pytest-timeout 2.4.0（pytest 9.2.0 不变）。
  - 验证：挂起样例 `--timeout=2` → 2.04s 失败并点名用例；`/home/nec/sd-verify` @ `5ef42f8` 全量 gate rc=0：母 **1495 passed / 369.34s**、vendor **886 passed / 13 skipped / 154.07s**，合计 523s。
  - 教训（写进脚本注释）：11-17 那次"超时"无法归因，本次改动把归因成本从"重跑整批"降到"看一行失败信息"。

- **v2.2.0 已发布**：在 v2.1.0 基础上按小版本号进位（tag `v2.2.0` + GitHub Release）。内容 = 子项目 vendor v0.9.0 合流 + 母侧子项目面板可用/可下单的端到端验证。
- **母侧子项目（:8081 合并树）可用与下单验证（隔离库 + 线上写路径）**：
  - 面板根页与全部读接口 200（`/api/status`/`engine`/`positions`/`cond`/`schemes`/`quotes`）。
  - 线上 :8081 实际挂条件单 → `armed=1` → 撤销 → `cancelled`，无口令写操作 **403**（口令门禁生效）。
  - 隔离库（复制 `data.db`/`paper_account.db`，不动生产账本）跑 CLI 真实成交：买 100 股 000792 @20.01 成交（佣金 5.00 地板、过户费 0.02、印花税 0）；持仓 400→500，**可用仍 400**（T+1 冻结生效）；同日卖 500 被风控拒（`Insufficient available volume`），卖 400 成交；面板 `/api/status` 同步反映现金 78955.79→76949.77。
  - 结论：母项目自己的子项目部分（面板读写、条件单、真实撮合/T+1/费用）可正常使用和下单。

- **v2.1.0 已发布**：annotated tag `v2.1.0` + GitHub Release（Latest），main=`e9b39f2`。发版物：版本号单一来源 + 页脚显示 + 数字抽检修复 + vendor v0.9.0。
- **15:30 首跑验证通过（新参数首跑）**：`run_log` id=865 `completed`、`analyzed_count=20`；20 条分析 15:47–16:33 全部 `glm-4.5-flash`、0 失败（串行 1 并发，单只约 2–3 分钟，上游仍拥堵）。数字抽检新口径下偏离 **10 处 / 20 只 = 0.5 处/只**（旧抽检器约 1.0–2.0）。kline：活跃池 23/23、全市场轮换补 600/600（400s）。
- **子项目 vendored v0.8.0 → v0.9.0（`subtree pull --squash`，零冲突）**：53 文件 +5789/−87，无依赖/schema 变更；vendor 用例 **886 passed / 0 failed**（此前 3 个既存失败已由上游修好，`gate-baseline-vendor.txt` 772→886）。:8081（母侧合并树面板）已按新代码重启验证。
- **AI 模型**：glm-4.5-flash（→4.7-flash）。设置：max_tokens **98304**（API 上限，131072 被拒）、**thinking 显式开启**、json_mode 开、并发 1、rpm_limit 40。数字抽检器已修（41% 误报）。
- **部署纪律**：nec 的 `git pull` 必须核对 `git log -1`（曾因本地 scp 改动静默失败，部分部署未生效）；推送只从本地发起。
- **qfq 重锚产线首验通过（批20，11-13 提前实测，不等周六）**：dry-run（锚点 11-11、目标 60、多锚 24）→ 真跑 60 只全部重锚（0 partial、写 3600 行、191s）。**验收全过**：多锚票数 **24 → 0**；进度表 60 只 covered=2026-11-11；抽样 002582 窗口内 scale 一致（台阶消除）；区间收益率 10-29→10-30 = **9.96%** 与真实一致（修前虚高 1.68pp）。
- **深层周刷新产线首跑 + EM 段判据修复（今日，`858679c`）**：ic 段完成（4,747 done / 27,995 行）；EM 段 27 只全部 no_data、写 0 行——真根因：EM 段判据"ic 或 fcf 任一缺"，而 ic 对非金融股恒空（批19 第三层根因连带）→ 每周重复白拉。**修复**：`EM_WEEKLY_FIELDS=("fcf",)`，周六 EM 段只对 fcf 负责，ic 归 ic 段（mainfin）。**EM 路径本身实证生效**：27 只 fcf 26/26 已填、09-30 fcf 覆盖 115→216；覆盖率低（~25%）是处理慢（40 只/周），不是失效。**待生产重启生效**（改完时在 15:30–19:00 禁重启窗口内）。
- **批21（研究页）在途**（Qoder，Issue #9 派工帖 `6846953457`）：路由+模板（实验列表/详情）、分域桶显示 n、新旧 splits 兼容、ci_rule/敏感性展示；纯展示层，`src/research/` 不动，基线 `origin/main@2c5168e`。
- **批18-a 前置阻断已解除**（裁定 2 `2c5168e` 修复后），待评估/派工。
- **Qoder 连带跟进项两个**：kline 新鲜度杠杆（daily_close 定期合并进 kline_daily）、`mark_backfill_done(covered_report_date=None)` 回落年报日的语义碰撞。
- **分析写入方已恢复**（2026-11-16 修复）：本地 AI 分析自动触发已恢复（调度器 + `.env` 加载）；11-17 首跑 20/20。
- **生产状态（11-17 复核）**：三服务（:9527/:8081/:8080）全 200、近 24h 零 ERROR、流水线每交易日 completed；ic 覆盖率 41.4%（62,483/151,038 行）。

## 下一步方向（按此顺序，一次一步，每步带生产证据）

> **开发路线见 `docs/roadmap.md`「2026-11-24 起」与 `docs/value-project-roadmap.md`（Q0–Q5）**；P0 详细工序见 `docs/remediation-20261121.md`。

1. **Q0 稳定验收（11/29–12/05）**：A2 三日 freshness、F1 五日自动检阅、B1 启动、交易日预检脚本/调度触发测试；F1 先不改阈值。
2. **Q1 数据可信（12/01–12/19）**：13 只无 K 线、308 只无 `financial_summary`、`pe_percentile_5y`、2026-10 分析缺口、B3 全市场复核。
3. **Q2 分析与研究（12/08–12/26）**：F5 标注集、R4/R5、H-001/H-002、R6 一条完整链。
4. **Q3 纸盘纪律（12/15–01/09）**：C0 dry-run、执行质量、风险预算、B1 收官；仍不接真实账户。
5. **Q4 产品收尾（12/22–01/23）**：日报/周报/巡检/备份、v2.3/v2.4。
6. **Q5 接管设计（2027-01 起）**：只写门槛与评估方法，不实现真实下单；第二个子项目/分支届时另立项。
7. **批21 验收 / 批18-a 派工 / Qoder 两个连带项**（与主线无冲突时做）。

## 已知隐患

- ~~生产树 11-05 的旧应用残留~~：**2026-11-19 已清出**——`main.py`/`src/app.py`/`src/database.py`/`templates/`/`.venvdeb/`/`nightly-report.md`/`paper_trading/data.db.pre-t35.bak` 全部 `mv` 到 `/home/nec/attic/20261119-old-hermes/`，附 `MANIFEST.txt`（sha256 + 原路径，可逆）。移动前确认：无进程占用、全仓零引用、根 `templates/` 不是 `src/web/templates`。生产树现在 `git status` 干净。
- ~~`sse-starlette` 约束与 venv 漂移~~：**2026-11-19 已解决**——全仓（src/tests/scripts/vendor）零引用，已从 `pyproject.toml` 删除；生产 venv 里已装的 3.6.0 不动（没有代码引用）。`pip install -e .` 不会再因 `<3` 约束降级它。
- **母侧纸盘的运行时配置曾只存在于 crontab（2026-11-19 实证）**：10-23 之后三条任务被一次 crontab 重写丢失，面板照常 200、计划与交易静默停摆三周，无任何告警。现已把任务（当前 **9 条**：交易 3 + raw 2 + NAV/指数兜底 2 + A2 二次 K 线 2）写进 `scripts/install_mother_agent_cron.sh`（幂等 + `--check`）；**任何改 crontab 的操作之后都要跑一次 `--check`**，并注意母侧与独立版 :8080 的 cron 行不能互相覆盖。母侧目前**没有**看门狗能发现"tick 停了"，只能靠 `logs/agent.log` 与 `agent_plans` 日期人工看。
- **母侧 `nav_history` 10-23 → 11-19 断档是既成事实**：20:50 `nav_close_mother.py` 上线后 11-20 起恢复每日写入（B2 绩效表 11-20 之后连续）。断档段**不插值、不编造**，`paper_performance.py` 按 NAV 实际日期与基准取交集；引用 09-28 → 11-27 累计收益时要记得中间少了一段交易日。
- **计划新鲜度被 `asof=max` 掩盖（2026-11-19 实录，上游 #5）**：v0.10.1 的 T96 已改为逐只覆盖率、持仓必须当日；母侧 17:05/17:35 二次拉取作为源晚发布兜底。**尚未连续 3 日验收**，不要把它当成"数据已经是最新"的证据。
- **全市场 `kline_daily` 覆盖只有约 13% 是设计，不是故障（2026-11-28 实测）**：11-27 为 **715/5527**；轮换池每天只补一部分，`daily_close` 才是全市场收盘覆盖（**5527/5527**）。判断"当日 K 线够不够"看 **持仓 + 候选池**（A3 报告，11-27 为 20/20），不要拿全市场 715 当异常。
- **E1 桥的边界**：`/api/paper/*` 以 `mode=ro` + `query_only` 读子项目账本，任何写路径都不经过母站；首页卡片在桥不可用时整块隐藏。桥只读的是**合并树**活库（`:8081` 同源），不是独立版 `:8080` 的库。
- **F 线收盘检阅的时延/成本（2026-11-28 实测）**：两次 LLM 调用合计约 **3.5–5.5 分钟**（含一次重试可到 8 分钟）；15:45 首跑在 16:30 计划窗口前不保证完成，18:30 重试兜底。`ask_raw` 单次超时已放宽到 300s；超时降级为确定性摘要并标 `degraded`，不会阻断主站。
- **F 线事件规则会误报也会漏报**：P0/P1 靠关键词+实体，政策新闻的转载/标题党可能误报；公司事件只在持仓/观察池/候选代码命中时 P1。当前只落库提醒，**不要**用事件表直接驱动交易；P2 市场级阈值未实现。
- **历史笔记的持仓是当前快照**：`portfolio_facts(date)` 对历史日期截断 NAV/成交/计划，但子项目没有持仓历史表，回填笔记里的"持仓"是当前持仓（字段有 `positions_note` 标注）。
- **门禁依赖 `pytest-timeout`（2026-11-18）**：`gate.sh` 靠 `import pytest_timeout` 探测，装了才有单条用例超时；换新 venv / 新机器时记得装，否则只剩整批 600s 兜底（脚本会在头部提示）。
- **nec 承载双项目（互不干扰为用户约定）**：子项目独立版（:8080 + 5 cron，`/home/nec/paper-trading`）与母项目同机，`mother_dir()` 默认解析到合并树活库；母方改动（重启/迁移/deploy）前须确认不碰 :8080 与其目录。Pi 全面退役（无监听/无 cron/服务禁启），冻结副本仅存档。
- **nec CPU 老（无 sse4.2/popcnt，不支持 x86-64-v2）**：numpy 1.26.4 / pandas 2.2.3 / duckdb 1.4.2 被钉住；`pip install -U` 任何含二进制的依赖前先验证 import（新 numpy/duckdb wheel 直接 illegal instruction core dump）。
- **共用工作树干扰已定规（2026-10-28 实录）**：母方 deploy 中途主工作树被切分支致输出错乱。现行规程：主工作树归母方、Qoder 独立 worktree（Issue #9 指引在册）、母方验收部署在隔离克隆（`%TEMP%\opencode\sd-accept`）；**deploy 运行期间任何一方不得切分支/改工作区**。
- **deploy 窗口纪律（2026-10-28/29 实录）**：**15:30–19:00 禁止部署重启**——两次在流水线运行窗口内部署把当日选股打断重跑（无数据损失但拖到 ~19:15）。部署排 15:30 前或 19:00 后。
- **上游 EM 接口对产线 IP 会整天 RemoteDisconnected**（2026-10-30/11-03 实录）：K 线增量整天 0 行、靠 F2 覆盖层兜住广度；增量次日自愈。腾讯行情批查对北交所 920 号段出数正常（批13-b 已接），但**腾讯当日 bar 发布晚于 15:30**（11-03 实录：15:36 拉不到当日、17:00 已有）→ EM 挂掉的日子增量可能零新增，次日 start_date=latest 自愈（已知行为，不修）。
- **EM 依赖补数待解封**：301139（10-30 备源救回后 qfq 锚点轮再失败）与 920305（partial）等 push2his 解封后 `--source akshare` 手动补。
- ~~BJ 财务复扫与归因器新桶~~：**2026-11-19 已解除**——归因器改按任务语义归类后，生产库 `unattributed` 3209→**0**、退出码 0（2911 `no_early_history` + 282 `financial_partial`）。"有行情缺财务"是 `coverage_gaps()` 的**跨库缺口**（当前 0 只），不是 no_data 记录，塞进分类会让 100% 归因变成假话。BJ 财务 321/321 早在批12 已清零。
- ~~**vendor 两条用例依赖墙钟（2026-11-19 凌晨实录）**~~：**2026-11-24 v0.10.1 已修**——`test_intraday_collect_logs_each_symptom_once` 与 `test_intraday_collect_writes_minute_heartbeat` 统一传 `--now 2026-11-03T14:37:00`，不再依赖运行时刻（此前 00:50 跑必红、22:55 跑必绿，vendor 计数会 884/886 波动）。
- **close_adj 修复仍手动**：增量已由 U9-A 供给、回填由 Sina 三列，但修残缺仍靠手动 `fix_close_adj_nulls`（无调度）。
- ~~既有 vendor 用例失败待查~~：**2026-11-17 已消除**——v0.9.0 上游修复后 vendor 套件 886 passed / 0 failed（含此前失败的 `test_raw_feed` 与 `test_minute_supply_t59`），vendor 基线升到 886。
- **生产纪律提请（用户协调，未决）**：母仓库目录（/home/nec/stock-dashboard）不得跑非母项目进程（Hermes agent 的旧 main.py 曾抢占 9527 致母服务重启循环）；端口 9527/8081/8080 各归其主。
- **MOS 存量值是旧前视口径算的**：N7 修后只补空位不重算（"冻结"约定）；H-001 若用存量 mos_at_entry 分层，旧值含前视污染——跑前先决定重算或只用新样本（256 行有 ret_60；`research_result` 0 行无连带）。
- **N22 历史 eps 存量两位小数**：前向修复已合入，已入库历史值仍被截断，估值分位的股本反推受污染——与 `000001` 历史重抓并批处理（涉生产写+上游限流），未排期。
- **员工/AI 协作纪律**：勿直接提交 `nightly/20261023`；独立分支 push + Issue #9 贴 SHA；员工本机缺 akshare 的 12 条本地失败以 nec gate 为准（已实证非损坏）；**改判败路径的批次必须排查旧用例桩面、交付批次必须附本机全量报告**（批12 违反被 gate 拦下，记档）。
- **pi→nec SSH 单向不通**（pi 的 key 未入 nec authorized_keys）：当前部署流 Windows→nec、数据流 nec 拉取，不需要反向；如需 Pi 主动推 nec 再打通。
- **`000001` 历史数据事故**（10 年前复权被误删仅恢复 243 行）与 **push2his 35 次限流**（既有）不变。
- **上游 `total_shares` 当前值盖所有年份**：历史 PB/股本反推只在净利/EPS 可反推时可用，否则 NULL（既有）。
- **EM F10 `interest_coverage` 2026 缺口（2026-11-12 取证）**：2026 三报告期（03-31/06-30/09-30）全 0，2025-12-31 有 4437。根因：唯一写入方是流水线 EM 抓取，被 `incremental_days=60` 陈旧度规则挡住（候选股最新报告期 43 天）；周末 `force_full` 是死代码（`weekday()==5` 但流水线只工作日跑）。**批19 已派修**。
- **`analysis_outcome` 写入方停滞（2026-11-12）**：10 月 0 条、11 月仅 1 条（9 月 73 条）。非重复行（双基准是设计）。外部 AI 分析写入方，待查。
- **`daily_close` 间歇缺口（2026-11-12）**：11-10 缺 80 只（603/605 开头）、11-12 缺 7 只（3016xx/3021xx）；缺失代码均 active 且其它日有数据。疑腾讯批查漏拉/停牌，待查。
- **`market_state` 覆盖虚高（2026-11-12）**：每日 pass、coverage=1.0，但 `pe_percentile_5y` 缺失；11-09/10/11 三天指标完全一致（疑似同一份快照）。
- **母仓库 `paper-upstream` remote 指向 GitHub `oldfunk/paper-trading`**：`subtree pull` 是**只读拉取**（不写子项目仓库、不推 main/tag/Release）。当前 vendored **v0.12.0**；下次跟新 tag 同样只读拉取，发布权归子方。:8081 进程重启归子项目，母方不代重启。
- **nec 独立版框架状态与账本曾脱节（2026-11-12 修复）**：`portfolio.state` 停在 11-06（2 仓 vs 账本 8 仓）。修复用 `ledger reset-state --only rq_session_state`；若再出现 `InitPositionError`，同样处置。

## 历史交接区（追加，不删）
- 2026-11-18 门禁加固：pytest-timeout 单条用例超时（挂起报出用例名，`5ef42f8`）；`[project.optional-dependencies].dev` 入册；顺手修 iteration-log 两处历史编辑残留（`<tool_call>` 文本 + 重复段落）
- 2026-10-30 数据来源收口日（批9–批12 四批验收：温度计回填/申万扩图/季报披露日/北交所接入/判败加固；F2 覆盖层上线首跑 regime=risk_off；北交所 321 只价格回填；三次 gate 拦截教训三条；gate 1063→1124）
- 2026-10-29 员工批1-4 全量验收合入（N1-N4/N19-N22/N17-N18/T1 共 10 commit）+ 用户四决策落地（U9-A 双拉三列 / N5① 门修活 / N8① pe_ttm 落列 / P0-3b）+ 假门清理；gate 875→907；协作规程更新（独立 worktree）
- 2026-10-28 生产迁移 Pi→nec（gzip 压缩流搬 2.5G 库；nec venv 老 CPU 适配 gate 1115 等价；Pi 服务 stop+disable、母库冻结副本留子项目；gate 867→873）
- 2026-10-27 子项目价格尺度事故触发全仓彻查（双 AI 审计：主 AI F1–F7 + Hermes P0×3 修复、员工 N1–N22 工单入 Issue #9；日历未来段回填闭环 max is_open=2026-12-31；gate 856→867）
- 2026-10-27 子项目数据契约 Issue #4–#8 落地（close_qfq/基准代码/交易日历/market_state 快照/覆盖度/契约文档；gate 849→856）
- 2026-10-27 数据调阅 API（`/api/data/*` 供子项目调阅）+ R2-1 假设卡 CLI（gate 835→849）
- 2026-10-27 R2-0 漏洞修补（D-QG 质量门阻断 / G1 block bootstrap / G3 事件研究 runner / G4 分域 / G2 幸存者偏差 / G5 holdout；gate 826→835）
- 2026-10-27 Research OS R1 市场状态切片完成（R1-1…R1-5；gate 794→826；每项完成均审查）
- 2026-10-26 Research OS R0 收尾（R0-8…R0-13：LLM 任务/Prompt/工具网关/输出校验/夹具/Universe；gate 758→794；修 /tmp tmpfs 撑爆致门禁全 ERROR）
- 2026-10-26 Research OS R0 审理 + 5 批修复（D1/D2/D3/G1/G2 + 一致性清理；gate 751→758；复审 13/13）
- 2026-10-26 Research OS R0 框架落地（R0-1…R0-7 + R0-2a/2b/2c；gate 704→751；生产迁移完成 `instrument_id` 0 空值 / `instrument` 5527；duckdb 1.5.6 入依赖；全市场年报回填仍在跑）
- 2026-10-25 审计整改 A2–A4/A6/A7 上线（回填 partial/进度覆盖/年报过滤/估值分位上屏/门禁离线化；gate 669）；A5 价格域受 push2his 限流阻断（kline 仍 49 只，000001 历史误删待重抓）
- 2026-10-25 全量审计（13 项缺陷）+ A1 估值 fail-closed 上线（`367a75a`；gate 644→650；生产 001332/600389/600519 实测）
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
