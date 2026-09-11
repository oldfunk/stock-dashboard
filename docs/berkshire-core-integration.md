# AI Berkshire 算法核心融入研究（2026-09-07）

## 结论先行

上游框架层（skills/）自 08-29 era-alpha 后零更新，prompt 层我方已全吸收（B1-B8/B2-B7）。
真正的"算法核心"在上游 `tools/` 计算层，其中**两件值得融入**（C1 确定性终值验算 + C2
东财第二财务源），一件可选（C3 引用数字抽检），其余明确不做。

## 上游算法核心盘点（tools/，2026-09-07 实测）

| 工具 | 算法 | 我方现状 | 融入价值 |
|------|------|----------|----------|
| terminal_value.py（596 行） | 戈登模型终值 PE=(1-g/ROIC)/(r-g) + 十年 IRR + 三条 audit 硬约束（C1 币种一致/C2 分母≥5pct/C3 离散风险归属） | intrinsic_value 纯 LLM 心算（0%/10倍、3%/12倍、5%/15倍三档倍数无数学验证） | **高（C1）**：确定性验算 + 写库前纪律闸 |
| financial_rigor.py（465 行） | Decimal 市值/估值/表间交叉验算 | 已融入（verify_valuation V1/V1b/V2/V3） | 无（完成） |
| ashare_data.py（368 行） | 腾讯行情 + 东财 datacenter 公开 JSON API + suggest 搜索，stdlib only | 只有 Tencent + AKShare；B8 暴露单源脆弱 | **中（C2）**：第二财务源 fallback |
| report_audit.py（553 行） | 报告 15% 数据点抽样 vs 可靠信源比对，准出/打回 | AI 分析引用的数字从未 vs 库交叉 | 中（C3）：抽检 warn-only |
| stock_screener.py | 动量发现 + 价值验证 | 无 | **不做**：上游 09-03 自证动量无预测力 |
| morningstar_fair_value.py | Morningstar 公允价值 Top100 | 无 | **不做**：美股中心，A 股价值低 |
| xueqiu_scraper.py | 爬虫 | 无 | **不做**：用户红线 |

## C1 终值验算闸 P0（核心融入项）

问题：prompt 第 179 条让 LLM 用"5 年均 FCF 为基准，0%/10 倍、3%/12 倍、5%/15 倍"
手算三档估值——倍数本身是拍脑袋，且无任何程序校验。LLM 可在乐观档隐含 g>2%
（人民币上限）或 r-g<5pct（分母失效区），输出看起来像估值实则是放大偏见。

设计（对标上游 audit 三条，全部可 deterministic 实现，stdlib only）：

1. `scripts/verify_intrinsic.py`：输入任一只 code，读 financial_summary
  （roic_5y_avg、fcf 5 年均、总市值/流通市值）+ 快照现价，输出：
   - 戈登终值 PE 三档（悲/基/乐 g 取 -?/基准/基准+1pct，上游不对称规则照搬），
     r 用 CNY 区间 [6%, 9%] 中值 7.5%，RF=1.7%；
   - LLM 三档隐含倍数反解（intrinsic_value / 年化 Owner Earnings）vs 终值 PE
     对比，偏差 >50% 告警、>100% 或符号矛盾失败（口径沿用 V2 宽容设计）；
   - C2 体检：任一档 r-g<5pct → 该档标记"情景参考，不得当估值用"；
     C1 体检：LLM 隐含 g>2%（CNY 上限）→ 打回。
2. 写库前纪律（仿 `_enforce_verdict_discipline`，落在 `analyze_stock`）：
   乐观档隐含 g 超上限或分母失效 → 该档估值字段强制标注"分母失效，仅情景参考"，
   永不静默通过。(dto：不阻断落库，只改判标注——估值是观点，验算是标尺。)
3. 接入：`run_pipeline` 步骤 4.6（try/except 包裹永不阻断，仿 4.5）；
   单测 ≥6（戈登算式/三档不对称/C1 打回/C2 降级/反解偏差/空 FCF 诚实 SKIP）。
4. 口径陷阱（预先声明，仿 V2）：Owner Earnings 用 5 年均 FCF（含负年则该档 SKIP，
   不许拿单年 FCF 充数）；ROIC 用 5 年均（单年 ROIC 失真）；r-g 分母用小数，
   百分比换算错一位结果差十倍——单测必须覆盖单位换算。

验收：单测 + pi1 实测 20 只（报告每只三档 verdict 分布 + 打回/降级数）+
全仓无回归 + 零 emoji。合并部署走常规口径。

## C2 东财 datacenter 第二财务源 P0（2026-09-11 完成）

`_fetch_eastmoney_direct()` 直连 `datacenter.eastmoney.com/securities/api/data/get`
公开 JSON API（stdlib only，curl_get），仅当 `stock_yjbb_em` 抛异常时触发。
填充字段：ROE/毛利率/EPS/每股净资产/营收增长/净利增长/净利润。
单测 5 个（直接API/无效代码/代码格式/兜底触发/正常路径不变），全仓 218 passed。

## C3 AI 引用数字抽检 P2（可选）

仿 report_audit：每次复盘/AI 分析落库后，抽样正文中的数字断言
（如"ROE 25%"、"PE 11 倍"）vs financial_summary/快照交叉，偏差超阈值记
`actions_summary.numeric_mismatch`（warn-only，永不阻断）。
价值中、实现复杂度中（需数字抽取 + 口径对齐），排 P2，C1 落地后再议。

## 明确不做（2026-09-07）

- 动量/技术面：上游自证无预测力，我方不碰。
- Morningstar/雪球爬虫：前者无 A 股价值，后者红线。
- investment-team 多 Agent：pi 性能 + 免费配额撑不起（沿用 09-04 结论）。
- 上游研报跟进：只看 skills/ + tools/ 新增，不跟个股研报（沿用 09-04 结论）。

## 上游跟踪任务（常设）

上游 skills/ + tools/ 自 08-29 后零实质更新。本任务设为账本常设项：
每月初由 nightly 检查一次上游这两个目录的新增 commit，有新增才研判融入，
无新增则 ledger 记一笔 no-op（不水文档）。研报目录永远不看。
