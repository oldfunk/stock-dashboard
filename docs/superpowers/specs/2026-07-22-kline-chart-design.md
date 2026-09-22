# 单股详情页 K 线图 + 技术指标设计

> 在单股详情页第 10 章节（技术分析扩展位）填入 K 线图 + 全套技术指标，参考东方财富/同花顺的专业行情面板。
>
> 一次看清这只股票的价格走势：日/周/月 K 线蜡烛图 + 成交量 + MA/MACD/KDJ/RSI 技术指标。

## 背景与动机

单股详情页已有 AI 笔记 + 财务历史 + 时间线，但缺少最核心的"行情数据"——K 线图。东方财富/同花顺的详情页都以 K 线图为核心，用户参考这两者设计。

第 10 章节已预留空 `<section id="technical-analysis">` 占位，本次填入实现。

## 目标

1. **专业 K 线图** — 日/周/月 K 蜡烛图 + 成交量柱，支持缩放、十字线拖拽
2. **全套技术指标** — MA（5/10/20/60）叠加 + MACD/KDJ/RSI 独立面板
3. **数据缓存** — 定时拉取缓存到 SQLite，毫秒级 API 响应
4. **零打包** — klinecharts 通过 CDN 引入，无需前端构建工具
5. **性能可扩展** — 生产服务器不够就升级硬件，不在代码层硬卷优化

## 非目标

- 不做分时图（min 级别数据），本次只做日/周/月 K
- 不做实时推送（WebSocket），数据来自定时缓存
- 不做自定义指标公式编辑器
- 不做画线工具（趋势线/支撑阻力线）
- 不做与 AI 分析的联动（如 K 线上标注买卖点）

## 技术选型

### 前端：klinecharts

- **版本**：klinecharts v9（`https://cdn.jsdelivr.net/npm/klinecharts@9/dist/umd/index.min.js`）
- **体积**：~95KB（gzip 后 ~30KB）
- **理由**：
  - 轻量，专为 K 线图设计
  - 自带 MA/MACD/KDJ/RSI/布林带等技术指标
  - 原生支持缩放/十字线/指标叠加
  - CDN 引入，无需打包构建
- **引入方式**：stock_detail.html `<head>` 加 `<script src="...klinecharts@9...">`

### 数据：定时缓存到 SQLite

- 新建 `kline_daily` 表存储日K
- 周K/月K 从日K 本地聚合（后端 API 层处理），不单独存储
- scheduler 每日 15:30（收盘后）拉取
- 拉取范围：观察池 5 只 + 候选股 25 只 = 30 只
- 每只最近 1 年日K（~250 条），共约 7500 条

## 数据模型

### 新表：kline_daily

```sql
CREATE TABLE IF NOT EXISTS kline_daily (
    code TEXT NOT NULL,
    trade_date TEXT NOT NULL,   -- YYYY-MM-DD
    open REAL,
    close REAL,
    high REAL,
    low REAL,
    volume REAL,                -- 成交量（手）
    amount REAL,                -- 成交额（元）
    turnover REAL,              -- 换手率（%）
    PRIMARY KEY (code, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_kline_code_date ON kline_daily(code, trade_date);
```

### KlineDAO

```python
class KlineDAO:
    def upsert_many(self, code: str, records: list[dict]) -> int:
        """批量写入日K（INSERT OR REPLACE）"""

    def get_daily(self, code: str, limit: int = 250) -> list[dict]:
        """按日期升序返回日K"""

    def get_latest_date(self, code: str) -> str | None:
        """获取该股票最新已缓存日期（用于增量更新）"""

    def get_tracked_codes(self) -> list[str]:
        """获取需要拉取K线的股票代码列表（观察池+候选股）"""
```

### 采集方法（akshare_fetcher.py 新增）

```python
def fetch_kline_data(code: str, start_date: str | None = None, adjust: str = "qfq") -> list[dict]:
    """
    拉取单只股票的日K数据。

    参数：
    - code: 6位股票代码
    - start_date: 增量更新的起始日期（YYYY-MM-DD），None 则拉最近1年
    - adjust: 复权类型，qfq=前复权（默认）

    返回：[{"trade_date": "2025-07-21", "open":..., "close":..., ...}, ...]

    接口：ak.stock_zh_a_hist(symbol=em_code, period="daily",
                             start_date=YYYYMMDD, end_date=YYYYMMDD, adjust="qfq")
    注意：akshare 的 start_date/end_date 参数接受 "YYYYMMDD" 格式（无连字符），
          函数内部需将入参的 "YYYY-MM-DD" 转换为 "YYYYMMDD" 后再传给 akshare。
    """
```

### scheduler 集成

在现有 scheduler 的每日任务中追加 K 线拉取步骤：

```python
# scheduler.py 每日 15:30 任务追加
codes = KlineDAO().get_tracked_codes()
for code in codes:
    latest = KlineDAO().get_latest_date(code)
    start = latest or (today - 1年)
    records = fetch_kline_data(code, start_date=start)
    KlineDAO().upsert_many(code, records)
```

## API 设计

### GET /api/stock/{code}/kline

**参数：**
- `period`: `daily`（默认）/ `weekly` / `monthly`
- `limit`: 返回条数，默认 250

**响应（klinecharts 所需格式）：**

```json
{
  "code": "000792",
  "period": "daily",
  "klines": [
    {
      "timestamp": 1625097600000,
      "open": 18.0,
      "close": 18.5,
      "high": 18.8,
      "low": 17.9,
      "volume": 12345600,
      "turnover": 1.2
    }
  ]
}
```

- `timestamp`: 毫秒级 Unix 时间戳（klinecharts 要求）
- `volume`: 股数（非手数，klinecharts 显示为柱状图）
- `turnover`: 换手率百分比

**周K/月K 聚合逻辑**（API 层，非 DB 层）：

```python
def aggregate_weekly(daily_records: list[dict]) -> list[dict]:
    """将日K聚合为周K：周一开盘、周五收盘、周内最高/最低、周成交量求和"""
```

**无数据响应：**

```json
{"code": "000792", "period": "daily", "klines": []}
```

前端收到空 `klines` 时显示"暂无K线数据"占位。

## 前端实现

### 模板结构（stock_detail.html 第 10 章节替换）

```html
<!-- 10. 行情图表 -->
<section class="sd-section" id="technical-analysis">
    <h2 class="sd-section-title">行情图表</h2>
    <div class="sd-kline-toolbar">
        <button class="kline-period-btn active" data-period="daily">日K</button>
        <button class="kline-period-btn" data-period="weekly">周K</button>
        <button class="kline-period-btn" data-period="monthly">月K</button>
    </div>
    <div id="kline-chart" style="height: 500px;"></div>
    <div class="sd-kline-empty" style="display:none;">暂无K线数据</div>
</section>
```

### klinecharts 初始化 JS（页面底部 `<script>`）

```javascript
const chart = klinecharts.init('kline-chart');

// 默认指标：MA5/MA10/MA20 叠加在主图
chart.createIndicator('MA', false, { id: 'candle_pane' });

// 副图指标：成交量（默认显示）
chart.createIndicator('VOL');

// 默认加载日K
loadKline('daily');

// 日/周/月切换
document.querySelectorAll('.kline-period-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.kline-period-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        loadKline(btn.dataset.period);
    });
});

async function loadKline(period) {
    const resp = await fetch(`/api/stock/${code}/kline?period=${period}`);
    const data = await resp.json();
    if (!data.klines || data.klines.length === 0) {
        document.getElementById('kline-chart').style.display = 'none';
        document.querySelector('.sd-kline-empty').style.display = 'block';
        return;
    }
    chart.applyNewData(data.klines);
}
```

### CSS（复用现有 design tokens）

```css
.sd-kline-toolbar { display: flex; gap: 4px; margin-bottom: 12px; }
.kline-period-btn {
    padding: 4px 12px; border: 1px solid var(--divider); background: transparent;
    color: var(--text-secondary); border-radius: var(--radius-sm); cursor: pointer;
    font-size: 13px;
}
.kline-period-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
.sd-kline-empty { padding: 48px; text-align: center; color: var(--text-tertiary); }
#kline-chart { background: var(--bg-secondary); border-radius: var(--radius); }
```

## 技术指标配置

klinecharts v9 内置指标，通过 `chart.createIndicator()` 启用：

| 指标 | 位置 | 默认显示 |
|---|---|---|
| MA（5/10/20/60） | 主图叠加 | ✓ 默认显示 |
| VOL（成交量） | 副图 | ✓ 默认显示 |
| MACD | 副图 | 用户可从指标列表添加 |
| KDJ | 副图 | 用户可从指标列表添加 |
| RSI | 副图 | 用户可从指标列表添加 |
| BOLL（布林带） | 主图叠加 | 用户可从指标列表添加 |

用户可通过 klinecharts 自带的指标选择 UI 添加/删除副图指标，无需我们额外开发。

## 错误处理

- **akshare 拉取失败**：scheduler 记录日志，跳过该股票，下次重试
- **API 返回空数据**：前端显示"暂无K线数据"占位，不报错
- **klinecharts CDN 加载失败**：`<script onerror>` 显示提示文字
- **代码不在 tracked_codes**：API 仍可查询（返回缓存数据或空），不限制

## 测试策略

### 单元测试

1. `KlineDAO.upsert_many` + `get_daily` 往返测试
2. `KlineDAO.get_latest_date` 增量更新判断
3. `aggregate_weekly` / `aggregate_monthly` 聚合逻辑
4. `fetch_kline_data` mock akshare 返回（异常格式容错）

### API 测试

1. `GET /api/stock/{code}/kline?period=daily` 200 + 正确格式
2. `period=weekly` 聚合正确
3. 无数据时返回空 `klines` 数组
4. 无效 code 格式返回 404

### 前端验证（浏览器）

1. K 线图正常渲染（蜡烛 + 成交量）
2. 日/周/月切换生效
3. 缩放/十字线交互正常
4. MA 默认叠加显示
5. 无数据股票显示占位文字

## 实施范围

### 新增

- `kline_daily` 表 + 初始化（database.py）
- `KlineDAO` 类（database.py）
- `fetch_kline_data()` 方法（akshare_fetcher.py）
- `GET /api/stock/{code}/kline` 路由（routes.py）
- scheduler 每日 K 线拉取任务（scheduler.py）

### 修改

- `stock_detail.html` 第 10 章节占位替换为 K 线图组件

### 不做

- 不做分时图 / 实时推送 / 画线工具 / 买卖点标注
- 不做自定义指标公式编辑器
- 不改其他章节

## 性能考虑（生产服务器环境）

- 30 只股票 × 250 条 = 7500 条，单次全量拉取预计 1-2 分钟
- 增量更新（仅拉缺失日期）预计 < 10 秒
- API 读 SQLite 单只 250 条 < 10ms
- klinecharts 前端渲染 250 条 K 线无压力
- 若生产服务器性能不足，用户已同意升级硬件而非硬卷优化
