# Stock Dashboard — A 股价值投资数据面板

> 全自动 · 量化筛选 + 数据展示 · 每日收盘后跑完全市场约 5500 只 → 候选池 ≤20 只
> AI 分析由外部 AI 通过 API 接入（数据读取 + 笔记写回）；面板本身只展示数据，不再内联 AI 分析
> 开发重心：价值投资分析面板优化（P1 面板深化 → P2 体验优化），量化交易系统已归档

---

## 快速开始

```bash
git clone https://github.com/oldfunk/stock-dashboard.git
cd stock-dashboard

# 安装依赖
pip install -e .

# 启动 Web + 内置调度器
python -m src.main serve
# 浏览器打开 http://localhost:9527/
```

### AI 配置（外部 AI，自备实现）

面板本身不跑 LLM、不展示内联 AI 分析。外部 AI 通过数据 API 读取
（`/api/stocks`、`/api/watchlist/{code}/full` 等），分析后写回
（`watchlist_notes`、`ai_journal`、`stock_analysis_history`）。

```env
# .env（仅手动脚本 retry_ai.py 等使用；pipeline 已停用本地触发）
STOCK_AI_API_KEY=your_api_key_here
STOCK_AI_API_BASE=https://opencode.ai/zen/v1
STOCK_AI_MODEL=deepseek-v4-flash-free
```

> 现状（2026-09-21）：OpenCode Zen 免费通道自 9/07 起服务端不可用（403）；
> 免 Key 备用通道（Pollinations）9/18 起同样失败。本地 AI 触发已停用，
> 等外部 AI 消费方排期（通用协议见 `src/ai_proxy/` + `docs/ai-proxy-ai-analysis.md`；
> 作者自用 Hermes 接入）。详见账本最新 Changelog。

### 生产部署（systemd）

```bash
# /etc/systemd/system/stock-dashboard.service
# ExecStart=<部署目录>/.venv/bin/python -m src.main serve
sudo systemctl enable --now stock-dashboard
```

内置调度器自动完成每日流水线（采集→筛选→K 线），无需额外 cron。AI 分析由外部 AI 执行，不走面板触发。

---

## 项目结构

```
stock-dashboard/
├── config/
│   ├── config.yaml           # 全局配置（筛选条件、AI、Web、调度）
│   └── strategies.yaml       # 多策略定义（成长/红利/困境反转阈值）
├── src/
│   ├── collector/            # 数据采集层
│   │   ├── akshare_fetcher.py  # AKShare + 腾讯行情（S1–S8 注册表）
│   │   └── onboard.py          # 新股上市检测
│   ├── screener/
│   │   └── value_screener.py   # 7 条门规筛选 + 多策略评分
│   ├── analyzer/
│   │   ├── ai_analyzer.py      # 本地 LLM 分析（pipeline 已停用，仅手动脚本可用）
│   │   └── watchlist_reviewer.py # 观察池复盘（硬规则本地执行；LLM 决议待外部 AI）
│   ├── ai_proxy/               # 外部 AI 通用代理协议（POST /api/analyze + /api/health）
│   │   └── server.py           # 参考实现（任何外部 AI 实现均可照此协议提供服务）
│   ├── models/
│   │   ├── database.py         # SQLite DAO（screening/analysis 等）
│   │   └── ai_watchlist.py     # 观察池 DAO
│   ├── web/
│   │   ├── routes.py           # FastAPI 路由
│   │   ├── templates/          # Jinja2 模板
│   │   │   ├── index.html            # 首页（候选股总览 + AI 观察池 + 钉选 Tab）
│   │   │   ├── stock_detail.html     # 详情页（数据 + 财务 + K 线；AI 章节为历史展示）
│   │   │   ├── watchlist_detail.html # 观察池详情
│   │   │   ├── journal.html          # 投资日记（外部 AI 笔记展示）
│   │   │   ├── journal_compare.html  # 日记对比
│   │   │   ├── _stock_list.html      # 候选卡 partial（只展示数据，无 AI 内联）
│   │   │   └── _watchlist_card.html  # 观察池卡 partial（只展示数据，无 AI 内联）
│   │   └── static/             # CSS/JS 静态资源
│   ├── orchestrator.py         # 流水线编排（采集→筛选→入库→日志）
│   ├── scheduler.py            # 内置定时调度（daemon 线程）
│   ├── config.py               # YAML 配置加载
│   └── main.py                 # 入口（serve / run）
├── scripts/
│   ├── stock-ai-slow-feed.sh   # 全量流水线（采集+筛选；内含 AI 步骤当前不可用）
│   ├── run_pipeline.py         # 同上，Python 版
│   ├── retry_ai.py             # 补跑失败 AI 分析（手动，本地通道已死，慎用）
│   ├── run_ai_analysis.py      # 慢喂模式（每次 1 只，手动）
│   ├── backtest_topk.py        # TopK 离线回测（历史归档产物，路线已砍）
│   ├── setup-cron.sh           # 定时任务注册脚本（可选，需 hermes CLI 的机器手动执行）
│   ├── verify_valuation.py     # B1 估值验算闸
│   ├── verify_intrinsic.py     # C1 终值验算闸
│   ├── c25_bulk_fill.py        # C2.5 批量补 ROIC/FCF
│   └── daily_cron.sh           # OS cron 兜底（Web 未运行时）
├── tests/                      # pytest（基线 367 passed，2026-09-22 生产服务器 gate 全绿）
│   ├── screener/               # 筛选器单测
│   ├── analyzer/               # AI 分析单测
│   ├── ai_proxy/               # 外部 AI 代理协议单测
│   ├── models/                 # DAO 表单测
│   └── web/                    # 路由 + 模板渲染单测
├── data/db/                    # SQLite 数据库（gitignore）
├── docs/
│   ├── architecture.md         # 架构真相源
│   ├── roadmap.md              # 总路线（P1 面板深化 → P2 体验优化）
│   ├── iteration-log.md        # 迭代进程账
│   ├── handoff.md              # 班次交接速览
│   ├── agent-api.md            # 外部 Agent 接入指南（读数据/写分析/写笔记）
│   ├── ai-proxy-ai-analysis.md # 外部 AI 代理开发规范
│   ├── scheduled-tasks.md      # 定时任务参考设计（制度由使用者自定）
│   ├── paper-trading.md        # 历史归档（量化路线已砍，不再开发）
│   └── strategies-dry-run.md   # 多策略 dry-run 文档
└── tools/                    # 工具目录（对照工具已移除）
```

---

## 工作原理

### 核心理念

**不靠排名，只靠及格线。** 7 条硬性门规的指标过滤 + 本地五维评分，每只股票必须通过全部门规才能进入候选池。

列表卡片（候选/观察池）只展示数据：指标、筛选原因、评分拆解（含行业均值参照）、财务历史、监控条件。
AI 分析（护城河/管理层/估值/交易信号/历史分析文本）**不再内联展示**，由外部 AI
通过数据 API 消费后输出、写回笔记（2026-09-21 方向）。

### 每日流水线（交易日 15:30，完整流程见 `architecture.md` §2）

```
1. 腾讯行情 → 全 A 股行情 → 初筛预过滤
2. AKShare 财务采集（yjbb + 深度补充，含兜底链 C2/C2.5）
3. 历史财务采集 → financial_history → financial_summary（5y/10y 均值）
4. 质量闸（ROE 覆盖 ≥50%）→ 7 条门规筛选 → 评分 → 候选池 ≤20 只
5. K 线拉取（本地 AI 自动触发已停用；外部 AI 消费数据后写回分析/笔记；`retry_ai.py` 等手动脚本因通道死亡暂不可用）
6. 每周六：复盘（硬规则 + 监控条件本地执行；LLM 决议依赖已死通道，失败时整轮跳过，待外部 AI 消费方排期）→ ai_watchlist + ai_journal
```

### 7 条硬性门规

| # | 规则 | 指标 | 豁免 |
|:-:|:-----|:-----|:-----|
| 1 | 5 年均 ROE < 8% → 排除 | `roe_5y_avg` | 高增长(>20%)新上市公司 |
| 2 | 5 年累计 FCF ≤ 0 → 排除 | `fcf_5y_sum` | 小额负 FCF(<1 亿) |
| 3 | 利息覆盖 < 2x → 排除 | `intcov_5y_avg` | — |
| 4 | 毛利率 < 15% → 排除 | `gross_margin` | 高 ROE(≥20%)薄利模式 |
| 5 | OCF/股 ≤ 0 → 排除 | `ocf_per_share` | 高毛利(≥30%)+高增长(≥20%) |
| 6 | 5 年均净利率 < 5% → 排除 | `net_margin_5y_avg` | 高毛利率(≥30%)主动压利 |
| 7 | 5 年稀释 > 20% → 排除 | `share_dilution_5y` | — |

叠加：PE(3~20) / PB(≤3.5) / 营收增长≥0 / 净利增长≥0 / 负债率<65% / 市值 30~50000 亿 / 排除 ST。

### AI 输出结构

```json
{
  "analysis": "投资人笔记风格完整分析",
  "moat_evaluation": [{ "type": "转换成本/网络效应/无形资产/成本优势/有效规模", "score": "1-5", "trend": "稳定", "evidence": "..."}],
  "management_score": { "capital_allocation": "1-10", "shareholder_friendliness": "1-10"},
  "intrinsic_value": { "conservative/base_case/optimistic": "估值(亿)", "margin_of_safety": "安全边际"},
  "reverse_thinking": "2-3 个致死场景",
  "investment_strategy": "仓位和持有周期建议",
  "trade_strategy": { "signal": "BUY/HOLD/AVOID", "confidence": "高/中/低", "buy_zone": "...", "target_price": "...", "stop_loss": "...", "take_profit": "..."}
}
```

候选卡前置摘要已移除（2026-09-21 起列表不再内联 AI）。下述 JSON 仍是外部 AI
应输出/写回的数据契约（`ai_proxy` prompt 与 `ai_analysis` 落库格式以此为准）：

---

## 数据源架构

8 个数据源 S1–S8 完整注册表见 `architecture.md` §5，含兜底链与契约单测要求：

- **行情**：腾讯为主 S1，兜底新浪 → AKShare 自算
- **财务**：AKShare `stock_yjbb_em` 为主 S2，兜底 C2 东财直连
- **深度/历史**：AKShare 摘要/利润表/现金流表 S3–S5，S4/S5 已挂由 C2.5 东财直连永久兜底
- **K 线 / tick**：AKShare S6–S7，东财 → 腾讯回退

> 事故教训已固化为三条铁律：删适配函数 = 删注册表行 + 删契约单测，三者同 commit（见 `architecture.md` §5.2）。

---

## 脚本

| 命令 | 用途 |
|---|---|
| `python -m src.main serve` | 启动 Web + 内置调度器 |
| `python -m src.main run` | 执行一次采集 + 筛选（不含 AI 分析） |
| `bash scripts/stock-ai-slow-feed.sh` | 全量脚本（采集 + 筛选；内含 AI 步骤当前因通道死亡不可用） |
| `python3 scripts/retry_ai.py` | 补跑最新 run 中失败的 AI 分析（手动；本地通道已死，慎用） |
| `python3 scripts/retry_ai.py --all-failed` | 扫所有 run 里的失败记录 |
| `python3 scripts/retry_ai.py --dry-run` | 只列清单不调用 |
| `python3 scripts/run_ai_analysis.py` | 慢喂模式（每次 1 只，适合 cron） |
| `python3 scripts/verify_valuation.py` | B1 估值验算闸 |
| `python3 scripts/verify_intrinsic.py` | C1 终值验算闸 |
| `python3 scripts/c25_bulk_fill.py` | C2.5 批量补 ROIC/FCF |

---

## 配置

详见 `config/config.yaml`：

| 配置项 | 说明 |
|---|---|
| `screener.conditions.*` | 7 条门规阈值（ROE/FCF/利息覆盖/毛利率/OCF/净利率/稀释） |
| `screener.multi_strategy` | 多策略开关（默认 false，true 走 strategies.yaml 三池） |
| `strategies.yaml` | growth/dividend/turnaround 三策略阈值 + alpha_criteria + exit_triggers |
| `schedule.daily_update_time` | 每日运行时间（默认 15:30） |
| `web.port` | 看板端口（默认 9527） |
| `ai.*` | 本地 LLM 配置（pipeline 已停用本地触发；Zen 主通道 + `ai.fallback` 备用通道均已确认不可用，仅手动脚本可用） |
| `ai_proxy.*` | 外部 AI 代理协议配置（主/备双通道，不绑定具体 AI 实现） |
| `ai_review.*` | 周六 AI 复盘配置（观察池容量、硬规则） |

本地覆盖：`config/local.yaml`（gitignore），YAML 合并到 config.yaml。

---

## 技术栈

- **数据采集** — httpx + AKShare（东方财富/同花顺底层）+ 腾讯行情 API
- **存储** — SQLite（WAL 模式，`data/db/stock_dashboard.db`）
- **AI 分析** — 外部 AI 通过数据 API 读取、分析后写回笔记（通用协议 `src/ai_proxy/`；作者自用 Hermes 接入）。本地 Zen/Pollinations 通道 9/07 起相继不可用，本地触发已停用。
- **Web 看板** — FastAPI + Jinja2（候选卡 / 观察池卡双 partial，均只展示数据；2026-09-21 起不再内联 AI 分析）
- **调度** — 内置 `src/scheduler.py`（daemon 线程，采集→筛选→K 线；不触发 AI）+ systemd 常驻
- **验证** — `bash scripts/gate.sh`（全仓 pytest，2026-09-22 生产服务器基线 367 passed）

---

## 方法论来源与设计取舍

价值投资门规与分析框架为本项目自有实现，方法论原则（能力圈、护城河、安全边际、一票否决纪律）源自公开价值投资常识，不绑定、不镜像任何外部项目。设计取舍：

1. **自动化** — 全自动采集、筛选、复盘、报告，无需手动跑
2. **本地数据仓库** — 逐日累积历史财务数据
3. **A 股适配** — 数据源 AKShare/腾讯，规则兼容 A 股特性
4. **简化代理指标** — 利息覆盖/稀释率/FCF 使用 AKShare 可获取字段
5. **结构化分析契约** — 外部 AI 应输出护城河/管理层/估值/策略 JSON（格式见上节；面板只展示数据与笔记，不内联分析）
6. **落库追踪** — 论点/假设/红线/笔记全部落库跨期对照，不做事后一次性报告

---

## 开发

### 工作流

1. 从 `main` 切出 `nightly/YYYYMMDD` 分支
2. 改动 + 单测 + 全仓 pytest 零失败（基线 367 passed 只升不降，确切数见账本最新 Changelog）
3. 推送 nightly → 用户批准后合并到 main → 同步生产服务器
4. 生产环境：生产服务器 `stock-dashboard.service` :9527（部署目标见本地 `deploy.local.md`，不入库）

### 约束

- 单模块改动 + 单测 + 零 emoji（仅 → ↑ ↓ ✓）
- 迭代在 `nightly/*`，合并到 `main` 需用户批准（规则见 `AGENTS.md`：默认推分支、永不自动合并；合并须用户批准，合并后由 agent 同步生产服务器）
- 踩坑铁律 6 条：见 `iteration-log.md` §工程约定

### 文档约定

单一事实源，不重复维护：

| 文档 | 定位 |
|---|---|
| `docs/architecture.md` | 架构真相源：结构图、流水线、模块边界、数据源 S1–S8 |
| `docs/roadmap.md` | 总路线（P1 面板深化 → P2 体验优化） |
| `docs/iteration-log.md` | 迭代进程账（Hermes agent 上下文源，含 backlog） |
| `docs/handoff.md` | 班次交接速览（历史追加不删） |
