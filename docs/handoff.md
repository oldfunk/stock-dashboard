# Stock Dashboard — 班次交接 (Handoff)

> 账本 `iteration-log.md` 是真相源，本文件是下一班开工的 10 行速览。
> 每班收尾必须重写本文件（"最后状态"+"下一步方向"+"已知隐患"三段重写，"历史交接区"只追加不删），写完再 push + 简报。后一班只读它 + 账本当日条目就能接上。

## 最后状态（2026-10-22 审计二轮落地：P1 一键部署 + P2 基线强制 + P3 死脚本清理；基线 768）
- **P1**：`scripts/deploy.sh` 一键收尾（推 nightly 留痕 → 生产 worktree 门禁，绿了才 ff 合 main → 生产 pull → 按改动自动重启 → 健康检查）；部署目标读 `deploy.local.md` KEY=VALUE 块。首跑实弹全链路成功：生产 gate 768/150.49s → 合 main → 双服务 200
- **P2**：gate.sh 自动强制基线下限（`scripts/gate-baseline.txt` 首值 768）——"只升不降"从人脑记忆变机器强制；AGENTS/architecture/iteration-log 基线改引用该文件，不再写死数字
- **P3**：删三个死脚本 `daily_cron.sh`/`ai_analysis_cron.sh`/`daily_update.sh`（误装即闯祸；生产 crontab 与 src/tests 零引用双核实）；README 结构树同步
- **审计二轮修正**：CI 表述改实际（PR 触发属休眠能力，"合并前"机器验证=生产 worktree gate，CI=每次合并的机器记录）；协议 §4 补"vendor 落地即关对应 Issue"；Issue #1 过期已关（被 v0.6.5 vendor 超越）
- **坑**：PowerShell 裸敲 bash 进的是 WSL——WSL 的 Linux git 无 GitHub 凭据，push 卡死 10 分钟；deploy.sh 须用 Git for Windows bash 执行（脚本头已注）
- **验证**：生产 worktree gate **768 passed（新 gate.sh 含下限检查实测输出 "Gate passed: 768 passed (基线下限 768)"）**；CI main push 绿（run 39784179188）；双服务 active，:9527/:8081 200；本批无 src/config/paper_trading 改动，deploy.sh 正确判定不重启

## 下一步方向
1. 观察 `deploy.sh` 后续日常使用（下一批含 `src/` 改动时验证自动重启分支；CI main 绿跑跟踪是否稳定）
2. （继承）观察 v0.6.5 固定交易时段 runtime 的 tick 日志；用户确认 UI 净化观感；红绿开关 `--color-up` 兼作强调色是否拆 token 待拍板
3. 审计清单状态：P1/P2/P3 全部落地，无缓做项；`docs/automation-audit.md` 为现行状态

## 已知隐患
- **deploy.sh 凭据链**：Windows 上须用 Git for Windows bash 执行（PowerShell 裸 bash = WSL，Linux git 无 GitHub 凭据会卡 push）；健康检查依赖 pi 的 sudo 免密（当前成立）；`/home/pi/sd-verify` 为持久复用的验证 worktree，`/home/pi/sd-nightly` 是上一班残留（无害可清）
- **CI 环境非生产等同**：ubuntu runner 与生产 Pi 不完全等同——CI 是记录与信号，权威门禁仍是生产 gate，CI 红不等于生产坏
- （继承）交易日历缓存新接缝：母树 `paper_trading/var/trading_calendar.json` 由 cron 每周一刷新，失效则回退"周末+硬编码"判断
- （继承）v0.6.5 弃用旧 schedule gate（部分入口 410）；`engine.enabled: false` 是安全线；独立克隆 `/home/pi/paper-trading` 两套 cron 并存（单交易员原则）；`scripts/backtest_topk.py` 仍坏（`no such column: code`）
- （继承）股息/收益口径三条：个股 qfq 含分红 vs 基准价格指数不含分红（超额略偏乐观）；重叠窗口非独立样本；120 日窗口 2027-01 起才有数据

## 历史交接区（追加，不删）
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
