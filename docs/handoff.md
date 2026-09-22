# Stock Dashboard — 班次交接 (Handoff)

> 账本 `iteration-log.md` 是真相源，本文件是下一班开工的 10 行速览。
> 每班收尾必须重写本文件（"最后状态"+"下一步方向"+"已知隐患"三段重写，"历史交接区"只追加不删），写完再 push + 简报。后一班只读它 + 账本当日条目就能接上。

## 最后状态（2026-09-22 P1② 行业均值收口 + C3 取消已合已同步）
- 分支：`main` = `1fd7b9d`（handoff 续写提交在其上）；远端 `nightly/20260922f` 已删；pi1 已 pull + gate，服务 active
- 内容：行业均值参照（ROE/PE 板块均值 + 样本数，只读不改评分）补进四处评分拆解块；评分透明化 P1② 主项关闭（roadmap ✅）；C3 AI 引用数字抽检拍板取消（分析归外部 agent，本地无正文可抽检）
- 验证：本地 verify ok=True（**348 passed**）；pi1 gate 348 + http 200；全仓设备名扫描 0 命中（含 AGENTS.md，2026-09-22 用户批准后补修）
- 基线：342 → **348**

## 下一步方向
1. P2 体验优化 ⑤⑥⑦⑧（详情页上下只切换 / 财务红绿高亮 / 移动端适配 / 搜索按 PE·ROE·市值排序）——本项目唯一剩余排期面，做哪个由用户点单
2. 外部接入闭环、周六（09-26）复盘执行与决议、盯盘调度制度——使用者与外部 AI 自行发起，本项目不排期（接口 `docs/agent-api.md`、调度参考 `docs/scheduled-tasks.md` 已就绪）

## 已知隐患
- 本地 Zen/Pollinations 双通道已死；本地复盘 LLM 决议不可用（复盘决议按新分工归外部 AI 负责）
- AKShare 利润表/现金流 API 永久挂（S4/S5 标"已挂"，C2.5 兜底）；82 只小盘股无 roic/fcf（东财无数据，非 bug）
- 本机 Windows 拦截 loopback，TestClient HTTP 用例本地跑不动，须 pi1 验证
- `watchlist_thesis` 表为新表（init_database 自动建），pi1 首次写入前未做 live 端到端验证（单测全过）

## 历史交接区（追加，不删）
- 2026-09-22 误入内容清除 + 职责定位已合已同步（`e954b29`→`2ff32b1`，gate 342 全绿；AGENTS.md:53 经用户批准于同日补修，设备名全仓归零）
- 2026-09-22 Agent 接入指南已合已同步（`1499691`：agent-api.md + README 挂链，gate 342 全绿）
- 2026-09-22 ABC 方案已合已同步（`bb64119`：反面检验/论文追踪/复盘三问内化 + 架构方法论固化 + 解绑上游镜像与对照工具，gate 342 全绿）
- 2026-09-22 残留清理已合已同步（nightly/20260922b：hermes_proxy 空目录 + scheduler 纸盘注释尸体，pi1 328 全绿）
- 2026-09-22 残留清理待合（nightly/20260922b：hermes_proxy 空目录 + scheduler 纸盘注释尸体）
- 2026-09-22 文档复检修正已合已同步（9 处写错：AGENTS 工作流/基线 317/归档表述/代理契约，纯文档未重启）
- 2026-09-21 nightly/20260921b 已合已部署（分析师整改 T1–T4 + 去 kanban，生产 gate 328 全绿）
- 2026-09-21 分析师整改 T1+T2 待合（nightly/20260921b：数据质量标注/估值自选/纸盘 schema 删除/笔记 model 列，pi1 323 全绿）
- 2026-09-21 nightly/20260921 已合已部署（去 AI 内联 + ai_proxy 改名 + 文档清包袱，pi1 gate 317 全绿）
- 2026-09-21 去 AI 内联 + 去 Hermes 化待合（nightly/20260921：卡片去 AI/停本地触发/ai_proxy 改名/文档清包袱）
- 2026-09-18 文档复检：修复 handoff 残留冲突标记（21615d2 合并遗留）+ AGENTS.md 校订（基线 317/运行命令/交接段）+ README 校正（run-once→run、基线 313→317），分支 nightly/20260918 待合
- 2026-09-17 收尾（AGENTS.md 入 main + README 如实化 + 删 0917/0917b + 0917c 已合）
- 2026-09-17 备用通道 nightly/20260917c（Zen 全灭实证 + Pollinations 兜底 + 生产冒烟通过）
- 2026-09-17 AI 修复+UI 收敛已部署（ling 优先/Retry-After/观察池卡片统一，pi1 307 全过；发现 403 需 Zen Key）
- 2026-09-17 nightly/20260917 已合已部署（/paper 面板上线 + B6/B7，pi1 297 全过，删废分支0916）
- 2026-09-16 M4a 纸盘引擎：撮合+信号编排+scheduler 触发（37 单测，main 已合，pi1 已同步）
- 2026-09-15 README 全面重写（项目结构/贡献流程/配置表）+ handoff 状态更新
- 2026-09-15 nightly/20260914 合并到 main（9 commits, +1463/-76, 22 files）
- 2026-09-15 账本对齐 P0#1-3/P1#5-6 标完成（纯文档，402cd5e/293e08d/6256d58/4495b9c 注记 + Changelog）
- 2026-09-14 M2 多策略后端 + AI 摘要前置合并（nightly/20260914 + nightly/20260913 → main）
- 2026-09-14 M2 多策略筛选后端核心落地 + verify（strategy_tags + multi 开关 + 5 单测，全仓 229 passed）
- 2026-09-12 架构治理 + README 重排（architecture.md + paper-trading.md + roadmap 升级 + README 重构）
- 2026-09-12 C2.5 东财 datacenter 补 roic/fcf P0（全仓 221 passed，822/904 股有 roic/fcf）
- 2026-09-11 C2 东财第二财务源 P0（全仓 218 passed）
- 2026-09-09 建交接文件，三任务串联启动
- 2026-09-09 白班试跑：豁免D夹具修复落袋（577cc83）
