# Stock Dashboard — 班次交接 (Handoff)

> 账本 `iteration-log.md` 是真相源，本文件是下一班开工的 10 行速览。
> 每班收尾必须重写本文件（"最后状态"+"下一步方向"+"已知隐患"三段重写，"历史交接区"只追加不删），写完再 push + 简报。后一班只读它 + 账本当日条目就能接上。

## 最后状态（2026-09-15 README 重写 + nightly/20260914 合并）
- 分支：`main`，`3ca739f`（9 commits nightly/20260914 已合并），pi1 已同步
- M2 多策略收尾：orchestrator multi_strategy 开关 + 三池 dry-run + 3 单测
- M4a 纸盘基建：paper_* 五表 schema + 迁移守卫 + 5 DAO + BrokerAdapter + PaperBroker 桩 + 8 单测
- C3 数字抽检 warn-only：_check_numeric_citations + actions_summary + 7 单测
- P1 摘要前置：moat_type/mgmt_score/iv_range 透传 + 候选卡摘要块 + 4 回归测试
- M3 前置：交易 Tab 一句话操作指引 + 5 用例渲染验证
- 上游月检：scripts/check_upstream.py + 5 单测
- 验证：49 passed（本地），7 failed 为环境缺依赖非代码 bug；README 全面重写

## 下一步方向（Hermes 自动迭代）

**P0 — M2 完成 → M3 深度分析（目标 10 月）**
1. M2 已完成（multi_strategy 后端 + Tab + orchestrator 接入），转向 M3 持有纪律
2. M3 核心：B5 论点漂移检测 + B6/B7 状态机与周报（prompt 已就位，待周六 live 验证）

**P1 — M4a 纸盘深化**
3. PaperBroker 撮合/费用/风控逻辑接入（当前为 stub）
4. paper_* 五表 DAO 已就绪，待 UI 展示层

**周六 live 验证**
- B6（监控池状态机）+ B7（周报模板）prompt 已就位，周六自动触发
- 验证 watch 动作 + journal 新模板 + coverage 校验

## 已知隐患
- 82 只无 roic/fcf 股票可能是东财 API 无数据的小盘股（C2.5 永久兜底）
- AKShare 利润表/现金流 API 永久挂（S4/S5 标"已挂"）
- `deep_research` 废表已清理（表不存在）
- pi2 系统 python 缺 fastapi/httpx/pandas，全仓验证须用 uv 临时 venv

## 历史交接区（追加，不删）
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
