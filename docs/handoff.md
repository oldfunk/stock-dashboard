# Stock Dashboard — 班次交接 (Handoff)

> 账本 `iteration-log.md` 是真相源，本文件是下一班开工的 10 行速览。
> 每班收尾必须重写本文件（"最后状态"+"下一步方向"+"已知隐患"三段重写，"历史交接区"只追加不删），写完再 push + 简报。后一班只读它 + 账本当日条目就能接上。

## 最后状态（2026-09-21 nightly/20260921 已合已部署）
- 分支：`main` = `ac62476`（ff 合并）；远端 `nightly/20260921` 待删；pi1 已 pull + restart + 验证
- 上线：列表卡片去 AI 内联 / 调度停本地 AI 触发 / `ai_proxy` 改名 / 文档清包袱（基线 317）
- 验证：pi1 gate **317 passed**；三端点 200（`/`、`/api/status`、`/journal`）；`/candidates` 按预期 404；日志零 Traceback；DB 备份 `stock_dashboard.db.bak0921`
- 注意：本地 AI 自动触发已停——明日起不再有每日 AI 失败循环；外部 AI 消费方待排期

## 下一步方向
1. 删远端 `nightly/20260921`（已合，无残留）
2. 外部 AI 消费方排期（取数/写回接线，见 `docs/ai-proxy-ai-analysis.md` §5）
3. P1 面板深化；周六复盘 LLM 决议现状已记账（失败整轮跳过）

## 已知隐患
- 本地 Zen/Pollinations 双通道已死；周六复盘 LLM 决议同命（失败整轮跳过）
- AKShare 利润表/现金流 API 永久挂（S4/S5 标"已挂"，C2.5 兜底）
- 本机 Windows 沙箱拦截 loopback，TestClient HTTP 用例本地跑不动，须 pi1 验证
- pi1 落后 main 2 commits 的状态已消除（本次已同步到最新）

## 历史交接区（追加，不删）
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
