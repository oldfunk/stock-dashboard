# 默认通用交易策略（general，2026-11-28）

> 这是 AI 交易员的**默认基线**，不是"唯一正确"或收益承诺。用户可以切到 `custom`
> 用自己的风格；AI 可以在框架风控内调整节奏。框架只负责安全/执行/账本。
> 唯一真相源：`paper_trading/strategy/general_playbook.py` 的 `GENERAL_RULES`。

## 1. 为什么更新旧默认

旧默认是 **MA5/20 金叉死叉**（`general` 方案 desc 明确写 MA5/20）。问题：

- 5/20 在 A 股震荡市里噪音大，金叉追高、死叉割在反弹前；
- 只看均线，不看质量/估值/相对强度，容易买到弱势股；
- 没有明确的仓位、止损、时间止损，AI 每轮自由发挥。

新默认改为 **中线趋势 + 质量 + 风险**，10–30 个交易日，不作为日内策略。

## 2. 默认规则（AI 必须遵守，框架风控另有一层）

1. **趋势**：收盘 > MA20 且 MA20 > MA60（或 MA20 斜率向上）；高于 MA20 超过 5% 不追。
2. **质量/估值**：ROE 5 年均值 ≥8%，PE ≤35 或低于行业，负债率 ≤70%，经营现金流为正；ST/停牌不碰。
3. **相对强度**：20 日涨幅不低于沪深300，或放量突破 20 日新高；都没有时宁可 hold。
4. **仓位**：单票 ≤25%，总仓位 ≤90%，现金 ≥10%，最多 5 只；首笔 1/3–1/2 目标仓位，禁止补跌摊薄。
5. **离场**：−7% 硬止损或跌破 MA20/结构低点（先到先执行）；+15% 止损上移成本；+25% 后回撤 8% 止盈；20 个交易日无进展时间止损；论点破坏立即离场。
6. **条件单**：回踩买入/止损/止盈用 `price_below/price_above`，有效期 ≤10 个交易日，不与直接动作同标同向重复。
7. **健康度**：20 日超额/换手/费用是参考；`demote` 时必须在计划里说明继续/放缓/停手的理由，**框架不自动拦买**。
8. **可审计**：每条 buy/sell 必须给 `basis/thesis/catalyst/risk_stop/expected_hold_days`；每只 hold 说明“什么情况就卖”。

## 3. 实现位置

| 位置 | 内容 |
|---|---|
| `paper_trading/strategy/general_playbook.py` | `GENERAL_RULES`（唯一真相源） |
| `paper_trading/strategy/schemes.py` | `general` 方案：title/desc/signal 20/60/rules |
| `paper_trading/agent/prompts.py` | `SCHEME_TMPL` 注入 `{rules}` |
| `paper_trading/agent/loop.py` | `_scheme_txt()` 把方案规则送进 AI prompt |
| `config.yaml` | `strategy.short_window: 20`、`long_window: 60`（fallback 对齐） |

## 4. 边界：默认 ≠ 强加

- 默认规则是**起点**；用户切 `custom` 或改 `strategy.local.yaml` 即覆盖；
- AI 可以在默认规则内调整选股/节奏/仓位（受风控上限）；
- 框架不因为“20 日跑输”自动停手；`health_policy=enforce` 只能由用户显式开启；
- 默认规则不进入撮合/风控/账本，L1 仍由 rqalpha + 账本决定。

## 5. 验收

- `general` 方案 `signal=20/60`，prompt 含 `方案规则` 与完整手册；
- 全量测试全绿；
- 生产 `TradingBridge.strategy` 为 `CompositeStrategy(ma_cross)`，内层 `MACrossStrategy` 为 **MA20/60**；
- 下一次计划（11-30 晚生成 12-01 计划）使用新默认；已存在的计划 #32 不受影响。

## 6. 未做 / 后续

- 没有用历史数据回测这套默认规则的收益（避免把“默认”说成“验证过”）；
- 健康度进 AI 上下文与 `health_policy` 仍属 I5c；
- 多策略资金分配仍属 I5c。
