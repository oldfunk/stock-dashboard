# AI Berkshire 深度对齐 实施计划

> **For agentic workers:** 按本 plan task-by-task 实施。步骤使用 checkbox (`- [ ]`) 语法追踪。每步都应是短且快的单文件/单区块改动，可独立 commit、独立验证。

**Goal:** 把 AI-Berkshire 的分析理论补全到本项目，聚焦上一轮审查发现的 4 个缺口：

1. 信息丰富度评级 A/B/C（防"资料多=确定"幻觉）
2. 六关 Checklist（能力圈/好生意/护城河/管理层/安全边际/纪律）
3. 真镜子测试（升级现有 mirror_counts 转折词计数）
4. 快速否决红线（8 条一票否决 + 观察池联动）

**Architecture:** 全部改动在 Prompt + 模板层完成，**不改流水线结构**（遵循 `.hermes/prompt-v2-design.md` 已固化的原则）。依赖事实：

- `ai_analysis` 是 TEXT 列存完整 JSON → 加新字段零 DB 迁移、无 ALTER TABLE
- `parse_ai_response` 只校验 3 个 required 字段（analysis/investment_strategy/trade_strategy），其它字段缺失只是不渲染 → 新字段完全向后兼容
- 模板全部用 `ai_parsed.xxx|default('--')` 容错渲染 → 字段不存在不报错

**Tech Stack:** Python 3.11 + FastAPI + Jinja2 + SQLite + httpx（复用 `FreeModelPool`）

**Review 依据:**
- 上游理论: `skills/investment-checklist.md` / `skills/quality-screen.md` / `skills/investment-team.md`（AI-Berkshire 仓库）
- 本项目现状: `src/analyzer/ai_analyzer.py`（ANALYSIS_PROMPT / parse_ai_response）/ `src/web/templates/stock_detail.html` / `src/analyzer/watchlist_reviewer.py`

---

## 文件结构

### 修改文件

| 文件 | 改动 |
|---|---|
| `src/analyzer/ai_analyzer.py` | ANALYSIS_PROMPT 逐阶段增加 info_richness / checklist / mirror_test / veto_checklist 字段定义与指导 |
| `src/web/templates/stock_detail.html` | 逐阶段增加对应渲染区块（服务端容错 + CSS） |
| `src/analyzer/watchlist_reviewer.py` | 阶段 D：新增第 5 条硬规则 `check_veto_triggered`，复用 `_apply_hard_rules` |
| `docs/roadmap.md` | 每个阶段完成后同步变更记录 |

### 不作改动（明确排除）

- `src/models/database.py` / schema：无需迁移
- `src/web/routes.py`：新字段由 `ai_parsed` 原样透传，无需改
- `src/screener/value_screener.py`：量化门规已对齐，不在本计划范围

---

## Task 1: spec 文档创建（留存基线）

### Step 1.1: 创建本文件

- [x] 本 plan 文档已创建（即本文件），作为对齐工作的留存基线。后续每步完成后用 checkbox 追踪。

---

## Task 2: 阶段 A — 信息丰富度评级 A/B/C

目标：让 AI 每只股票先评级信息丰富度（A/B/C），数据有限时强制标注置信度受限，防止"资料多=确定性高"的幻觉。

### Step 2.1: prompt 增加 `info_richness` 字段

- [x] 编辑 `src/analyzer/ai_analyzer.py` 的 `ANALYSIS_PROMPT`：
  - 在 JSON 结构中新增字段 `info_richness`：`{ "grade": "A 或 B 或 C", "basis": "评级依据（数据覆盖年限、指标完整度）" }`
  - 增加指导：数据覆盖年限 ≤3 年或多项核心指标缺失 → C 级，且 C 级时 confidence 不得超过"低"、intrinsic_value 需标注"数据不足，估值参考性有限"

### Step 2.2: 模板渲染信息丰富度徽章

- [x] 编辑 `src/web/templates/stock_detail.html`：在 AI 分析章节顶部（moat 之前）加"信息丰富度"区块：grade 徽章（A 绿 / B 黄 / C 红）+ basis 一句。缺失时整块不渲染

### Step 2.3: 复跑验证 + roadmap 同步

- [x] 用 `scripts/retry_ai.py` 对 1 只候选股复跑 AI 分析，确认 `info_richness` 落库且页面显示
- [x] `docs/roadmap.md` 变更记录表追加阶段 A 行

---

## Task 3: 阶段 B — 六关 Checklist

目标：六关（能力圈/好生意/护城河/管理层/安全边际/纪律）各给 ★1-5 评分 + 一行依据，作为单块展示的汇总视图，不推翻现有 moat/management/估值单块。

### Step 3.1: prompt 增加 `checklist` 字段

- [x] 编辑 `ANALYSIS_PROMPT`：新增 JSON 字段 `checklist`：

```json
"checklist": {
    "circle_of_competence":  {"score": 1-5, "note": "一句话能否说清生意 + 是否理解"},
    "good_business":         {"score": 1-5, "note": "经济特征：ROE/毛利/FCF 综合"},
    "moat":                  {"score": 1-5, "note": "取 moat_evaluation 汇总"},
    "management":            {"score": 1-5, "note": "取 management_score 汇总"},
    "margin_of_safety":      {"score": 1-5, "note": "当前价相对内在价值折让"},
    "discipline":            {"score": 1-5, "note": "仓位纪律/买入论述是否清晰"}
}
```

- [x] 增加指导：护城河/管理层/安全边际三关必须与 moat_evaluation / management_score / intrinsic_value 保持一致；能力圈与纪律是新增判断

### Step 3.2: 模板渲染六关评分卡

- [x] 编辑 `stock_detail.html`：新增"六关 Checklist"区块，横向 6 卡，每卡：关名 + ★评分（★ 符号渲染实数）+ note 一行。分数 ≥3 绿色、≤2 红色

### Step 3.3: 复跑验证 + roadmap 同步

- [x] `scripts/retry_ai.py` 复跑 1 只，确认 `checklist` 六关全落库、与单块字段一致（抽查一致性）
- [x] `docs/roadmap.md` 变更记录表追加阶段 B 行

---

## Task 4: 阶段 C — 真镜子测试

目标：把现有 `mirror_counts`（转折词计数，是探测不是测试）升级为 investment-checklist.md 的 5 句镜子测试 + 通过/未通过判定。

### Step 4.1: prompt 升级 `mirror_test`

- [x] 编辑 `ANALYSIS_PROMPT`：`mirror_counts` 保留（用于统计），新增 `mirror_test`：

```json
"mirror_test": {
    "statements": ["我以___元买入___公司，因为这门生意的本质是___，我理解它",
                   "它的护城河是___，而且在变宽/变窄",
                   "管理层___，值得/不值得信赖",
                   "当前价格相当于内在价值的___折，有/无足够安全边际",
                   "即使我错了，下行风险可控/不可控，因为___"],
    "passed": true/false,
    "missing": ["缺失或敷衍的句子编号列表"]
}
```

- [x] 指导：5 句缺任意一句或含转折词超限 → passed=false；"5 句话说不完整 = 不买"

### Step 4.2: 模板渲染镜子测试

- [x] 编辑 `stock_detail.html`：新增"镜子测试"区块：每句一行（缺句标红），底部通过/未通过徽章
- [x] 说明：旧数据只有 mirror_counts 的不用改，新字段缺失时整块不渲染

### Step 4.3: 复跑验证 + roadmap 同步

- [x] 复跑验证 `mirror_test` 落库 + 页面渲染（含 passed=false 样例），roadmap 同步

---

## Task 5: 阶段 D — 快速否决红线

目标：8 条一票否决红线（投资-checklist 第五步），任一触发 → signal 强制 AVOID，并在周六复盘联动调出观察池。

### Step 5.1: prompt 增加 `veto_checklist`

- [x] 编辑 `ANALYSIS_PROMPT`：新增 `veto_checklist`，8 条逐条 ✅/❌：

```json
"veto_checklist": {
    "cannot_explain_business": false,
    "negative_fcf_3y_no_improvement": false,
    "management_integrity_issue": false,
    "moat_eroding_irreversibly": false,
    "greater_fool_required": false,
    "cannot_afford_total_loss": false,
    "following_the_herd": false,
    "cannot_write_200_char_thesis": false,
    "triggered_count": 0
}
```

- [x] 指导：任一 true → trade_strategy.signal 必须为 AVOID 且 confidence 不得为高；triggered_count = true 数量

### Step 5.2: 模板渲染否决区块

- [x] 编辑 `stock_detail.html`：新增"快速否决"区块：8 条红线逐条显示，触发项标红，顶部显示 triggered_count；与 Signal=AVOID 的一致性提示

### Step 5.3: 观察池联动（第 5 条硬规则）

- [x] 编辑 `src/analyzer/watchlist_reviewer.py`：
  - 新增 `check_veto_triggered(stock, analysis)`：解析 vet_checklist，triggered_count ≥1 → 强制调出
  - 在 `_apply_hard_rules` 中追加调用，违规理由加 `veto_triggered`
  - 新增对应单元测试（参照现有 4 条规则测试）

### Step 5.4: 端到端验证 + roadmap 同步

- [x] 端到端：复跑 AI 分析 → 手动复盘一次，验证 vetoe 触发股票被调出、理由正确
- [x] run 单测 `pytest tests/analyzer/test_watchlist_reviewer.py`
- [x] `docs/roadmap.md` 变更记录表追加阶段 D 行 + 本计划所有 checkbox 打勾

---

## 完成定义（Definition of Done）

- [x] 阶段 A/B/C/D 各有一次成功复跑证据（retry_ai.py 输出 + 详情页截图/字段确认）
- [x] watchlist_reviewer 新增测试全绿
- [x] roadmap.md 变更记录表完整
- [x] 全部改动保持向后兼容（旧 ai_analysis 数据页面不报错）