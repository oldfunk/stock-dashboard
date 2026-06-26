# 📊 Stock Dashboard — A 股价值投资实时选股看板

基于价值投资理念的 A 股实时监控 + 量化选股 + AI 分析 Web 系统。
运行在 Debian VM (192.168.50.56)，systemd 服务管理。

## 系统架构

```
                     ┌──────────────────────────────────┐
                     │  systemd: stock-dashboard.service  │
                     │  (开机自启 · 崩溃自愈)              │
                     │                                    │
 腾讯API ──────────→ │  Uvicorn Web 服务 (FastAPI)        │
  qt.gtimg.cn        │    ├─ 大盘指数轮询 (30分钟)         │
                     │    ├─ 选股池实时价格 (5分钟/交易时段) │
                     │    ├─ 每日选股流水线 (15:30自动触发) │
                     │    └─ AI 分析 (OpenCode Zen)        │
                     │                                    │
                     └─────────┬──────────────────────────┘
                               │
                     ┌────────▼──────────┐
                     │  SQLite 数据库      │
                     │  data/db/          │
                     │  ├─ stock_snapshot  │  5527 只股票基本行情
                     │  ├─ screening_result│  20 只精选股票
                     │  ├─ market_index    │  4 条大盘指数
                     │  ├─ run_log         │  运行记录
                     │  └─ ai_analysis_log │  AI 分析日志
                     └───────────────────┘
```

## 核心能力

### 1. 实时行情轮询（调度器内置）
| 项目 | 数据源 | 频率 | 存储 |
|:---|:---|---:|:---|
| 大盘指数 | 腾讯 qt.gtimg.cn | 30 分钟 | market_index 表 |
| 选股池价格 | 腾讯 qt.gtimg.cn | 5 分钟（仅 9:30-15:00） | 内存缓存 + /api/realtime |
| 全市场快照 | 腾讯批量接口 | 每日收盘后 | stock_snapshot 表 |

### 2. 每日选股流水线 (15:30)
```
腾讯报价 → 全量行情采集 (5527只)
    ↓
初筛过滤 (PE 3~20 / PB <3.5 / 排除ST / 市值 30~50000亿)
    ↓
AKShare 财务数据补充 (ROE / 负债率 / 增长率)
    ↓
价值投资评分 (PE/PB/ROE/增长/负债 五项加权)
    ↓
TOP 20 精选股票 → 存入 screening_result
    ↓
AI 分析 (DeepSeek V4 Flash Free via OpenCode Zen)
    → 逐股生成：选股解析 / 投资策略 / 买卖策略
```

### 3. Web 看板
- 地址：`http://192.168.50.56:9527`
- 大盘指数（上证/深证/创业板/科创50）实时显示
- 20 只精选股票卡片，含 PE/PB/ROE/市值/实时价格
- 实时价格涨跌 ▲▼ 红绿指示
- 30 秒自动刷新（API 驱动）
- 浅色/深色主题切换

### 4. AI 分析
- 模型：`deepseek-v4-flash-free` (OpenCode Zen)
- 对每只精选股票生成三部分：
  - 选股解析（竞争优势/财务健康/风险）
  - 投资策略（仓位/持有周期）
  - 买卖策略（买入区间/目标价/止损/止盈）
- **限流处理**：Free tier 限流严格（≈1次/分钟）。代码含智能冷却：
  - 股票间间隔 30 秒
  - 遇到 429 后冷却 120 秒，期间跳过剩余股票
  - 单股票最多重试 4 次（30s→60s→120s 退避）
  - 20 只股票约 10-30 分钟完成
- JSON 解析兼容 markdown/前缀/后缀/末尾逗号

## 状态检查

```bash
# 服务状态
sudo systemctl status stock-dashboard
sudo journalctl -u stock-dashboard -n 30 --no-pager    # 最近日志
sudo journalctl -u stock-dashboard -f                   # 实时日志

# Cron 日志
tail -f ~/stock-dashboard/data/logs/cron.log

# API 端点
curl -s http://127.0.0.1:9527/api/status      # 运行状态
curl -s http://127.0.0.1:9527/api/indices     # 大盘指数
curl -s http://127.0.0.1:9527/api/stocks      # 选股结果+实时行情
curl -s http://127.0.0.1:9527/api/realtime    # 纯实时行情
```

## 配置文件

`config/config.yaml` 关键配置项：

```yaml
screener.conditions:
  max_pe: 20, min_pe: 3       # 市盈率范围
  max_pb: 3.5                 # 市净率上限
  min_roe: 10                 # ROE 下限
  max_debt_ratio: 65          # 负债率上限
  max_candidates: 20          # 候选股数量

ai:
  api_base: "https://opencode.ai/zen/v1"
  model: "deepseek-v4-flash-free"
```

API Key 存在 `.env` 中（被 .gitignore 排除）：
```
STOCK_AI_API_KEY=sk-xxxx...
```

## 数据文件

```
~/stock-dashboard/
├── config/config.yaml          # 主配置
├── data/db/stock_dashboard.db  # SQLite 数据库
├── data/cache/stock_codes.json # 股票代码缓存
├── data/logs/cron.log          # Cron 运行日志
├── src/
│   ├── collector/akshare_fetcher.py   327行  采集层
│   ├── screener/value_screener.py     409行  筛选引擎
│   ├── analyzer/ai_analyzer.py        224行  AI 分析
│   ├── models/database.py             308行  数据层
│   ├── web/routes.py                  244行  路由
│   ├── scheduler.py                   306行  调度器
│   ├── orchestrator.py                117行  流水线编排
│   └── main.py                        35行  入口
├── scripts/daily_update.sh      # Cron 脚本
└── .env                          # API Key (不提交)
```

## 数据库结构

| 表 | 行数 | 说明 |
|:---|---:|:---|
| stock_snapshot | 5527 | 全 A 股基本行情（代码/名称/PE/PB/市值/现价） |
| screening_result | 20 | 最新精选股票（评分/PE/PB/ROE/负债率/AI分析） |
| market_index | ~N | 大盘指数历史（上证/深证/创业板/科创50） |
| run_log | 1 | 最近一次流水线运行记录 |
| ai_analysis_log | 0 | AI 分析消耗记录 |

## 定时任务

| 触发方式 | 时间 | 任务 |
|:---|---:|:---|
| systemd 服务 | 开机自启 | Web 服务 + 调度器 |
| 调度器 _check_daily_pipeline() | 工作日 15:30 | 每日选股流水线 |
| 系统 crontab | 工作日 15:30 | `python -m src.orchestrator`（备份触发） |
| 调度器 _poll_indices | 每 30 分钟 | 大盘指数更新 |
| 调度器 _poll_stocks | 每 5 分钟 | 选股池实时价格（仅交易时段） |

两路 15:30 触发互不冲突（`_last_daily_date` 去重）。

## 历史开发关键决策

1. **数据源选择**: 腾讯 qt.gtimg.cn 为主要行情源（curl 绕过 TUN 代理阻断），AKShare 用于财务数据补充
2. **TUN 代理兼容**: 新浪 hq.sinajs.cn 被阻断 → 改为腾讯 qt.gtimg.cn 统一接口
3. **部署方式**: Flask 开发 → systemd 服务（开机自启 + 崩溃自愈）
4. **筛选指标**: PE<20/PB<3.5/ROE≥10%/负债率<65%/增长≥5%，五项加权评分
5. **UI 风格**: 纯白浅色背景 + Inter 字体 + 深色模式切换 + 细线分割 + 无 emoji/渐变色
6. **AI Provider**: OpenAI → GLM-4.7-Flash → OpenCode Zen DeepSeek V4 Flash Free

## 2.0 规划

- [ ] 全量财务数据补进 stock_snapshot 表（支持按 ROE/负债率排序）
- [ ] 个股详情页（评分历史 + 实时走势）
- [ ] SSE 替代 30 秒前端轮询
- [ ] 价格预警推送（Telegram/Discord）
- [ ] 模拟投资组合
- [ ] 板块聚合分析
- [ ] Pi 3B 轻量副节点部署

## 免责声明

本工具仅供学习研究和参考，不构成任何投资建议。投资有风险，入市需谨慎。
