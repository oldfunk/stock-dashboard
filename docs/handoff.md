# Stock Dashboard — 班次交接 (Handoff)

> 账本 `iteration-log.md` 是真相源，本文件是下一班开工的 10 行速览。
> 每班收尾必须重写本文件，写完再 push + 简报。后一班只读它 + 账本当日条目就能接上。

## 最后状态（2026-09-13 AI 分析摘要前置）
- 分支：`nightly/20260913`（HEAD 待提交）
- 完成内容：候选股列表 AI 分析摘要前置，显示护城河类型/管理层评分/结论/稳健估值区间
- 核心改动：`_stock_list.html` 新增 ai-summary 区块 + index.html/candidates.html 补 CSS 样式
- 验收：224 passed 零失败，零 emoji，模板语法正确

## 下一步方向（Hermes 自动迭代）

**P0 — M2 多策略接入（目标 9-30）**
1. `strategies.yaml` 已定义三策略阈值，未接入筛选流水线。需在 `src/screener/value_screener.py` 新增多策略模式，输出三个独立候选池。
2. `screening_result` 新增 `strategy_tags`（TEXT, JSON array），记录命中策略。
3. 首页增加策略 Tab 切换（全部 / 成长 / 红利 / 反转）。

**P1 — 分析深度补强**
4. ✅ 已完成：候选列表前置关键结论（护城河类型 / 管理层评分 / 估值区间）
5. Signal 卡片增加自然语言操作指引（"BUY 置信度高：现价在买入区间内…"）。

**本周六 live 验证**
- B6（监控池状态机）+ B7（周报模板）prompt 已就位，周六自动触发。
- 验证 watch 动作 + journal 新模板 + coverage 校验，不通过则修 prompt + 补单测。

## 已知隐患
- 82 只无 roic/fcf 股票可能是东财 API 无数据的小盘股（C2.5 永久兜底）
- AKShare 利润表/现金流 API 永久挂（S4/S5 标"已挂"）
- `deep_research` 废表已清理（表不存在）
