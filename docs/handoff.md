# Stock Dashboard — 班次交接 (Handoff)

> 账本 `iteration-log.md` 是真相源，本文件是下一班开工的 10 行速览。
> 每班收尾必须重写本文件（"最后状态"+"下一步方向"+"已知隐患"三段重写，"历史交接区"只追加不删），写完再 push + 简报。后一班只读它 + 账本当日条目就能接上。

## 最后状态（2026-09-15 账本对齐：P0#1-3 与 P1#5-6 标完成，纯文档）
- 分支：`nightly/20260914`，本地 ahead 2（`6256d58` P1 摘要透传 + `4495b9c` trade-guide），基线 `origin/main=45a9bfc`（含 `402cd5e` 后端 + `293e08d` 策略 Tab）
- P0#1-3 完成：strategy_tags 列 + 迁移守卫 + load_strategies/multi_strategy 开关 + 透传 + 首页策略 Tab（全部/成长/红利/反转）
- P1#5-6 完成（nightly 待合）：候选卡摘要块（moat_type/mgmt_score/iv_range）+ 交易 Tab trade-guide 一句话指引
- 验证：`git diff` 仅 docs/ 两文件；改动文件 emoji 零命中；`bash ~/work/gate.sh` PASS

## 下一步方向（Hermes 自动迭代）

**P0 — M2 收尾（目标 9-30）**
1. orchestrator 接 multi_strategy 开关：`src/orchestrator.py` 从 config 读 `screening.multi_strategy`（默认 false），为 true 时调 `run_screener(..., multi_strategy=True)` 并 log 三池 size；单测验证开关分支。
2. pi1 拉 nightly 验证迁移守卫（`strategy_tags` 自动加列）+ 全市场 dry-run 看三池分布是否合理。

**P1 — C3 / M4a**
3. C3 AI 引用数字抽检 P2（可选）：仿 report_audit，正文数字 vs 库交叉，warn-only。
4. M4a 纸盘基建：paper_* 五表 schema + 迁移（SQLite 复用现有 DB）。

**本周六 live 验证**
- B6（监控池状态机）+ B7（周报模板）prompt 已就位，周六自动触发。
- 验证 watch 动作 + journal 新模板 + coverage 校验，不通过则修 prompt + 补单测。

## 已知隐患
- 82 只无 roic/fcf 股票可能是东财 API 无数据的小盘股（C2.5 永久兜底）
- AKShare 利润表/现金流 API 永久挂（S4/S5 标"已挂"）
- `deep_research` 废表已清理（表不存在）
- pi2 系统 python 缺 fastapi/httpx/pandas，全仓验证须用 uv 临时 venv

## 历史交接区（追加，不删）
- 2026-09-15 账本对齐 P0#1-3/P1#5-6 标完成（纯文档，402cd5e/293e08d/6256d58/4495b9c 注记 + Changelog）
- 2026-09-14 M2 多策略后端 + AI 摘要前置合并（nightly/20260914 + nightly/20260913 → main）
- 2026-09-14 M2 多策略筛选后端核心落地 + verify（strategy_tags + multi 开关 + 5 单测，全仓 229 passed）
- 2026-09-12 架构治理 + README 重排（architecture.md + paper-trading.md + roadmap 升级 + README 重构）
- 2026-09-12 C2.5 东财 datacenter 补 roic/fcf P0（全仓 221 passed，822/904 股有 roic/fcf）
- 2026-09-11 C2 东财第二财务源 P0（全仓 218 passed）
- 2026-09-09 建交接文件，三任务串联启动
- 2026-09-09 白班试跑：豁免D夹具修复落袋（577cc83）
