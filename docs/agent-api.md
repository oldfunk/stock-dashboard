# 外部 Agent 接入指南（Agent API）

> 任何外部 agent（Hermes、OpenCode、Claude、自写脚本）通过 HTTP 连入本面板：**读数据、写分析、写笔记**。
> 面板本身不触发 AI 分析——分析由外部 agent 负责，面板只存储与展示。
> 相关协议：`docs/ai-proxy-ai-analysis.md`（分析结果的展示契约）。

## 连接

| 项 | 值 |
|---|---|
| 基址 | `http://<生产服务器>:9527`（生产环境，局域网；实际地址由部署方线下提供）；本机开发 `http://127.0.0.1:9527` |
| 协议 | 纯 HTTP + JSON，UTF-8，无鉴权、无 CORS 限制 |
| 安全边界 | **仅限局域网使用**。服务监听 `0.0.0.0:9527`，无 token；不要暴露公网 |
| 错误格式 | 非 2xx 返回 `{"detail": "原因"}`；`400` 参数非法、`404` 对象不存在 |
| 股票代码 | 一律 6 位字符串，保留前导零（`"000792"`，不要转 int） |
| 日期 | `YYYY-MM-DD`；时间戳带时区（东八区） |

## 读数据

| 端点 | 说明 |
|---|---|
| `GET /api/stocks` | 最新筛选结果 + 实时行情（当日候选池全量） |
| `GET /api/realtime` | 纯实时行情（仅价格变化，最轻量） |
| `GET /api/search?q=茅台` | 全 A 股搜索（code 或名称模糊匹配） |
| `GET /api/watchlist` | 钉选列表（最新行情 + 最近 AI 信号） |
| `GET /api/watchlist/{code}/full` | **钉选股一站式**：基本信息、实时行情、财务指标、AI 分析 JSON、分析历史 5 条、投资笔记 10 条、投资论文 |
| `GET /api/ai-watchlist` | AI 观察池（最多 5 只） |
| `GET /api/ai-watchlist/history` | 观察池进出调整历史 |
| `GET /api/watchlist/notes?limit=50` | 全部钉选股笔记批量拉取 |
| `GET /api/watchlist/{code}/thesis` | 单股投资论文（论点+假设+红线+卖出条件） |
| `GET /api/watchlist/{code}/monitor` | 单股监控条件 |
| `GET /api/history/{code}` | 单股历史分析记录 |
| `GET /api/journal/latest`、`/api/journal/{date}` | 投资笔记（最新/指定日；缺失 404） |
| `GET /api/journal/list` | 笔记列表（轻量，仅 date + title；**空时返回 `[]`（200），不 404**） |
| `GET /api/journal/{date}/conflicts` | 笔记矛盾信号检测 |
| `GET /api/stock/{code}/kline?period=daily&limit=250` | K 线（可选 `period`） |
| `GET /api/index/{index_code}/kline` | 指数 K 线（实时拉取不缓存） |
| `GET /api/indices`、`GET /api/status`、`GET /api/progress`、`GET /api/data-quality` | 大盘 / 运行状态 / 流水线进度 / 数据质量概览 |
| `GET /api/config` | 当前生效配置（API Key 等敏感字段已脱敏） |

HTML 页面（`/`、`/stock/{code}`、`/watchlist/{code}`、`/journal`）是给人看的，agent 不要用。

## 写分析与笔记

### 1. 投资笔记

```
POST /api/watchlist/{code}/notes
{"note": "笔记正文", "note_type": "analysis", "model": "你的模型标识"}
```

- `note_type`：`weekly`（周复盘）/ `analysis`（分析）/ `user`（人工），默认 `weekly`
- `model`：溯源标识，外部 agent 必填（如 `hermes-gpt-5`、`opencode-claude`）
- 追加写入，每条独立成行；成功返回 `{"ok": true, ...}`

### 2. 投资论文（论点级结构化分析）

```
POST /api/watchlist/{code}/thesis
{
  "core_thesis": "以1400元买入茅台，因为高端白酒定价权+渠道掌控，自由现金流持续大于净利润",
  "assumptions": [
    {"content": "毛利率维持91%以上", "verify_method": "季报毛利率", "verify_freq": "季度", "status": "未验证"}
  ],
  "red_lines": [
    {"condition": "单季净利同比负增长", "action": "重新评估，跌破止损线则清仓"}
  ],
  "sell_conditions": ["估值PE>45倍且增速<10%", "发现财务造假证据"],
  "source": "ai_analysis"
}
```

- **upsert 语义**：每股一行，重复 POST 即覆盖更新，不会产生重复
- `core_thesis` 超 500 字截断；`source`：`ai_analysis` / `manual`（默认 `manual`）
- `assumptions[].status`：`未验证` / `成立` / `证伪`（本地复盘会更新此状态）

### 3. 钉选与监控

```
POST   /api/watchlist/{code}          加入钉选
DELETE /api/watchlist/{code}          取消钉选
POST   /api/watchlist/{code}/monitor  {"monitor_condition": "{\"pe_max\": 30}"}   # null 清空
```

### 4. 触发数据更新（谨慎）

```
POST /api/trigger_update
```

触发全量每日更新（后台线程，内部有锁防并发）。手动调用即可，**不要高频重复调**。

## 典型工作流

```bash
BASE=http://<生产服务器>:9527

# 1. 找到要分析的股票
curl -s $BASE/api/watchlist

# 2. 一次性取全量数据
curl -s $BASE/api/watchlist/000792/full

# 3. 本地完成分析后，写回论文 + 笔记
curl -s -X POST $BASE/api/watchlist/000792/thesis -H 'Content-Type: application/json' -d '{
  "core_thesis": "...", "assumptions": [...], "red_lines": [...],
  "sell_conditions": [...], "source": "ai_analysis"}'

curl -s -X POST $BASE/api/watchlist/000792/notes -H 'Content-Type: application/json' -d '{
  "note": "本周假设核验：毛利率91.3%，成立", "note_type": "analysis", "model": "hermes-xxx"}'
```

## 约定与红线

1. **只读不算完成**：分析结论必须经 `thesis` / `notes` 写回，面板负责跨期对照；不要只在本地生成 `.md`
2. **溯源必填**：`notes.model`、`thesis.source` 是跨期追踪的依据，不要省略
3. **不改动流水线**：`trigger_update` 之外的系统级操作（重启服务、改配置）不在本 API 范围
4. **数据新鲜度**：工作日 15:30 跑全量流水线；实时行情接口即拉即取。非交易时段数据为上次收盘
5. **失败如实标**：分析失败时写明原因（`notes` 里说明），不要编造未执行的结论

## 模拟交易面板（paper-trading 子项目）

- 本面板 `/paper` 直达子项目原仪表盘（`:8081`，与本面板同机不同端口）
- 外部 AI 也可直调它的读写面： universe/信号/下单/持仓/NAV（CLI `python -m paper_trading.hermes_bridge --help`，运行 CWD 须为 `paper_trading/`）；母库只读（screening/watchlist/快照/AI 历史），写操作只落它自己的账本库
- 密钥与 Key：它用独立 `secrets.local.json`（0600），与本面板 `.env` 互不干扰
