# AI Berkshire 照搬规格书

> 2026-09-07 产出。对照上游 `docs/berkshire/skills/` 21 个文件 + `tools/berkshire/` 10 个工具，逐项标注我方现状与照搬动作。
> 上游文件已全量镜像（`git b32c743` commit），本规格书作为实施路线图。

---

## 差异 ① quality-screen：10年口径 vs 我方5年口径

### 上游原文要点

| 上游项 | 原文 | 出处 |
|--------|------|------|
| 规则1 ROE | **10年平均ROE < 8%** 排除 | `quality-screen.md` 第1行去劣指标表 |
| 规则5 OCF/NI | **经营现金流/净利润（5年均值）< 0.7** 排除 | `quality-screen.md` 第5行 |
| 豁免A | 上市**不足10年**，且高增长+OCF转正 | `quality-screen.md` 豁免A |
| 数据口径 | **近10年逐年ROE计算均值**，近10年净利率趋势 | `quality-screen.md` 执行流程 |
| 豁免C | ROE > 20% 可豁免毛利率/净利率不达标 | `quality-screen.md` 豁免C |

### 我方现状（部分对齐）

| 我方项 | 当前值 | 文件位置 | 差异 |
|--------|--------|----------|------|
| 规则1 ROE | **5年平均ROE < 8%** 排除 | `src/screener/value_screener.py:59-73` | **口径差5年**：上游要10年 |
| 规则5 OCF/NI | 用 **OCF/股>0 + OCF正年数≥3** 代理 | `value_screener.py:75-95` | **口径不同**：上游要OCF/NI≥0.7，我方用代理 |
| 豁免A | 上市**不足12年**，高增长+OCF转正 | `value_screener.py:66-69` | 12年 vs 10年 |
| 豁免C | 未实现 | — | **缺失** |
| 3条豁免全量 | 仅豁免A、B实现 | `value_screener.py` | 缺豁免C |

### 照搬动作

1. **`src/screener/value_screener.py`** 新增字段 `roe_10y_avg`（如果 DB 有）或标注"当前仅5年可用"
   - `roe_5y_avg` → `roe_10y_avg` 需 DB 支持；如果 DB schema 只有 5 年列 → 保持 5 年 + 在输出中标注"口径：5年（上游要求10年）"
   - 规则1 增加 `if roe_10y is not None: use roe_10y else: use roe_5y with warning`
2. **规则5** 增加 OCF/NI 精确计算：`sum(OCF_5y) / sum(NI_5y)`，与现有 OCF/股代理并存
3. **豁免C**（高周转薄利）：新增逻辑 `if roe > 20 and gross_margin < 20 and net_margin < 5: pass with note`
4. **豁免A** 年限从 12 改 10（如果 DB `data_years` 字段支持）
5. 输出表格增加列 `roe_10y`、`ocf_ni_ratio`（如果数据可得）

### 依赖

- `src/models/database.py` `ai_stock_analysis` 表需新增 `roe_10y_avg`、`ocf_5y_sum`、`ni_5y_sum` 列
- `src/collector/akshare_fetcher.py` 需返回 10 年财务数据（当前 `get_financial_history` 返回 5 年）
- **优先级 P1**（不阻塞写入，仅提升筛选精度）

---

## 差异 ② financial-data：A 股东财主+巨潮副双源+误差>1%标记+前复权

### 上游原文要点

| 上游项 | 原文 | 出处 |
|--------|------|------|
| 双源原则 | **每个关键数据必须来自两个独立来源** | `financial-data.md` 开头 |
| A股主源 | 东方财富（push2his.eastmoney.com） | `financial-data.md` A股表 |
| A股副源 | **巨潮资讯** cninfo.com.cn 原始年报/季报PDF | `financial-data.md` A股表 |
| 误差规则 | ≤1% ✅ 一致；1%~5% ⚠️ 标记；>5% ❌ 排除 | `financial-data.md` 第二步 |
| 前复权 | 历史股价/N年涨幅/历史PE band **一律前复权**；同一分析内不得混用 | `financial-data.md` 复权规则 |
| 台股ADR | 1 TSM ADR = 5 股 2330，汇率/存托比率差异 | `financial-data.md` 台股说明 |

### 我方现状（部分对齐）

| 我方项 | 当前值 | 文件位置 | 差异 |
|--------|--------|----------|------|
| 数据源 | akshare 封装东财接口（push2his） | `src/collector/akshare_fetcher.py:970` | **缺巨潮副源** |
| 误差校验 | 无双源误差校验 | — | **缺失** |
| 前复权 | akshare `adjust=qfq` 前复权默认 | `akshare_fetcher.py:965` | **已对齐** ✅ |
| 复权一致性 | 无"同一分析内不得混用"校验 | — | 部分缺失 |
| 数据源标记 | `data_source TEXT DEFAULT 'eastmoney'` | `src/models/database.py:190` | 单源，无副源字段 |

### 照搬动作

1. **`src/models/database.py`** `financial_history` 表新增副源字段：
   - `eastmoney_value DECIMAL(12,4)`（主源）
   - `cninfo_value DECIMAL(12,4)`（副源，nullable）
   - `data_diff_pct DECIMAL(5,2)`（误差率，nullable）
   - `data_status TEXT DEFAULT 'unverified'`（unverified/verified/flagged/excluded）
2. **`src/collector/akshare_fetcher.py`** 新增 `fetch_with_cninfo()` 函数：
   - 主源：东财 push2his（现有 akshare 调用）
   - 副源：巨潮 API（需研究 cninfo 的公开 API 结构；当前用网页爬取，`akshare` 也提供 `stock_financial_abstract` 等间接方式）
   - 如果副源不可得 → `cninfo_value=NULL, data_status='single_source'`
3. **`src/analyzer/ai_analyzer.py`** ANALYSIS_PROMPT 增加：
   - "数据交叉验证"模块：调用 `financial-data.md` 的误差规则
   - 如果 `data_diff_pct > 5%` → `data_status='excluded'`，分析中标注"主副源偏差>5%，数据不可信"
   - 如果 `1% < data_diff_pct ≤ 5%` → `data_status='flagged'`，正常分析但附注差异
4. **前复权一致性**：新增 `is_adjustment_consistent()` 校验函数
   - 检查同一分析中所有历史价格是否都用了前复权
   - 如果混用 → 抛出 warning

### 依赖

- 巨潮 API 研究（`akshare` 有 `stock_financial_abstract_ths` 等函数，但需验证列级数据）
- **优先级 P2**（数据质量提升，不阻塞核心分析）

---

## P0/P1/P2 实施顺序

| 优先级 | 事项 | 文件 | 状态 |
|--------|------|------|------|
| **P0** | C1 终值验算闸（terminal_value.py 接入 pipeline） | `src/analyzer/ai_analyzer.py` + `scripts/verify_intrinsic.py` | ✅ 已完成 |
| **P0** | ANALYSIS_PROMPT 补全缺失模块（生意本质/文明趋势/反向DCF/决策备忘录/偏见清单） | `src/analyzer/ai_analyzer.py` | ✅ 已完成 |
| **P0** | tools/berkshire 全量落盘（10个工具） | `tools/berkshire/` | ✅ 已完成 |
| **P0** | docs/berkshire/skills/ 全量镜像（21个skill） | `docs/berkshire/skills/` | ✅ 已完成 |
| **P1** | quality-screen 10年口径对齐 | `src/screener/value_screener.py` + DB | 待实施 |
| **P1** | 豁免C（高周转薄利）实现 | `src/screener/value_screener.py` | 待实施 |
| **P1** | `verify_intrinsic.py` 接入 analyze_stock 写库前改判 | `src/analyzer/ai_analyzer.py` | 待接线 |
| **P2** | financial-data 东财主+巨潮副双源 | `src/collector/akshare_fetcher.py` + DB | 待实施 |
| **P2** | 误差>1%标记 + 前复权一致性校验 | `src/analyzer/ai_analyzer.py` + `src/screener/` | 待实施 |
| **P2** | `quality-screen.md` 中"10年"字段的兜底策略（DB无10年列时降级为5年+标注） | `src/screener/value_screener.py` | 待实施 |

---

## 上游其余 19 个 skill 现状总结

| Skill | 对齐状态 | 说明 |
|--------|----------|------|
| bottleneck-hunter | ✅ 部分 | 5年趋势确认逻辑我方有雏形 |
| deep-company-series | ✅ 部分 | 8主轴模板我方 `ai_analyzer.py` 模块有覆盖 |
| dyp-ask | ⚠️ 需要订阅key | 需段永平独家素材，无法直接执行 |
| earnings-review | ✅ 对齐 | 5步流程我方有 |
| earnings-team | ✅ 对齐 | 4并行Agent我方有 |
| era-alpha | ✅ 对齐 | 5步+报告结构我方有 |
| financial-data | ⚠️ P2差异 | 见上方差异② |
| income-investment | ✅ 对齐 | 现金流追踪框架我方有 |
| industry-funnel | ✅ 对齐 | 5条硬指标我方有 |
| industry-research | ✅ 对齐 | 逻辑链+产业链我方有 |
| investment-checklist | ✅ 对齐 | 6关Checklist我方有 |
| investment-research | ✅ 对齐 | 6步研究框架（段永平+巴菲特+芒格+李录）我方有 |
| investment-team | ✅ 对齐 | 4并行Agent我方有 |
| management-deep-dive | ✅ 对齐 | CEO能力圈+诚信我方有 |
| news-pulse | ✅ 对齐 | 4侦察Agent我方有 |
| portfolio-review | ✅ 对齐 | 5体检维度我方有 |
| private-company-research | ⚠️ 规模大 | 22KB，超复杂公司深度研究；我方无此模块 |
| quality-screen | ⚠️ P1差异 | 见上方差异① |
| thesis-drift | ✅ 对齐 | 漂移检测我方有（watchlist_reviewer） |
| thesis-tracker | ✅ 对齐 | 200字投资论文+红线清单我方有 |
| wechat-article | ⚠️ 非核心 | 微信公众号文章写作，我方不产出 |

---

## 校验流程（照搬上游）

上游每个 skill 的执行校验：
1. **前置步骤**：AI研究偏见自觉（必须执行）→ 我方 `investment-research` 模块已含
2. **数据可得性评级**：对每个数据源评分（可得/部分/不可得）→ 我方缺此步骤
3. **交叉验证**：双源误差≤1% → 我方 P2 待补
4. **输出校验**：上游每个 skill 有输出格式模板 → 我方 ANALYSIS_PROMPT 已有
5. **退出条件**：上游每个模块有"何时停止"规则 → 我方部分有

