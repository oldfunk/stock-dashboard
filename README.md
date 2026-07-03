# 📊 Stock Dashboard — A 股价值投资实时选股看板

基于价值投资理念的 A 股实时监控 + 量化选股 + AI 分析 Web 系统。
运行在 Debian VM (192.168.50.56)，后台进程管理。

## 系统架构

```
                     ┌──────────────────────────────────┐
                     │  Hermes 管理的后台进程             │
                     │  (nohup / cron 保活)              │
                     │                                  │
 腾讯API ──────────→ │  Uvicorn Web 服务 (FastAPI)        │
  qt.gtimg.cn        │    ├─ 大盘指数轮询 (30分钟)         │
                     │    ├─ 选股池实时价格 (5分钟/交易时段) │
                     │    ├─ 每日选股流水线 (15:30自动触发) │
                     │    └─ AI 分析 (GLM 免费模型)       │
                     │                                  │
                     └─────────┬────────────────────────┘
                               │
                     ┌────────▼──────────┐
                     │  SQLite 数据库      │
                     │  data/stock_dashboard.db │
                     │  ├─ stock_snapshot    │ 全 A 股行情
                     │  ├─ screening_result  │ 精选 20 只
                     │  ├─ market_index      │ 大盘指数
                     │  ├─ run_log           │ 运行记录
                     │  └─ stock_analysis_history │ AI 分析累积
                     └─────────────────────┘
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
AI 分析 (GLM-4.7-Flash 免费模型)
    → 逐股生成：选股解析 / 投资策略 / 买卖策略
```

### 3. Web 看板
- 地址：`http://192.168.50.56:9527`
- 大盘指数（上证/深证/创业板/科创50）实时显示
- 20 只精选股票卡片，含 PE/PB/ROE/市值/实时价格
- 实时价格涨跌 ▲▼ 红绿指示
- 60 秒自动刷新（API 驱动）
- 浅色/深色主题切换 + 红涨绿跌/绿涨红跌风格切换
- **右侧知识面板**: PE/PB/ROE/负债率/增长率/评分体系 科普注解
- **历史分析追溯**: 点击展开历史分析完整详情（分析/策略/交易合并展示）

### 4. AI 分析
- 模型：`glm-4.7-flash` (智谱 AI — 免费，兼容 OpenAI API)
- 对每只精选股票生成：选股解析 + 投资策略 + 买卖策略
- **极致重试策略**：20次指数退避 (30s→60s→120s→240s→300s×16)
- **离线时段**：凌晨 2-4 点（非交易时段）批量分析
- **失败记录**：24 小时内自动跳过失败股票，次日重试
- JSON 解析兼容 markdown/前缀/后缀/末尾逗号等多种格式

### 5. 基础知识科普
- 内置价值投资指标指南（PE、PB、ROE、负债率、增长率、评分体系）
- 每个指标附带意义、A股参考区间、筛选逻辑说明

## 启动方式

```bash
# 启动 Web 服务（默认）
python src/main.py serve

# 运行选股流程
python src/main.py run

# 全量 AI 分析（离线时段使用）
python scripts/run_ai_analysis.py --all

# 运行完整流水线
python scripts/run_pipeline.py
```

## 状态检查

```bash
# API 端点
curl -s http://127.0.0.1:9527/api/status          # 运行状态
curl -s http://127.0.0.1:9527/api/indices          # 大盘指数
curl -s http://127.0.0.1:9527/api/stocks           # 选股结果+实时行情+AI分析
curl -s http://127.0.0.1:9527/api/realtime         # 纯实时行情
curl -s http://127.0.0.1:9527/api/history/000612   # 单只股票历史分析
```

## 配置文件

`config/config.yaml` 关键配置项：

```yaml
screener.conditions:
  max_pe: 20, min_pe: 3       # 市盈率范围
  max_pb: 3.5                 # 市净率上限
  min_roe: 5                  # ROE 下限（东财加权ROE）
  max_debt_ratio: 65          # 负债率上限
  max_candidates: 20          # 候选股数量

ai:
  api_base: "https://open.bigmodel.cn/api/paas/v4"
  model: "glm-4.7-flash"
```

API Key 存在 `.env` 中（被 .gitignore 排除）：
```
STOCK_AI_API_KEY=4207ea...        # 智谱 API Key
STOCK_AI_API_BASE=https://open.bigmodel.cn/api/paas/v4/chat/completions
STOCK_AI_MODEL=glm-4.7-flash
```

## 项目文件结构

```
stock-dashboard/
├── config/config.yaml                   # 主配置
├── src/
│   ├── main.py                          # 35行  入口 (run/serve)
│   ├── orchestrator.py                  # 流水线编排
│   ├── scheduler.py                     # 调度器 (实时轮询+每日触发)
│   ├── collector/akshare_fetcher.py     # 数据采集 (腾讯API+AKShare)
│   ├── screener/value_screener.py       # 价值投资筛选引擎
│   ├── analyzer/ai_analyzer.py          # AI 分析器
│   ├── models/database.py               # 数据层
│   └── web/routes.py                    # FastAPI 路由
├── scripts/
│   ├── run_pipeline.py                  # 完整流水线
│   ├── run_ai_analysis.py               # AI 分析 (--all 全量)
│   ├── daily_update.sh                  # Shell 版每日更新
│   └── stock-ai-slow-feed.sh            # Hermes cron wrapper
├── .env                                 # API Key (不提交)
└── README.md
```

## 数据库结构

| 表 | 行数 | 说明 |
|:---|---:|:---|
| stock_snapshot | 5527 | 全 A 股基本行情（代码/名称/PE/PB/市值） |
| screening_result | 20 | 最新精选（评分/指标/AI分析） |
| market_index | ~N | 大盘指数历史（上证/深证/创业板/科创50） |
| run_log | 1/N | 流水线运行记录 |
| stock_analysis_history | ~N | AI 分析累积历史（支持多次回看） |

## 定时任务

| 触发方式 | 时间 | 任务 |
|:---|---:|:---|
| 调度器 | 工作日 15:30 | 每日选股流水线 |
| 调度器 | 每 30 分钟 | 大盘指数更新 |
| 调度器 | 每 5 分钟 | 选股池实时价格（仅交易时段） |
| Hermes cron | 凌晨 2/3/4 点 | AI 全量分析（离峰时段） |

## AI 分析重试策略

GLM-4.7-Flash 是免费模型，使用人数众多。脚本采用激进的重试策略：

1. **20次指数退避**：30s → 60s → 120s → 240s → 300s×16
2. **单次最大等待**：约 88 分钟（20次全部重试）
3. **全体分析最坏情况**：约 29 小时（20只×88分钟，极端拥堵）
4. **正常情况**：1-5 只分析成功/每次 cron 执行
5. **失败记录**：当日失败的股票自动标记，24 小时后重试

## 历史开发关键决策

1. **数据源选择**: 腾讯 qt.gtimg.cn 为主要行情源（绕过 TUN 代理阻断），AKShare 补充财务数据
2. **TUN 代理兼容**: 新浪 hq.sinajs.cn 被阻断 → 改为腾讯 qt.gtimg.cn
3. **筛选指标**: PE 3-20/PB<3.5/ROE≥5%/负债率<65%，五项加权评分
4. **UI 风格**: 浅色/深色切换 + Inter 字体 + 红涨绿跌/绿涨红跌双风格
5. **AI Provider**: → GLM-4.7-Flash（免费，20次重试应对拥堵）
6. **右侧面板**: 价值投资指标科普注解（PE/PB/ROE/负债率/增长率/评分体系）
7. **历史分析**: 点击展开详情（合并分析/策略/交易三板块）

## 免责声明

本工具仅供学习研究和参考，不构成任何投资建议。投资有风险，入市需谨慎。
