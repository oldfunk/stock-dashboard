# Stock Dashboard — AI分析升级 + 实时更新 实施计划

> **约束：** 能不改就不改，现有功能尽量不动。
> **节奏：** 第一阶段（AI分析）→ 第二阶段（网页实时更新），互不依赖可独立部署。

**Goal:** 让 AI 分析跟踪股票回归历史、频次从日降到周、网页30秒内自动反映所有变化。

---

# Phase 1: AI 分析升级

## 改动概述

1. `ai_analyzer.py` — `analyze_stock()` 注入历史摘要（~20行）
2. `ai_analyzer.py` — `analyze_batch()` 增加 `returning_only` 模式（可选跳过已有近期分析的股票）
3. 调度/cron — AI 分析频次从每日改为每周
4. 测试 — 新增历史摘要格式化测试（~15行）

---

## Task 1.1: 历史摘要提取函数

**File:** `src/analyzer/ai_analyzer.py`（新增函数，~25行）

在 `_save_analysis` 上方添加：

```python
def _build_history_summary(stock_code: str, limit: int = 3) -> str:
    """从 stock_analysis_history 提取历史分析的关键行摘要。
    
    每条历史记录压缩为一行：日期 + Signal + 核心判断
    控制在 150 tokens 以内，避免 prompt 膨胀。
    """
    from src.models.database import StockAnalysisHistoryDAO
    records = StockAnalysisHistoryDAO().get_history(stock_code, limit=limit)
    if not records:
        return ""
    
    lines = []
    for r in records:
        date = r.get('analysis_date', '')[:10]
        trade_str = r.get('ai_trade_strategy', '{}')
        try:
            trade = json.loads(trade_str) if isinstance(trade_str, str) else trade_str
        except (json.JSONDecodeError, TypeError):
            trade = {}
        signal = trade.get('signal', '--')
        confidence = trade.get('confidence', '')
        # 从 ai_analysis 提取第一句作为核心判断
        analysis = r.get('ai_analysis', '')
        try:
            parsed = json.loads(analysis) if isinstance(analysis, str) else analysis
            core = str(parsed.get('analysis', ''))[:80] if isinstance(parsed, dict) else str(analysis)[:80]
        except (json.JSONDecodeError, TypeError):
            core = ''
        lines.append(f"  {date} {signal}{'('+confidence+')' if confidence else ''} | {core}")
    
    return "【过往分析记录】\n" + "\n".join(lines)
```

**验证：** 手动调用看输出格式是否符合预期（~150 tokens 内）

---

## Task 1.2: 在 analyze_stock 中注入历史摘要

**File:** `src/analyzer/ai_analyzer.py`（修改 `analyze_stock()`，+5行）

在 `ANALYSIS_PROMPT.format(...)` 之后，`self._call_llm(prompt)` 之前插入：

```python
# 注入历史分析摘要（仅当存在过往记录时）
history_summary = _build_history_summary(stock.get('code', ''))
if history_summary:
    prompt += "\n\n" + history_summary + """
\n在本次分析中，关注以下问题：
1. 你过往的判断是否仍然成立？哪些条件变了？
2. 过去哪次判断被市场验证了、哪次被打脸了？
3. 本次结论相比之前是否有转变？为什么？
"""
```

**设计决策：** 历史摘要只给事实不给结论——prompt 引导 AI 做"对比差异"而非"继承旧观点"，防止锚定效应。

---

## Task 1.3: AI 分析从每日改为每周

**不动 scheduler（每日流水线保持），只改 cron 频次和 `ai_analysis_cron.sh`：**

### 1.3a 修改 cron 脚本 (`scripts/ai_analysis_cron.sh`)

```bash
#!/usr/bin/env bash
# 周度 AI 分析 cron 脚本（价值投资：周级节奏）
# 安装：crontab -e
# 0 16 * * 1 /home/debian/stock-dashboard/scripts/ai_analysis_cron.sh >> /var/log/stock-dashboard-ai.log 2>&1
```

改变：`*/30 16-23 * * 1-5` → `0 16 * * 1`（每周一 16:00）

### 1.3b 同时保留手动触发能力

现有 `scripts/run_ai_analysis.py --all` 保持不变，随时可手动跑。

现有 crontab 条目：
```
# 旧（日级）：
*/30 16-23 * * 1-5 /home/debian/stock-dashboard/scripts/ai_analysis_cron.sh

# 新（周级）：
0 16 * * 1 /home/debian/stock-dashboard/scripts/ai_analysis_cron.sh
```

---

## Task 1.4: 新增 / 修改测试

**File:** `tests/analyzer/test_ai_analyzer.py`（新增 ~15行）

```python
def test_build_history_summary_no_records():
    """无历史记录时返回空字符串"""
    from src.analyzer.ai_analyzer import _build_history_summary
    result = _build_history_summary("NONEXIST")
    assert result == ""

def test_build_history_summary_format():
    """有历史记录时格式正确、不含换行污染"""
    # 先插入一条测试记录
    from src.models.database import StockAnalysisHistoryDAO, db_conn
    StockAnalysisHistoryDAO().save("000001", "test_run", 80, 
        json.dumps({"analysis": "公司roe稳定15%+，负债率持续下降，估值合理。"}),
        json.dumps({"signal": "BUY", "confidence": "高"}))
    
    result = _build_history_summary("000001", limit=1)
    assert "【过往分析记录】" in result
    assert "BUY" in result
    assert "高" in result or "高" in result
    # 总长度控制在合理范围
    assert len(result) < 500
```

---

## Phase 1 验证清单

- [ ] `_build_history_summary` 无记录返回空串
- [ ] 有记录时格式为 `日期 信号(置信度) | 核心判断`
- [ ] 总长度 < 500 chars（~100 tokens）
- [ ] `analyze_stock` 传入含历史记录的股票时 prompt 追加了摘要段
- [ ] crontab 改为每周一 16:00
- [ ] 测试通过

---

# Phase 2: 网页实时更新

## 改动概述

1. **新建** `src/web/templates/_stock_list.html` — 从 `index.html` 抽出候选股列表的 Jinja2 部分
2. **修改** `src/web/routes.py` — 新增 `/api/stocks/html` 端点，返回渲染好的股票列表 HTML
3. **修改** `src/web/templates/index.html` — renderStocks 改为全量替换 + run_id 检测（~20行）
4. **修改** `index.html` — 原 stock-list 区域替换为 `{% include '_stock_list.html' %}`

---

## Task 2.1: 拆出 partial template

**Create:** `src/web/templates/_stock_list.html`

从 `index.html` 抽出从 `{% if stocks %}` 到 `{% endif %}` 之间的股票列表部分（约 200 行），包含：
- `section-header`（选股结果 N 只）
- `dashboard-layout`（每只股票的完整卡片：指标、tab、分析内容、历史）
- 不包含 market row、status bar、progress bars、sidebar、footer

**File:** `src/web/templates/index.html`（修改）

原处替换为：
```jinja2
{% include '_stock_list.html' %}
```

---

## Task 2.2: 新增 `/api/stocks/html` 端点

**File:** `src/web/routes.py`（+15行）

```python
@router.get('/api/stocks/html')
async def stocks_html(request: Request):
    """返回渲染好的股票列表 HTML（供前端全量替换）"""
    dao = ScreeningResultDAO()
    stocks = dao.get_active(run_id=None)  # 使用与 index 路由相同的查询
    run = dao.get_latest_run()
    
    # 对每只股票补充 ai_parsed / trade_parsed / _summary / analysis_history
    enrich_stocks_data(stocks)  # 复用现有 enrichment 逻辑
    
    template = Jinja2Templates(directory=TEMPLATE_DIR)
    html = template.get_template('partials/_stock_list.html').render(
        stocks=stocks,
        run_log=run,
        request=request,
    )
    return HTMLResponse(html)
```

**注意：** 需要把 `enrich_stocks_data` 逻辑从 index 路由拆成可复用的函数。或者直接在 `/api/stocks/html` 内重复一遍 enrichment。

```python
# 现有 index 路由中的 enrichment 逻辑，提取为独立函数
def _enrich_stocks(stocks: list[dict]) -> list[dict]:
    """补充 ai_parsed / trade_parsed / _summary / analysis_history"""
    for s in stocks:
        # 解析 JSON 字符串
        for key in ['ai_analysis', 'ai_trade_strategy']:
            if isinstance(s.get(key), str):
                try:
                    s[key] = json.loads(s[key])
                except (json.JSONDecodeError, TypeError):
                    pass
        s['ai_parsed'] = s.get('ai_analysis')
        s['trade_parsed'] = s.get('ai_trade_strategy')
    # 财务汇总
    try:
        fs_dao = FinancialSummaryDAO()
        for s in stocks:
            fs = fs_dao.get(s['code'])
            if fs:
                s['_summary'] = fs
    except Exception:
        pass
    # 分析历史
    try:
        hist_dao = StockAnalysisHistoryDAO()
        for s in stocks:
            history = hist_dao.get_history(s['code'], limit=5)
            if history:
                s['analysis_history'] = history
    except Exception:
        pass
    return stocks
```

---

## Task 2.3: 修改前端 renderStocks

**File:** `src/web/templates/index.html`（~20行 JS 改动）

```javascript
// 追踪最后的 run_id，用于检测新流水线
var lastRunId = null;

function renderStocks(data) {
    var listEl = document.getElementById('stockList');
    if (!listEl || !data) return;
    
    // 检测是否有 HTML 标记（来自 /api/stocks/html）
    if (data._html) {
        // run_id 变化 → 新流水线完成 → 全量刷新
        if (data._run_id && data._run_id !== lastRunId) {
            lastRunId = data._run_id;
            window.location.reload();
            return;
        }
        // 无 run_id 变化 → 只替换股票列表内容
        listEl.innerHTML = data._html;
        return;
    }
    
    // 兼容旧格式：只更新价格（fallback）
    if (!data.length) return;
    var header = document.querySelector('.section-count');
    if (header) header.textContent = data.length + ' 只';
    data.forEach(function(s) {
        var priceEl = document.getElementById('price-' + s.code);
        var chgEl = document.getElementById('chg-' + s.code);
        if (priceEl && s.current_price != null) {
            priceEl.textContent = s.current_price.toFixed(2);
        }
        if (chgEl && s.change_percent != null) {
            var cls = s.change_percent >= 0 ? 'up' : 'down';
            var arrow = s.change_percent >= 0 ? '&#9650;' : '&#9660;';
            chgEl.innerHTML = '<span class="' + cls + '">' + arrow + ' ' +
                (s.change_percent >= 0 ? '+' : '') + s.change_percent.toFixed(2) + '%</span>';
        }
    });
}
```

**/api/stocks 端点同时返回 _html + _run_id：**

```python
# 在 /api/stocks 末尾追加
html_template = Jinja2Templates(directory=TEMPLATE_DIR)
stocks_html = html_template.get_template('partials/_stock_list.html').render(
    stocks=stocks, run_log=run, request=request)
return {
    "_html": stocks_html,
    "_run_id": run['run_id'] if run else None,
    "stocks": stocks,  # 兼容旧 JS
}
```

---

## Task 2.4: 重启验证

```bash
systemctl restart stock-dashboard.service
journalctl -u stock-dashboard.service --no-pager -n 20
```

打开看板确认：
- [ ] 初始加载正常渲染全部股票
- [ ] 30s 后触发 `/api/stocks`，返回 `_html` 字段
- [ ] renderStocks 替换 stockList.innerHTML
- [ ] 股票价格变化时实时更新
- [ ] AI 分析完成后（从无到有）tab 自动出现
- [ ] 新流水线 run_id 变化时触发 reload

---

## Phase 2 验证清单

- [ ] `_stock_list.html` 渲染内容与原模板一致（diff 仅 whitespace）
- [ ] `/api/stocks/html` 返回有效 HTML
- [ ] 前端 30s 轮询成功替换列表
- [ ] run_id 变化及时 reload
- [ ] 降级路径：旧格式数据仅更新价格（不崩溃）
- [ ] 78 个已有测试全过

---

# 实施顺序

```
Phase 1 ─┬─ Task 1.1 历史摘要函数
          ├─ Task 1.2 prompt 注入
          ├─ Task 1.3 改为周频
          ├─ Task 1.4 测试验证
          └─ 确认后推送

Phase 2 ─┬─ Task 2.1 partial template
          ├─ Task 2.2 后端端点 + enrich_stocks 抽离
          ├─ Task 2.3 前端 renderStocks 改造
          ├─ Task 2.4 重启验证
          └─ 确认后推送
```

两阶段互不依赖，每阶段完成后可独立部署。
