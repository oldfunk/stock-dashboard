# Stock Dashboard — 班次交接 (Handoff)

> 账本 `iteration-log.md` 是真相源，本文件是下一班开工的 10 行速览。
> 每班收尾必须重写本文件（"最后状态"+"下一步方向"+"已知隐患"三段重写，"历史交接区"只追加不删），写完再 push + 简报。后一班只读它 + 账本当日条目就能接上。

## 最后状态（2026-09-22 四项拍板全部落地：0922d 已合已同步 + sector 回填 live 实证；P0-2 修复入 0922e 待合）
- **0922d 已合并部署**：`nightly/20260922d`（残留④ + 测试规则 + S8 sector 回填 + 收尾 docs，4 提交）ff 入 main = `1f0427c`，远端/本地分支已删；生产同步 = git pull → 部署目录 gate 360 passed → 重启 `stock-dashboard.service`（active，ROOT/API/JOURNAL 全 200，日志 0 Traceback）
- **sector 回填 live 实证（生产手动触发完整链路，等效 orchestrator 挂载点）**：`fetch_sector_map()` 真实新浪 92.3s 取 **2999 只**映射 → `backfill_sectors()` 回填 **2594 行**（DB total 5527；映射−回填差值 = 新浪收录但不在快照全集的代码）→ 最新快照日 Top 板块 金融40/机械33/生物制药30 → 首页 `行业均值` 渲染 **16 行**（此前 0）——板块归属→行业均值→卡片展示闭环打通
- **P0-2 修复完成（`nightly/20260922e` = `453dfdf` + 本收尾 docs，待合）**：① `verify_market_cap` 三分支补 `reported` 键（prompt 曾渲染「快照None亿」）② `verify_run` 加 `verified` 计数 + 全 SKIP 警告日志，CLI 全缺数 **exit 2**（不再以零失败冒充成功）③ prompt 双源块三态重写（全 SKIP 如实报「验算未执行/这不是通过」+ 跳过原因 findings 透传；部分跳过附原因；WARN/FAIL 明细带真实快照值）+ 7 单测
- 验证（生产服务器 worktree，按测试规则）：`gate.sh` **367 passed / Gate passed / 40.16s**（360 + 7；期间 gate 抓到测试内 SQL 用 `None` 的真 bug，改 `NULL` 后复跑全绿）；三扫描全绿（ast / 新增行 emoji 0 / 身份 0）
- 合并 0922e 后同步生产：git pull + gate + **重启服务**（验算闸/分析 prompt 改动需重启生效）；sector 此后随每日 15:30 流水线自动回填（缓存兜底）

## 下一步方向
1. 等用户批准合并 `nightly/20260922e`（P0-2 修复）→ git pull + 重启 `stock-dashboard.service` + gate 验证
2. P2 体验优化 ⑤⑥⑦⑧（详情页上下只切换 / 财务红绿高亮 / 移动端适配 / 搜索按 PE·ROE·市值排序）——本项目唯一剩余排期面，做哪个由用户点单
3. 外部接入闭环、周六（09-26）复盘执行与决议、盯盘调度制度——使用者与外部 AI 自行发起，本项目不排期（接口 `docs/agent-api.md`、调度参考 `docs/scheduled-tasks.md` 已就绪）

## 已知隐患
- S8 覆盖口径（live 定数）：新浪 49 板块映射 2999 只，生产回填 2594/5527 行（47%）——缺口为板块名单未收录的新股 + `其它行业` 家数元数据陈旧 31 只；未映射行 sector 保持 NULL、行业均值 WHERE 过滤——已知限制非 bug
- S8 上游不稳已实证：名单接口约 11s 慢响应、裸 curl 15s 超时（RC28）、背靠背连打被拖慢——0.6s 节奏 / 420s 护栏 / 磁盘缓存三级兜底已挂；新浪整体不可用时，缓存耗尽后回填自动跳过（不清旧值、不阻断流水线）
- 本地 Zen/Pollinations 双通道已死；本地复盘 LLM 决议不可用（复盘决议按新分工归外部 AI 负责）
- AKShare 利润表/现金流 API 永久挂（S4/S5 标"已挂"，C2.5 兜底）；82 只小盘股无 roic/fcf（东财无数据，非 bug）
- `watchlist_thesis` 表为新表（init_database 自动建），生产服务器首次写入前未做 live 端到端验证（单测全过）

## 历史交接区（追加，不删）
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
