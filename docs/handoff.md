# Stock Dashboard — 班次交接 (Handoff)

> 账本 `iteration-log.md` 是真相源，本文件是下一班开工的 10 行速览。
> 每班收尾必须重写本文件（"最后状态"+"下一步方向"+"已知隐患"三段重写，"历史交接区"只追加不删），写完再 push + 简报。后一班只读它 + 账本当日条目就能接上。

## 最后状态（2026-09-23 维护模式收尾已合：停止投入，保采集+选股，AI 接口保留）
- **项目冻结**：2026-09-23 用户指令停止投入、不再深入开发；日常只保留 pi 每日采集 + 算法选股；AI 分析不再投入，数据接口保留供日后外部 AI 自助分析
- **收尾分支 `nightly/20260923a` 已合入 main**：AI 零触发审计 + 备份清理 + 文档冻结（handoff/账本/roadmap 维护模式），纯文档零代码；远端仅 main + archive
- **在跑什么**：`stock-dashboard.service`（enabled，掉电自启）→ 内置调度器：交易日 15:30 `orchestrator.run_daily_pipeline`（采集+筛选+验算闸+S8 回填，不含 AI）+ 每 30min 大盘 + 交易时段每 5min 行情 + 周六本地复盘（规则部分；LLM 决议因通道死自动跳过）
- **今日实证（2026-09-23）**：`20260923_153008` completed（15:30:08→16:16:15，5527→20，analyzed=0）；screening 当日 20 行；快照今日回写 719 行（候选池+钉选补录；其余行保留历史日期，既有滚动记录设计）；sector 487/719；首页行业均值 17 行（S8 调度内自动回填生效）
- **AI 零触发六保险**：scheduler AI 调用已注释（`src/scheduler.py:236`，函数体留 dead code 未删）+ 日流水线走 orchestrator（无 AI 步骤）+ `run_pipeline` 步骤 6 只手动触发 + pi 无 crontab/systemd 定时任务 + `.env` 无 Key + 双通道本来就已死——零花钱、零限流风险，可无人值守
- **AI 数据接口（保留，gate 覆盖）**：`GET /api/stocks` 当日候选全量、`GET /api/watchlist/{code}/full` 钉选一站式、`GET /api/search`、`GET /api/journal/*`、`GET /api/*/kline` 等（全表见 `docs/agent-api.md`）；外部 AI 自助拉数 + POST 写回分析/笔记/论文；手动脚本 `scripts/run_ai_analysis.py`、`retry_ai.py` 保留（通道死，需自备可用模型才跑得动）
- **磁盘**：59G 卡用 25%（42G 空闲）；`data/db` 280M→122M（删 4 个陈年 .bak，留最新 2 个）；`data/backup` Sep 4-5 实验快照 8 个（323M，前 sector-schema 时代、恢复无用）已清；合计释放约 480M；`data/logs` 轮转 bounded（8.8M）；journal 4.3M；DB 42M 日增数千行，空间以年计充足
- **基线 367 = main 现状**（冻结；后续无代码，gate 仅在动代码时重跑）

## 下一步方向（无开发任务；只剩看护）
1. 看护（偶发）：服务 `active`？→ 本机 curl `/api/status`；今日 15:30 跑了？→ 查 `run_log` 最新行 / 首页候选日期；磁盘 → `df -h`（<80% 不理）
2. 日后想恢复 AI 分析：自备可用 LLM（配 `.env`/config）→ 手动 `run_ai_analysis.py` 或外部 agent 按 `docs/agent-api.md` 自助；本地通道（Zen/Pollinations）已死别试
3. 用户保留决策：`deep_research` 废表（5 行，无代码引用）是否 drop——留着无害，未动

## 已知隐患（冻结前状态，原样保留）
- S8 覆盖约 47%（新浪 2999 只 ∩ 快照 5527 行 = 2594；今日子集 487/719）——未收录新股 sector NULL，均值 WHERE 过滤；非 bug
- 快照表为滚动记录（每日仅回写候选池+钉选补录约 700 行，其余行日期旧）——既有设计；筛选扫描以当日流水线为准
- 上游不稳（新浪 11s 慢响应/curl RC28/背靠背拖慢）——0.6s 节奏+420s 护栏+缓存兜底；全挂则跳过不清旧值
- AKShare S4/S5 永久挂（C2.5 兜底）；82 只小盘无 roic/fcf（东财无数据）
- `watchlist_thesis` 从未 live 端到端验证（单测过）；外部 AI 写回前建议先手动 POST 验证
- 重启验证等待 ≥10s（uvicorn 启动约 8s，sleep 3 首检 000 系正常时序）

## 历史交接区（追加，不删）
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
- 2026-09-12 C2.5 东财 datacenter 补 roic/fcf P0（全仓 221 passed，822/904 股有 roic/fcf）
- 2026-09-11 C2 东财第二财务源 P0（全仓 218 passed）
- 2026-09-09 建交接文件，三任务串联启动
- 2026-09-09 白班试跑：豁免D夹具修复落袋（577cc83）
