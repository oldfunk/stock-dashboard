# Stock Dashboard — 班次交接 (Handoff)

> 账本 `iteration-log.md` 是真相源，本文件是下一班开工的 10 行速览。
> 每班收尾必须重写本文件（"最后状态"+"下一步方向"+"已知隐患"三段重写，"历史交接区"只追加不删），写完再 push + 简报。后一班只读它 + 账本当日条目就能接上。

## 最后状态（2026-09-17 备用通道 nightly/20260917c 待合）
- 分支：`nightly/20260917c`（`a0142a4`，基于 main `da9ea08`），只推分支，未动 main/pi1
- 内容：Zen 7 模型服务端全灭实证 + Pollinations 免 Key 兜底（配置化）+ 4 单测 + 生产冒烟通过
- 验证：本地 12/12 新单测；pi1 真实 prompt fallback 返回可用分析 JSON；全仓待合后跑
- 下一步：用户批准 → 合 main → pi1 部署 → 明日流水线验证首轮备用分析质量

## 下一步方向
1. 合 nightly/20260917c → pi1 部署 → 明日 15:30 验证首轮备用分析质量（首要）
2. 钉选 Tab JS 补齐（switchView/toggleWatch/加载渲染）
3. 周六 live 验证 B6/B7；M4b QLib 回测

## 已知隐患
- 82 只无 roic/fcf 股票可能是东财 API 无数据的小盘股（C2.5 永久兜底）
- AKShare 利润表/现金流 API 永久挂（S4/S5 标"已挂"）
- 本机 Windows 沙箱拦截 loopback，TestClient HTTP 用例本地跑不动，须 pi1 验证

## 历史交接区（追加，不删）
- 2026-09-17 备用通道 nightly/20260917c 待合（Zen 全灭实证 + Pollinations 兜底 + 生产冒烟通过）
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
