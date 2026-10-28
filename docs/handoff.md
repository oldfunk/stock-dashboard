# Stock Dashboard — 班次交接 (Handoff)

> 账本 `iteration-log.md` 是真相源，本文件是下一班开工的 10 行速览。
> 每班收尾必须重写本文件（"最后状态"+"下一步方向"+"已知隐患"三段重写，"历史交接区"只追加不删），写完再 push + 简报。后一班只读它 + 账本当日条目就能接上。

## 最后状态（2026-10-28 生产迁移 Pi→nec 完成部署面 + 全市场价格回填 9 成 + N7 修；基线：母项目 873 / vendor 242）

- **生产迁移 Pi → nec（用户提议，拍板"现在就迁"）**：Pi 连续自重启、954M 内存多项目过劳；nec（x86_64 2C/5.8G/370G，idle 0.03）承接母项目全部生产面。
  - DB：Pi `sqlite3 .backup` 一致快照（2.5G）→ nec gzip 压缩流拉取（688KB/s → 2.0MB/s）→ md5/integrity/行数比对（kline 10,075,767 / financial 143,500 / instrument 5,527 / snapshot 5,527 / progress 11,457）。
  - nec venv 适配老 CPU：numpy 1.26.4 / pandas 2.2.3 / duckdb 1.4.2（新 wheel x86-64-v2 基线 illegal instruction）；**gate 1115 passed 与 Pi 等价**。
  - systemd 两单元重建（User=nec）、02:30 年报 cron 迁移、`deploy.local.md` 已改 nec；nec 既有子项目旧 cron（/home/nec/paper-trading）按用户指示未动。
  - Pi 收尾：两服务 stop+disable、cron 整表清除（`crontab -r` 后 `no crontab for pi`）、无任何监听——**Pi 全面退役，不再投入生产**；母库冻结副本仅存档。
  - **子项目同日迁移完成（用户主导，双方约定互不干扰）**：独立版落 nec（:8080 + 5 条 cron → `/home/nec/paper-trading`），`mother_dir()` 默认路径解析到 nec 合并树 = **活母库，冻结库耦合解除**。
- **全市场价格回填全域收官（数据丰富主线，nec 17:35）**：`backfill_progress` done **5,193** + no_data 13 = 5,206 有年报全量、pending **0**；`kline_daily` **10,548,912 行 / 5,193 只**（回填前 959 只=17.4%→94%）；5 年深度 827→**3,841 只**；五契约列 NULL=0、负价/零价/重复/`qfq!=close` 违例全 0（母方预检）；13 只上游确无数据标 no_data（`--retry-no-data` 可重试），605336 三试后复活。全量正式复核=员工批4（Issue #9 完成信号已发）。
- **已合入（nightly/20261023 → 待 deploy 合 main）**：`165be68` 回填容锁（进度标记不再杀死 15h 长任务）；`7ab7e62` R2-2 实验计划 CLI（R2 完成 2/6）；`1b826c9` **N7 修**（`_mos_at_entry` 去 report_date 兜底，前视最长 4 个月；fail-closed，MOS 覆盖率下降是正确性代价）。
- **契约列存量归零（迁移前产线实跑）**：close_qfq 21→0 / close_adj 21→0（Sina 重抓）/ instrument_id+market 7,089→0。
- **B3 中期（只读）**：负价/零价/重复行=0；gm -5678% / roe -266264% 等异常值定性为真实财务事件；历史股本 4,759 只多年同值（已知上游盖值）。
- **服务（nec）**：`stock-dashboard.service` / `paper-trading-parent.service` active，`/` 与纸盘面板 http:200。
- **10-28 日流水线补跑进行中（18:01 起，setsid 独立进程）**：迁移停机窗（Pi 15:27 停 / nec 16:24 起）漏掉当日 15:30 收盘选股（screening 最新仍 20261027），手动 `src.main run` 补跑；财务历史阶段=常规日任务（792 初筛候选中多数报告期>60 天——Q3 披露窗口 4786/5206 全市场超期的放大效应），~2.7 只/分 → 预计 **21:00-22:00 完成**产出今日筛选结果。若中断重跑同命令即可；价格面不受影响（backfill 已含今日 K，upsert COALESCE 保 `close_adj`，已核源码确认）。

## 下一步方向（按此顺序，一次一步，每步带生产证据）

1. **B3 价格侧全市场复核验收**（回填已完成，母方预检全绿）：等员工批4 交付全量正式复核（NULL 日期分布 / adj-raw 比值抽样 / 当日双写路径交汇），母方逐项验收后 B3 关账。
2. **U9 → F2 解链**（等用户拍板方案 A/B/C，倾向 A=EM 双拉+merge_price_model，T1 先修）——全市场回填后 market_state 覆盖面已具全市场意义，此链价值更高。
3. **员工批 1（N1–N4）验收合并**：逐 SHA 排 gate；N3/N4 需同步改 test_quality/test_regime 夹具。协作纪律不变：独立分支 + Issue #9 贴 SHA。
4. **待拍板口径**（汇总一次问）：Hermes P0-3 / N5 / N6-② / N8 / U9 方案。
5. **R2-3 H-001（MOS 预测力）**：N7 已修（覆盖率副作用已披露）→ lock 假设 → create 计划（新 CLI）→ run；再稳健性 → 结论冻结。
6. **迁移遗留小项**：Pi→nec 单向 SSH（pi key 未入 nec authorized_keys，暂不需要；需要反向推送时再打通）；nec 上如需 duckdb 新版本特性，注意 CPU 基线上限（当前 1.4.2）。
7. **R3–R6** 照旧（S-BASE-v0 → 策略版本 CLI → 纸盘部署 → 归因 → 漂移 → LLM 助手 → 研究界面 → 最小闭环）。

## 已知隐患

- **nec 承载双项目（原迁移耦合已解除）**：子项目独立版（:8080 + 5 cron）与母项目同机，`mother_dir()` 默认解析到合并树活库；两项目互不干扰为用户约定——母方改动（重启/迁移/deploy）前须确认不碰 :8080 与 `/home/nec/paper-trading`。Pi 冻结副本仅存档，无读写方。
- **nec CPU 老（无 sse4.2/popcnt，不支持 x86-64-v2）**：numpy 1.26.4 / pandas 2.2.3 / duckdb 1.4.2 被钉住；`pip install -U` 任何含二进制的依赖前先验证 import（新 numpy/duckdb wheel 直接 illegal instruction core dump）。
- **T1 生产进行时（最高优）**：每日东财 35 次限流 → 腾讯回退 → `_fetch_kline_tx` 丢 adjust → 不复权价混入 `close` 列（10-27 日志满屏回退为证）。修=透传 adjust+单测；口径语义改动（raw 进 close_raw）需用户拍板。
- **U9 → F2 断链**：增量 kline 行 `close_adj` 全 NULL → market_state 每日快照接线恒 invalid；F2 代码已撤回，等 U9 方案拍板（倾向 A）。
- **close_adj/close_raw 的日常维护仍只靠手动跑 `fix_close_adj_nulls`**：Sina 回填是唯一三列补给，无调度（与日历同族"有回填无调度"，U9 方案落地时一并考虑）。
- **MOS 存量值是旧前视口径算的**：N7 修后只补空位不重算（仓内"冻结"约定）；H-001 若用存量 mos_at_entry 分层，旧值含前视污染——跑之前先决定是否重算存量（256 行有 ret_60）。
- **P0-3 / N5 / N6-② / N8 / U9 方案未拍板**：不擅动，汇总在 Issue #9 与 handoff"下一步 4"。
- **员工/AI 协作纪律**：勿直接提交 `nightly/20261023`（部署分支，gate 窗口内提交会造成 push/gate 错位——已发生一次并在 push 前拦截）；独立分支 push + Issue #9 贴 SHA。gate 期间主 AI 同样只改工作区不 commit。
- **pi→nec SSH 单向不通**（pi 的 key 未入 nec authorized_keys）：当前部署流 Windows→nec、数据流 nec 拉取，不需要反向；如需 Pi 主动推 nec 再打通。
- **`000001` 历史数据事故**（10 年前复权被误删仅恢复 243 行）与 **push2his 35 次限流**（既有）不变。
- **上游 `total_shares` 当前值盖所有年份**：历史 PB 只在净利/EPS 可反推时可用，否则 NULL（既有）。

## 历史交接区（追加，不删）
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
