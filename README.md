# 价值投资选股看板

📊 基于 AKShare + AI 的 A 股价值投资选股工具。

## 功能

- **全市场数据采集**：每日开盘/收盘后采集 A 股全市场基本面数据
- **价值投资筛选**：基于格雷厄姆/巴菲特理念的多条件量化过滤
- **AI 深度分析**：对筛选出的股票逐一生成选股解析、投资策略、买卖策略
- **Web 看板**：浏览器实时查看大盘数据、选股结果

## 快速开始

```bash
# 1. 安装依赖
pip install -e .

# 2. 配置 AI API Key（可选，不配则只做量化筛选）
cp .env.example .env
# 编辑 .env 填入你的 API Key

# 3. 启动 Web 看板
python -m src.main serve

# 4. 手动运行选股
python -m src.main run
```

打开浏览器访问 `http://<你的IP>:9527`

## 定时任务

每天收盘后（15:30）自动运行，用 cron 或 Hermes cron：

```bash
# 系统 cron
crontab -e
30 15 * * 1-5 /home/debian/stock-dashboard/scripts/daily_update.sh
```

## 目录结构

```
stock-dashboard/
├── config/
│   ├── config.yaml       # 主配置（筛选条件、数据源、AI 参数）
│   └── local.yaml        # 本地覆盖（被 .gitignore 排除）
├── data/db/              # SQLite 数据库
├── src/
│   ├── collector/        # 数据采集（AKShare）
│   ├── screener/         # 量化筛选引擎
│   ├── analyzer/         # AI 分析层
│   ├── models/           # 数据模型 & 数据库
│   ├── web/              # FastAPI + Jinja2 看板
│   ├── orchestrator.py   # 主流程编排
│   └── main.py           # 入口
├── scripts/              # 工具脚本
└── .env.example          # API Key 配置模板
```

## 选股条件（可配置）

默认价值投资过滤条件：
- PE: 3 ~ 20
- PB: < 3.5
- ROE: ≥ 12%
- 营收增长率: ≥ 5%
- 净利润增长率: ≥ 5%
- 负债率: < 65%
- 流通市值: 50亿 ~ 10000亿

编辑 `config/config.yaml` 中的 `screener.conditions` 调整。

## 2.0 规划

- [ ] AI 自动交易下单（对接券商 API）
- [ ] 港股/美股市场接入
- [ ] K 线图 & 技术指标
- [ ] 价格预警推送（Telegram/Discord）
- [ ] 投资组合管理 & 回测

## 免责声明

本工具仅供学习研究和参考，不构成任何投资建议。投资有风险，入市需谨慎。
