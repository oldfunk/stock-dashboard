# Stock Dashboard — 班次交接 (Handoff)

> 账本 `iteration-log.md` 是真相源，本文件是下一班开工的 10 行速览。
> 每班收尾必须重写本文件（"最后状态"+"下一步方向"+"已知隐患"三段重写，"历史交接区"只追加不删），写完再 push + 简报。后一班只读它 + 账本当日条目就能接上。

## 最后状态（2026-09-14 M2 多策略后端核心已落地 + verify 通过）
- 分支：`nightly/20260914`（impl `402cd5e` + verify 文档 commit 待推，只推 nightly，不碰 main/pi1）
- 落地内容：`screening_result.strategy_tags` 列 + 迁移守卫；`value_screener` 新增 `load_strategies`/策略阈值检查/`multi_strategy` 开关（默认单策略不变）；multi 模式三独立候选池 + 持久化去重；新单测 5 项
- 验证：全仓 229 passed 零失败（uv 建 /tmp/vrf_venv 补 fastapi/httpx/pandas 后）；改动文件 py_compile 通过；改动文件 emoji 零命中
- 账本已追加 09-14 Changelog 条目（改了什么 + 为什么）；M1 分析可信已达，M2 后端核心完成一半（缺首页策略 Tab）

## 下一步方向（Hermes 自动迭代）

**P0 — M2 多策略收尾（目标 9-30）**
1. 首页策略 Tab 切换（全部 / 成长 / 红利 / 反转），消费 `strategy_tags` 落库数据。
2. pi1 拉 nightly 验证迁移守卫（`strategy_tags` 自动加列）+ 全市场 dry-run 看三池分布是否合理。

**P1 — 分析深度补强**
3. 候选列表前置关键结论（护城河类型 / 管理层评分 / 估值区间），改 `_enrich_stocks()` + 模板。
4. Signal 卡片增加自然语言操作指引（"BUY 置信度高：现价在买入区间内…"）。

**本周六 live 验证**
- B6（监控池状态机）+ B7（周报模板）prompt 已就位，周六自动触发。
- 验证 watch 动作 + journal 新模板 + coverage 校验，不通过则修 prompt + 补单测。

## 已知隐患
- 82 只无 roic/fcf 股票可能是东财 API 无数据的小盘股（C2.5 永久兜底）
- AKShare 利润表/现金流 API 永久挂（S4/S5 标"已挂"）
- `deep_research` 废表已清理（表不存在）
- pi2 系统 python 缺 fastapi/httpx/pandas，全仓验证须用 uv 临时 venv（本次用 /tmp/vrf_venv， disposable）

## 历史交接区（追加，不删）
- 2026-09-14 M2 多策略筛选后端核心落地 + verify（strategy_tags + multi 开关 + 5 单测，全仓 229 passed，nightly/20260914）
- 2026-09-12 架构治理 + README 重排（architecture.md + paper-trading.md + roadmap 升级 + README 重构）
- 2026-09-12 C2.5 东财 datacenter 补 roic/fcf P0（全仓 221 passed，822/904 股有 roic/fcf）
- 2026-09-11 C2 东财第二财务源 P0（全仓 218 passed）
- 2026-09-09 建交接文件，三任务串联启动
- 2026-09-09 白班试跑：豁免D夹具修复落袋（577cc83）
