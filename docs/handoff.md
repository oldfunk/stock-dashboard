# Stock Dashboard — 班次交接 (Handoff)

> 账本 `iteration-log.md` 是真相源，本文件是下一班开工的 10 行速览。
> 每班收尾必须重写本文件（"最后状态"+"下一步方向"+"已知隐患"三段重写，"历史交接区"只追加不删），写完再 push + 简报。后一班只读它 + 账本当日条目就能接上。

## 最后状态（2026-09-17 nightly/20260917 待合）
- 分支：`nightly/20260917`（`a6b4d2d` 起，基于 main `67c2f42`），**只推 nightly，不碰 main/pi1**
- 收编：cherry-pick `d425b82`（M3 B6/B7 coverage 纯函数 + watch 回流 + 7 单测）；`53287aa` 已裁定废弃（与 main M4a 重复实现，合会删 engine.py，涨跌停以后按需重做）
- 新活：`GET /paper` 只读虚拟盘面板（账户/持仓/委托/净值 + 非实盘横幅 + 缺表降级）+ `PaperOrderDAO.list_recent` + 4 单测 + 首页导航入口
- 验证：265 passed；paper.html 离线三态渲染过；TestClient 三用例本机跑不动（沙箱 loopback 拦截，存量同症），待合后 pi1 验证
- M4a 纸盘基建：broker 撮合 + engine 信号 + scheduler 异步触发（main 已有，37 单测）

## 下一步方向（用户 review 合并后）
1. 合本分支到 main，pi1 pull + restart（建 paper_* 表，纸盘开始记数），跑 pi1 全仓验证 TestClient 三用例
2. M4b QLib 离线验证（PC/云跑 TopK 回测，只回流结论）或 M4a 深化（NAV 曲线 / 涨跌停重做）
3. 周六 live 验证 B6/B7（watch 动作 + journal 新模板 + coverage 校验）

## 已知隐患
- 82 只无 roic/fcf 股票可能是东财 API 无数据的小盘股（C2.5 永久兜底）
- AKShare 利润表/现金流 API 永久挂（S4/S5 标"已挂"）
- pi1 DB 尚无 paper_* 表（服务未重启迁移），/paper 在 pi1 现阶段显示"尚未初始化"属预期
- 本机 Windows 沙箱拦截 loopback，TestClient HTTP 用例本地跑不动，须 pi1 验证
- `origin/nightly/20260916` 剩余 `53287aa` 已裁定废弃，勿合（合会删 engine.py）

## 历史交接区（追加，不删）
- 2026-09-17 nightly/20260917 待合（收编0916-d425b82 + /paper 只读面板 + 4 单测，弃53287aa）
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
