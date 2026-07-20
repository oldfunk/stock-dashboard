# AI 观察池 + 投资笔记 设计文档

- **创建日期**: 2026-07-20
- **作者**: oldfunk + Trae
- **状态**: 设计通过，待实施

---

## 1. 背景与动机

### 现状
当前 stock-dashboard 每日 15:30 跑流水线，从全 A 股筛选出 Top 20 候选股展示在网页上。每周五 AI 分析每只候选股，输出护城河 / 估值 / Signal 等结构化字段。

### 痛点
1. **Top 20 每日变动无序**：股票每天进出榜单，用户被迫频繁切换关注点，违反价值投资"长期持有"的理念
2. **AI 没有主见**：AI 只是被动分析每只候选股，不会主动选择 / 调整观察池
3. **网页仅是榜单**：信息密度高但无叙事性，无法辅助用户理解"为什么"

### 终极目标
逐步演进为 AI 辅助炒股甚至接管的工具。本次设计是迈向该目标的第一步——让 AI 真正"选股 + 写笔记"，而不只是"列股票"。

---

## 2. 设计核心

### 理念
**Top 20 每日筛选继续跑（幕后）→ AI 周六做复盘决策 → UI 只展示 AI 观察池 + 笔记**

### 关键决策（用户确认）
1. **AI 维护稳定观察池**：固定 5 只（巴菲特集中持仓风格），每周复盘一次
2. **AI 周度写投资笔记**：类似巴菲特股东信，叙事性文本而非只是榜单
3. **不做"周度投票推荐"**：短期投票违背价值投资理念
4. **观察池取代 Top 20 在 UI 上的位置**：不留 Top 20 在前端，避免无序信息干扰判断
5. **混合决策机制**：硬规则做底线保护 + AI 自主做常规调整
6. **首次观察池成员**：AI 从最近 4 周 screening_result（含跌出 Top 20 的历史候选股）选 5 只作为初始成员
7. **复盘时机**：周六 00:00 触发（一过零点就跑），用户早上起来随时可看

---

## 3. 架构总览

### 数据流
```
[每日 15:30 流水线]（保留，不变）
    ↓
[screening_result 表]（继续写入，作为候选池数据源）
    ↓
[每周五 AI 分析]（保留，分析本周新进 Top 20）
    ↓
[ai_analysis 表]（继续写入，作为复盘输入）
    ↓
[每周六 00:00 AI 复盘]（新增）
    ├─ 输入：当前观察池 5 只 + 最近 4 周 screening_result
    │       + watchlist + 大盘 + 周五 ai_analysis
    ├─ 硬规则预过滤：Signal=AVOID / ROE<5% / 价格越出买入区上限 / ROE 同比下降 > 10pp → 强制调出
    ├─ AI 决策：在硬规则允许范围内选 5 只 + 写笔记
    └─ 输出 JSON：watchlist_actions + new_watchlist + journal
    ↓
[ai_watchlist 表]（新增：当前 5 只 + 历史调整记录）
[ai_journal 表]（新增：每周笔记 + 历史归档）
```

### 关键设计原则
1. **每日流水线保留**：数据采集 + 筛选继续跑，作为 AI 决策的数据源
2. **Top 20 在 UI 上消失**：不再展示每日变动的排行榜，避免干扰判断
3. **AI 复盘独立调度**：周六 00:00 跑（不与周五流水线/AI 分析冲突）
4. **硬规则层先跑**：先识别必须调出的股票，AI 只能在剩余位置做决策
5. **首次启动**：如果 `ai_watchlist` 表为空，AI 第一次复盘时从最近 4 周 screening_result 选 5 只作为初始观察池

---

## 4. 数据模型

### 4.1 `ai_watchlist`（当前观察池状态）

```sql
CREATE TABLE IF NOT EXISTS ai_watchlist (
    code            TEXT PRIMARY KEY,         -- 股票代码
    name            TEXT NOT NULL,
    added_at        TEXT NOT NULL,             -- 进入观察池时间
    added_reason    TEXT,                      -- AI 给的进入理由
    ai_confidence   TEXT,                      -- 高/中/低
    last_reviewed   TEXT,                       -- 最近一次复盘日期
    review_count    INTEGER DEFAULT 1           -- 连续在池周数
);
```

固定 5 行（由 AI 保证容量，不靠 DB 约束）。调出时 `DELETE`，调入时 `INSERT`。

### 4.2 `ai_watchlist_history`（历史调整记录）

```sql
CREATE TABLE IF NOT EXISTS ai_watchlist_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL,
    name            TEXT,
    action          TEXT NOT NULL,             -- add / remove / keep
    action_date     TEXT NOT NULL,
    reason          TEXT,                       -- AI 给的原因
    review_run_id   TEXT NOT NULL              -- 关联本次复盘 run_id
);
```

每次复盘追加一条记录（含 keep），保留完整决策历史。可追溯任意股票任意周的进出动作。

### 4.3 `ai_journal`（投资笔记）

```sql
CREATE TABLE IF NOT EXISTS ai_journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    journal_date    TEXT NOT NULL UNIQUE,       -- 复盘日期 YYYY-MM-DD
    run_id          TEXT NOT NULL,              -- 关联 run_log
    title           TEXT NOT NULL,              -- 笔记标题（AI 生成）
    content_md      TEXT NOT NULL,              -- 笔记正文 Markdown
    market_snapshot TEXT,                       -- 当时的市场状态摘要 JSON
    actions_summary TEXT,                       -- 本次调整动作摘要 JSON
    created_at      TEXT NOT NULL
);
```

---

## 5. 后端组件

### 5.1 `src/analyzer/watchlist_reviewer.py`（新文件）

核心复盘引擎，单一职责：**输入数据 → 输出复盘结果**。

```python
class WatchlistReviewer:
    """AI 观察池复盘引擎"""
    
    def __init__(self, config: dict):
        self.cfg = config
        self._lock = threading.Lock()  # 模块级锁，防并发
    
    def review(self, run_id: str) -> ReviewResult:
        """主入口"""
        # 1. 加载当前观察池
        current = AiWatchlistDAO().get_all()
        
        # 2. 加载候选池：最近 4 周 screening_result 去重
        candidates = self._load_recent_candidates(weeks=4)
        
        # 3. 加载周五 ai_analysis 结果
        analyses = self._load_recent_analyses()
        
        # 4. 加载大盘数据
        market = MarketIndexDAO().get_recent(days=7)
        
        # 5. 硬规则预过滤：强制调出
        forced_out = self._apply_hard_rules(current, analyses)
        
        # 6. 构建 LLM prompt
        prompt = self._build_prompt(
            current, candidates, analyses, market, forced_out
        )
        
        # 7. 调用 LLM（复用 ai_analyzer 的 FreeModelPool）
        result = self._call_llm(prompt)
        
        # 8. 校验 + 落库
        validated = self._validate_and_persist(
            result, run_id, forced_out
        )
        return validated
```

### 5.2 `src/models/ai_watchlist.py`（新文件）

DAO 层，独立文件避免 `database.py` 继续膨胀：

- `AiWatchlistDAO`：当前观察池 CRUD（get_all / add / remove / update_reviewed）
- `AiWatchlistHistoryDAO`：历史记录（append / list_by_code / list_by_date）
- `AiJournalDAO`：笔记 CRUD（get_latest / get_by_date / list_all / save）

### 5.3 硬规则层（4 条）

```python
HARD_RULES = [
    # 1. Signal 变 AVOID → 强制调出
    ("signal_avoid", lambda stock, analysis: 
        analysis and analysis.get('signal') == 'AVOID'),
    
    # 2. ROE 跌破 5% → 强制调出
    ("roe_below_5", lambda stock, analysis: 
        stock.get('roe') is not None and stock['roe'] < 5),
    
    # 3. 越出买入区上限 +20% → 强制调出（已涨过头）
    ("price_above_buyzone", lambda stock, analysis: 
        self._is_price_above_buyzone(stock, analysis)),
    
    # 4. 基本面恶化（ROE 同比下降 > 10pp） → 强制调出
    ("roe_collapse", lambda stock, analysis: 
        self._has_roe_collapse(stock)),
]
```

硬规则触发 → `forced_out` 列表 → AI 必须接受这些调出，只能在剩余位置做调入决策。

### 5.4 LLM Prompt 结构

```
[角色] 你是一名价值投资基金经理，每周复盘一次观察池。
[规则] 
  - 观察池固定 5 只
  - 硬规则已强制调出: <list>
  - 你可以从候选池选调入
  - 优先保持稳定，没有充分理由不要换
  - 调入决策要给出理由，调出决策也要给出理由
[当前观察池] 5 只股票 + 最近分析
[候选池] 最近 4 周 Top 20 去重后的 ~50 只 + 分析
[大盘] 最近 7 天指数
[输出 JSON] 严格按 schema 返回
```

输出 JSON schema：
```json
{
  "watchlist_actions": [
    {"code": "000792", "action": "keep", "reason": "..."},
    {"code": "600519", "action": "add", "reason": "..."},
    {"code": "002415", "action": "remove", "reason": "Signal 转 AVOID"}
  ],
  "new_watchlist": ["000792", "600519", "300750", "600276", "002415"],
  "journal": {
    "title": "本周复盘 - 市场震荡，观察池稳定",
    "content_md": "# 本周复盘\n## 市场观察\n..."
  }
}
```

### 5.5 scheduler.py 改动

新增 `_check_weekly_review` 方法，在 `_run` 主循环里调用：

```python
def _check_weekly_review(self):
    """检查是否需要触发周六 AI 复盘"""
    now = now_cn()
    if now.weekday() != 5:  # 周六
        return
    if self._last_review_date == now.date():
        return
    # 周六任意时刻进程还活着且当天没跑过就触发一次
    # 注意：_last_review_date 在 _run_review 成功后才设置，
    # 避免失败重试时被错误跳过
    threading.Thread(target=self._run_review, daemon=True).start()

def _run_review(self):
    """后台跑复盘，不阻塞主调度循环"""
    try:
        from src.analyzer.watchlist_reviewer import WatchlistReviewer
        from src.models.database import RunLogDAO
        run_id = now_cn().strftime("%Y%m%d_%H%M%S") + "_review"
        RunLogDAO().start_run(run_id)
        reviewer = WatchlistReviewer(self._config)
        result = reviewer.review(run_id)
        RunLogDAO().complete_run(
            run_id, 0, len(result.new_watchlist), 0, "review done"
        )
        # 只在成功后才标记当日已完成
        self._last_review_date = now_cn().date()
    except Exception as e:
        logger.warning(f"[复盘] 异常: {e}", exc_info=True)
```

**注意**：失败重试存在一个边界情况——如果复盘持续失败，每次主循环（30s）都会重新触发，可能浪费 LLM 调用。需要在实施时增加失败计数器或退避机制（writing-plans 阶段细化）。

---

## 6. 前端 UI 改造

### 6.1 设计语言（保持一致）
- 白色主题 + 深色模式
- 无 emoji
- 极简线框风格（hairline border、minimal wireframe）
- 字体、留白比例不动

### 6.2 新布局结构

```
┌─────────────────────────────────────────────────────┐
│ Stock Dashboard                  [Phase] [时间]    │  ← 顶部 banner（保留）
├─────────────────────────────────────────────────────┤
│ 上证 3174  深证 10253  创业板 2056  ...             │  ← 大盘指数条（保留）
├─────────────────────────────────────────────────────┤
│ [AI 观察池]  [钉选股票]                             │  ← 新 tab 结构
├─────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────┐ │
│ │ AI 观察池                          最近复盘:    │ │
│ │                                   2026-07-20    │ │  ← 主视图：5 只股票卡片
│ │ ┌──────┬──────┬──────┬──────┬──────┐           │ │
│ │ │600519│000792│002415│600276│300750│           │ │
│ │ │贵州茅台│盐湖股份│海康威视│恒瑞医药│宁德时代│           │ │
│ │ │BUY   │HOLD  │BUY   │HOLD  │AVOID │           │ │
│ │ │高置信 │中置信 │高置信 │中置信 │低置信 │           │ │
│ │ │¥1640 │¥18.5 │¥32.8 │¥45.2 │¥215  │           │ │
│ │ │持仓3周│持仓8周│持仓1周│持仓2周│警示中│           │ │
│ │ └──────┴──────┴──────┴──────┴──────┘           │ │
│ │                                                 │ │
│ │ 本周笔记: "..." [查看全文]                      │ │  ← 笔记摘要 + 链接
│ └─────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

### 6.3 卡片设计细节

每只观察池股票一个卡片（取代之前的表格行）：
- **顶部**：股票名 + 代码（小字）
- **中部**：大字价格 + 涨跌幅（绿涨红跌，跟现有保持一致）
- **Signal 标签**：
  - BUY：深色背景白字
  - HOLD：灰色描边
  - AVOID：虚线红色描边警示
- **底部**：
  - 置信度（小字 + 色条 indicator）
  - 在池周数（"持仓 3 周"或"新进"）
  - 进入理由（hover 显示完整，或点开详情）

### 6.4 投资笔记页

**两种呈现方式并存**：

1. **观察池下方摘要**：本周笔记开头 1-2 段 + "查看全文"链接
2. **独立笔记页**：`/journal` 路由
   - 默认显示最新一篇
   - 右侧 sidebar 列出历史归档（按日期）
   - 全文 Markdown 渲染（用 `python-markdown` 库，已轻量）
   - 顶部有"本周调整动作"摘要块（调入 / 调出 / 保持）

### 6.5 优化提升点（不动设计语言但解决痛点）

| # | 痛点 | 优化方案 |
|---|------|---------|
| 1 | 现有 Top 20 表格信息密度高但难以快速扫读 | 改为卡片布局，5 只一眼看完，无需滚动 |
| 2 | Signal 颜色编码不明确（之前是文字） | BUY=深色填充 / HOLD=灰色描边 / AVOID=虚线红色描边 |
| 3 | 实时价格更新时整页闪烁（已部分修复但还有） | 卡片局部更新，用 `morphdom` 或纯 innerHTML 替换卡片内容 |
| 4 | 钉选股票跟候选股混在一起展示 | 独立 tab，跟 AI 观察池分离互不干扰 |
| 5 | 复盘状态不可见 | 顶部 banner 加"AI 复盘中..."状态条（复用现有 pipeline_progress 机制） |
| 6 | 历史决策不可追溯 | 笔记页右侧 sidebar 展示历史归档，点击切换 |

### 6.6 路由设计

```
GET /                       主页（AI 观察池 + 钉选 tab）
GET /journal                最新一篇笔记
GET /journal/<date>         指定日期笔记
GET /api/ai-watchlist       当前观察池 5 只 + 状态
GET /api/ai-watchlist/history  调整历史记录
GET /api/journal/latest     最新笔记 JSON
GET /api/journal/<date>     指定日期笔记 JSON
GET /api/journal/list       笔记列表（轻量，仅 date+title）
```

### 6.7 前端文件改动

- `src/web/templates/index.html`：调整布局，移除 Top 20 表格，加 tab 结构
- `src/web/templates/journal.html`（新）：笔记页
- `src/web/templates/_watchlist_card.html`（新）：观察池卡片 partial
- `src/web/static/app.js`（或扩展 `watchlist.js`）：tab 切换、卡片渲染、笔记页交互
- `src/web/static/styles.css`：新增卡片样式（保持极简线框语言）
- `src/web/routes.py`：新增上述路由

### 6.8 不动的部分
- 配色方案、字体、留白比例
- 大盘指数条
- 配置监听机制
- 实时行情轮询机制（`_realtime_cache`）

---

## 7. 错误处理 + 回退

| 场景 | 处理 |
|---|---|
| LLM 调用失败 / JSON 解析失败 | 重试 3 次（复用 ai_analyzer 的 FreeModelPool 自动故障轮换）；仍失败 → 跳过本次复盘，写 run_log 状态 `review_failed`，不影响现有观察池状态 |
| 硬规则强制调出 N 只，但 LLM 只补了 N-K 只 | 容忍空仓：观察池少于 5 只但不少于 N-K 只（不强制补满）；下周复盘再补 |
| LLM 调入未在候选池的股票 | 拒绝调入，记 watchlist_history 的 reason="rejected: not in candidate pool" |
| LLM 给的代码格式错误 / 不存在 | 校验 + 拒绝，写日志 |
| 候选池为空（最近 4 周无 screening_result） | 跳过复盘，记日志 "no candidates, skip" |
| 数据库写入失败 | 事务回滚，保持观察池原状态 |

---

## 8. 并发保护

- `WatchlistReviewer` 内置模块级 `threading.Lock()`：同一时刻只有一个复盘在跑
- 跟每日流水线锁 `_pipeline_lock` 独立，互不阻塞（复盘只读 screening_result，不写）
- 复盘异步线程 `daemon=True`，进程退出时自然结束

---

## 9. 数据库迁移

新表通过 `init_database()` 在启动时自动创建（已有模式：`src/models/database.py` 用 `CREATE TABLE IF NOT EXISTS`）。无需独立迁移脚本，重启即生效。

---

## 10. 边界情况

- **首次启动**（`ai_watchlist` 表为空）：第一次周六复盘时，prompt 改为"初始化观察池"，让 AI 从候选池选 5 只作为初始成员，全部记为 `action=add`
- **观察池已有 5 只 + 无强制调出**：AI 可以选择保持不变（全部 `action=keep`）或主动换股，但必须给出理由
- **强制调出后候选池不足**：允许观察池少于 5 只，等下周补
- **手动钉选**：现有 `watchlist` 表独立于 `ai_watchlist`，用户钉选跟 AI 观察池互不影响

---

## 11. 测试策略

### 11.1 `tests/analyzer/test_watchlist_reviewer.py`（新）

1. **硬规则单测**：4 条规则各自触发条件 + 不触发情况
2. **Prompt 构建测试**：mock 数据验证 prompt 包含必需字段
3. **LLM 响应解析测试**：mock JSON 响应验证解析 + 校验逻辑
4. **决策落库测试**：mock 完整复盘流程验证数据库状态正确
5. **边界测试**：空观察池、强制调出后候选不足、JSON 解析失败

### 11.2 `tests/web/test_routes_journal.py`（新）

1. `/journal` 路由返回最新笔记
2. `/api/journal/list` 返回轻量列表
3. 指定日期笔记 404 处理

---

## 12. 配置项

`config/config.yaml` 新增：

```yaml
ai_review:
  enabled: true                    # 是否启用 AI 观察池复盘
  review_day: 5                    # 周几复盘（5=周六）
  candidate_pool_weeks: 4          # 候选池回看周数
  watchlist_size: 5               # 观察池容量
  hard_rules:
    signal_avoid: true             # Signal=AVOID 强制调出
    roe_below: 5                   # ROE 低于此值强制调出
    price_above_buyzone_pct: 20    # 越出买入区上限百分比
    roe_collapse_pp: 10            # ROE 同比下降 pp
```

---

## 13. 实施顺序建议

1. DAO 层 + 表 schema（最底层，无依赖）
2. `WatchlistReviewer` 核心逻辑 + 硬规则
3. Prompt 模板 + LLM 调用
4. scheduler 集成（周六触发）
5. 后端路由 `/api/ai-watchlist` + `/api/journal/*`
6. 前端卡片 + tab + 笔记页
7. 测试

每一步完成后 commit，最后整体联调。

---

## 14. 范围外（YAGNI）

以下功能**不在本次设计范围**：
- AI 自动下单交易
- 实时调仓（日内换股）
- 多策略组合（不同风格的观察池并行）
- 笔记导出 PDF / 邮件订阅
- 用户可配置硬规则（先固定 4 条）
- 观察池股票详情页跟现有股票详情页合并（先复用现有详情页）

这些可在未来迭代中考虑，本次保持范围聚焦。
