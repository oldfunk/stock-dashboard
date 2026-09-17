# Stock Dashboard — 班次交接 (Handoff)

> 账本 `iteration-log.md` 是真相源，本文件是下一班开工的 10 行速览。
> 每班收尾必须重写本文件（"最后状态"+"下一步方向"+"已知隐患"三段重写，"历史交接区"只追加不删），写完再 push + 简报。后一班只读它 + 账本当日条目就能接上。

## 最后状态（2026-09-17 AI 修复 + UI 收敛已部署，待 Zen Key）
- 分支：`main` = `fa09679`（nightly/20260917b 已合，已推）；pi1 已 pull + restart + 验证
- 上线：ling 优先 + 429 Retry-After + 观察池卡片统一候选卡（摘要/评分/AI 行/trade 指引/历史/失败徽标）
- 验证：pi1 全仓 307 passed 零失败；三端点 200；DB 备份 `stock_dashboard.db.bak0917b`
- **阻塞**：ling live 调用 403 FreeTierError（免费层仅限 OpenCode 内用）——无 Key 则 AI 持续全败；已向用户索要 Zen Key，拿到后写 pi1 `.env` + restart 即恢复
- 备忘：钉选 Tab JS 缺失（死按钮，后端完好），另起一轮

## 下一步方向
1. 拿到 Zen Key → 写 pi1 `.env` → restart → 验证 AI 恢复（首要）
2. 钉选 Tab JS 补齐（switchView/toggleWatch/加载渲染）
3. 周六 live 验证 B6/B7；M4b QLib 回测

## 已知隐患
- 82 只无 roic/fcf 股票可能是东财 API 无数据的小盘股（C2.5 永久兜底）
- AKShare 利润表/现金流 API 永久挂（S4/S5 标"已挂"）
- 本机 Windows 沙箱拦截 loopback，TestClient HTTP 用例本地跑不动，须 pi1 验证

## 历史交接区（追加，不删）
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
