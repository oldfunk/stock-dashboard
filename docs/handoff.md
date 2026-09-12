# Stock Dashboard — 班次交接 (Handoff)

> 两档迭代（半夜 00:00 + 白班 12:30）与守卫（18:00）共用的短交接。
> 账本 `iteration-log.md` 是真相源，本文件是下一班开工的 10 行速览。
> 每班收尾必须重写本文件，写完再 push + 简报。后一班只读它 + 账本当日条目就能接上。

## 最后状态（2026-09-12 C2.5 落袋）
- 分支：`main`（HEAD `16d28e1` + merge commit `e4a86f2`）
- 合并内容：C2.5 东财 datacenter 补 roic/fcf P0（`_fetch_eastmoney_roic_fcf()` + `collect_historical_financial_data()` 兜底 + 单测 3 个）
- 验收：全仓 221 passed 零失败；pi1 端到端验证 000792（roic_5y=31.8, fcf_5y=172亿）；批量重建 822/904 股有 roic/fcf（91%）
- C 系列完成状态：C1 已完成 / C2 已完成 / C2.5 已完成 / C3 待议（P2 可选）
- 待观察项：82 只无 roic/fcf 的股票可能是东财 API 无数据的小盘股；AKShare 利润表/现金流 API 仍挂（C2.5 作为永久兜底）
- 当日管线：09-12 筛选 20 只，AI 分析 0（正常：AI 为每周六触发）

- 2026-09-11 C2 东财第二财务源 P0 落袋（全仓 218 passed）。

## 历史交接区（追加，不删）
- 2026-09-09 建交接文件，三任务串联启动。
- 2026-09-09 白班试跑：豁免D夹具修复落袋（577cc83），交接链路缺 commit/push/handoff 三件套，已人工补齐。
