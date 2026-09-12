# Stock Dashboard — 班次交接 (Handoff)

> 账本 `iteration-log.md` 是真相源，本文件是下一班开工的 10 行速览。
> 每班收尾必须重写本文件（"最后状态"+"下一步方向"+"已知隐患"三段重写，"历史交接区"只追加不删），写完再 push + 简报。后一班只读它 + 账本当日条目就能接上。

## 最后状态（2026-09-12 架构治理 + README 重排）
- 分支：`main`（HEAD `2d18db1`）
- 合并内容：架构治理三件套（`docs/architecture.md` + `docs/paper-trading.md` + `docs/roadmap.md` 升级）+ README 重排（逻辑分区 + 数据源表格化）
- 架构真相源已立：S1–S7 数据源注册表 + 三条铁律（删适配 = 删注册表 + 删契约单测，三者同 commit）
- 虚拟盘方向已定：M4a 自研 paper engine（跑 pi，零依赖）→ M4b QLib 离线（PC/云）→ M4c QMT 模拟首选
- B1–B8 / C1–C2.5 / P1③ / P1② 全部完成；M1 分析可信已达

## 下一步方向（Hermes 自动迭代）

**P0 — M2 多策略接入（目标 9-30）**
1. `strategies.yaml` 已定义三策略阈值，未接入筛选流水线。需在 `src/screener/value_screener.py` 新增多策略模式，输出三个独立候选池。
2. `screening_result` 新增 `strategy_tags`（TEXT, JSON array），记录命中策略。
3. 首页增加策略 Tab 切换（全部 / 成长 / 红利 / 反转）。

**P1 — 分析深度补强**
4. 候选列表前置关键结论（护城河类型 / 管理层评分 / 估值区间），改 `_enrich_stocks()` + 模板。
5. Signal 卡片增加自然语言操作指引（"BUY 置信度高：现价在买入区间内…"）。

**本周六 live 验证**
- B6（监控池状态机）+ B7（周报模板）prompt 已就位，周六自动触发。
- 验证 watch 动作 + journal 新模板 + coverage 校验，不通过则修 prompt + 补单测。

## 已知隐患
- 82 只无 roic/fcf 股票可能是东财 API 无数据的小盘股（C2.5 永久兜底）
- AKShare 利润表/现金流 API 永久挂（S4/S5 标"已挂"）
- `deep_research` 废表已清理（表不存在）

## 历史交接区（追加，不删）
- 2026-09-12 架构治理 + README 重排（architecture.md + paper-trading.md + roadmap 升级 + README 重构）
- 2026-09-12 C2.5 东财 datacenter 补 roic/fcf P0（全仓 221 passed，822/904 股有 roic/fcf）
- 2026-09-11 C2 东财第二财务源 P0（全仓 218 passed）
- 2026-09-09 建交接文件，三任务串联启动
- 2026-09-09 白班试跑：豁免D夹具修复落袋（577cc83）
