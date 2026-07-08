# AI Berkshire 对齐 — Prompt V2

## 设计原则
1. **结构但不八股** — 让 LLM 逐项评估护城河/管理层/估值，但 analysis 字段仍保持口语化笔记
2. **数据驱动** — 每项判断必须有数据依据，不能空谈
3. **可验证** — JSON 输出新增结构化字段，方便前端展示和后续比对

## 新字段映射表

| JSON 字段 | 改动 |
|---|---|
| analysis | 保持口语笔记 + 内嵌结构化评估 |
| moat_evaluation | **新增** — 5项护城河逐项评分+趋势 |
| management_score | **新增** — 资本配置评分 (1-10) |
| intrinsic_value | **新增** — 保守/基准/乐观估值 |
| reverse_thinking | 保持 |
| investment_strategy | 保持 |
| trade_strategy | 保持 |
| mirror_counts | 保持 |

## Prompt 变更对比

### 数据输入新增
现有字段 + 以下新传数据：

| 传入字段 | 来源 | 用途 |
|---|---|---|
| roe_10y_avg, net_margin_10y_avg | financial_summary | 长期趋势 |
| fcf_5y_sum, fcf_10y_sum | financial_summary | FCF规模 |
| fcf_positive_years_10 | financial_summary | FCF稳定性 |
| roe_volatility | financial_summary | ROE稳定性 |
| roe_improvement | financial_summary | 改善趋势 |
| roic_5y_avg, roic_10y_avg | financial_summary | 资本回报 |
| share_dilution_5y, share_dilution_10y | financial_summary | 股本变化 |
| intcov_5y_avg, intcov_10y_avg | financial_summary | 偿债能力 |
| 预计算：roe_roic_gap | roe_5y - roic_5y | 杠杆依赖度 |
| 预计算：fcf_yield | (fcf_5y_sum/5)/market_cap | FCF收益率 |

### 分析三段新增内容

**1. 护城河结构化评估（5项）**
- 转换成本：客户迁移代价有多大？
- 网络效应：用户增长是否创造更多价值？
- 无形资产：品牌溢价多少？专利有效期？牌照壁垒？
- 成本优势：规模效应/区位优势/工艺领先
- 有效规模：市场自然寡头还是竞争激烈？

**2. 管理层资本配置**
- 再投资效率：ROIC 趋势是升是降？留存利润是否创造了等量价值？
- 股份处置：稀释/回购趋势？高价还是低价操作？
- 财务杠杆：负债水平是否合理？利息覆盖是否安全？

**3. 内在价值估算**
- 基准 Owner Earnings ≈ 近 5 年平均 FCF
- 保守/基准/乐观三档 DCF 估值
- 安全边际百分比
- 相对国债收益率比较