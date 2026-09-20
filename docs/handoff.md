# Stock Dashboard — 班次交接 (Handoff)

> 账本 `iteration-log.md` 是真相源，本文件是下一班开工的 10 行速览。
> 每班收尾必须重写本文件（"最后状态"+"下一步方向"+"已知隐患"三段重写，"历史交接区"只追加不删），写完再 push + 简报。后一班只读它 + 账本当日条目就能接上。

## 最后状态（2026-09-20：路线调整完成，聚焦价值投资分析面板）
- 分支：main = `24c83c9`（AGENTS.md 新增验证规则）；nightly/20260920 已归档 AI 分析和纸盘交易代码
- pi1：已同步 main，gate.sh 360 passed 全绿；服务运行正常
- 完成项：M2 策略分化、M3 论点漂移 + 监控条件、t4 通用 Hermes 代理 API、t5 TopK 回测脚本、t6 pi1 验证
- 路线调整：移除 M4a/M4b/M4c/M4d 量化交易路线，AI 分析缩减为 API 接口，面板只负责展示
- 归档：`src/paper/_legacy/`（纸盘交易）、`src/analyzer/_legacy/`（本地 AI 分析）
- 用户定：AI 分析由外部 Hermes 代理执行，面板不触发；全局约定：默认推 nightly 分支、永不自动合并、合并后同步 pi1
- 阻塞更新：Zen Key 不配了（用户否决）；免费模型仅限 OpenCode 内部使用，外部不可用

## 下一步方向
1. P1 面板深化（目标 11 月）：评分体系透明化、AI 笔记增强、时间线交互、详情页体验优化
2. P2 体验优化（目标 12 月）：移动端适配、快捷切换、财务指标高亮、搜索排序
3. 外部 Hermes 代理接入 AI 分析（`src/hermes_proxy/` 已实现通用 API 接口）
4. 周六 live 验证完整 review() 流程（pi1 httpx 已安装）

## 已知隐患
- 82 只无 roic/fcf 股票可能是东财 API 无数据的小盘股（C2.5 永久兜底）
- AKShare 利润表/现金流 API 永久挂（S4/S5 标"已挂"）
- 本机 Windows 沙箱拦截 loopback，TestClient HTTP 用例本地跑不动，须 pi1 验证
- pi1 venv 缺 httpx（已安装），完整 review() 流程待验证

## 历史交接区（追加，不删）
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
