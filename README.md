# Stock Dashboard — AI 驱动的 A 股价值投资选股看板

> 全自动 · 量化筛选 + 结构化 AI 分析 · 每日收盘后跑完全市场约 5500 只 → 候选池 ≤20 只
> 最终目标：让 AI 接管投资决策（见 `docs/roadmap.md` 总路线）

---

## 当前阶段（Stage Marker）

> **M2 策略分化收尾 → M3 持有纪律前夜 · 更新 2026-09-15 · 分支 `nightly/20260914` ahead 2**

| 里程碑 | 状态 | 关键产出 | 说明 |
|---|---|---|---|
| **M1 分析可信** | ✓ 已完成 | B1 估值验算闸 + B2 结论三态 | 每个候选估值可验算，三档价格区间，目标 9-14 已达成 |
| **M2 策略分化** | → 进行中 90% | `strategies.yaml` 三策略 + `strategy_tags` 落库 + 首页策略 Tab | 后端核心 `402cd5e` 与 Tab `293e08d` 已合入 `main`；仅剩 `orchestrator` multi 开关收尾（`t_dbebf27e`） |
| **M3 持有纪律** | 计划 10 月 | B5 论点漂移 + B6/B7 状态机与周报 | prompt 已就位待周六 live 验证 |
| **M4a 自研纸盘** | 规划 Q4 | `src/paper/` + `paper_*` 五表 | 信号→委托→持仓→净值全链路（见 `paper-trading.md`） |
| M4b QLib 离线验证 | 并行 | PC/云 TopK 回测 | 只回流结论不回流代码 |
| M4c 券商仿真 | 待定 | QMT 模拟首选 / PTrade 备选 | 需 Windows+券商账户 |
| M4d 实盘预备 | 达标后议 | BrokerAdapter+人工闸 | 仿真 3 个月达标 + 明确下令才启动 |

**本周在做（Active Kanban `stock-trading` 板）**
- `t_a217d8a2` [P0] 账本对齐（docs 真相源修复）→ ready
- `t_dbebf27e` [P0] orchestrator 接 multi_switch
- `t_34a0cdc1` [P1] paper_* 五表 schema
- `t_1c8b8467` [P1] C3 数字抽检 warn-only
- `t_08938e57` [P2] BrokerAdapter 接口
- `t_eef5de93` [P2] 上游跟踪月检

生产：`pi1 192.168.50.210` `stock-dashboard.service` :9527（`main` 部署，Hermes 只推 `nightly/*` 不碰生产）

---

## 文档导航（单一事实源）

| 文档 | 定位 |
|---|---|
| `docs/architecture.md` | 架构真相源：结构图、每日流水线、模块边界禁令、数据源 S1–S7 注册表、防回归门禁 |
| `docs/roadmap.md` | 总路线 M1–M4d（含子路线沉淀） |
| `docs/paper-trading.md` | 虚拟盘/量化接入选型与 M4a–M4d 设计草案 |
| `docs/iteration-log.md` | 迭代进程账（Hermes agent 上下文源，含 backlog 与踩坑铁律） |
| `docs/handoff.md` | 班次交接速览（三段重写 + 历史追加不删） |

> 约定：`README` 仅放摘要与链接，完整表格/清单只在对应文档维护一处（见铁律 #3）。

---

## 快速开始

```bash
git clone https://github.com/oldfunk/stock-dashboard.git
cd stock-dashboard
# 推荐：proot Debian（glibc，Python 3.13）一键验证闸
bash ~/work/gate.sh          # → 导入 8/8 + 236 passed + 改动文件 emoji clean
# 或本地
pip install -r requirements.txt 2>/dev/null || pip install akshare fastapi uvicorn jinja2 httpx python-dotenv schedule
python -m src.main serve
# 浏览器打开 http://localhost:9527/
```

### AI 配置（可选）

```env
# .env（免费模型无需 Key，自动从 OpenCode Zen 发现可用 free 模型）
STOCK_AI_MODEL=deepseek-v4-flash-free
STOCK_AI_API_KEY=your_api_key_here          # 仅非免费模型需要
STOCK_AI_API_BASE=https://opencode.ai/zen/v1
```

### 生产部署（systemd，pi1）

```bash
# /etc/systemd/system/stock-dashboard.service
# ExecStart=/home/pi/stock-dashboard/.venv/bin/python -m src.main serve
sudo systemctl enable --now stock-dashboard
```

内置调度器自动完成每日流水线 + AI 分析，无需额外 cron。`scripts/daily_cron.sh` 仅作 Web 未运行时的 OS cron 兜底。虚拟盘/回测见 `paper-trading.md`。

---

## 工作原理

### 核心理念

**不靠排名，只靠及格线。** 基于 AI Berkshire 7 条门规的硬性指标过滤，结合 LLM 结构化分析，每只股票必须通过全部门规才能进入候选池。

候选股展示 4 个结构化标签页（Analysis / Strategy / Risks / Trade），Trade 标签含 Signal + 置信度 + 买入区间 + 目标价 + 止损 + 止盈；首页新增策略 Tab（全部/成长/红利/反转）按 `strategy_tags` 过滤。

### 每日流水线（交易日 15:30，完整流程见 `architecture.md` §2）

```
1. 腾讯行情 → 全 A 股行情 → 初筛预过滤
2. AKShare 财务采集（yjbb + 深度补充，含兜底链 C2/C2.5）
3. 历史财务采集 → financial_history → financial_summary（5y/10y 均值）
4. 质量闸（ROE 覆盖 ≥50%）→ 7 条门规筛选 → 评分 → 候选池 ≤20 只（含 strategy_tags）
5. K 线拉取 → AI 分析（每日，失败可用 scripts/retry_ai.py 补跑）
6. 每周六：AI 复盘 → ai_watchlist（5 只）+ ai_journal（B6/B7 模板，待 live 验证）
```

### AI Berkshire 7 条门规

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

候选卡前置摘要：`moat_type` / `mgmt_score` / `iv_range`（`_enrich_stocks` 透传，旧分析 NULL 行守卫为不渲染）。

---

## 数据源架构

7 个数据源 S1–S7 完整注册表见 `architecture.md` §5，含兜底链与契约单测要求：

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
| `bash ~/work/gate.sh` | 本机验证闸（Debian）：导入冒烟 + 全仓 pytest + 改动文件 emoji 扫描 |
| `bash scripts/stock-ai-slow-feed.sh` | 全量脚本（采集 + 筛选 + AI 分析） |
| `python3 scripts/retry_ai.py` | 补跑最新 run 中失败的 AI 分析 |
| `python3 scripts/retry_ai.py --all-failed` | 扫所有 run 里的失败记录 |
| `python3 scripts/retry_ai.py --dry-run` | 只列清单不调用 |
| `python3 scripts/verify_valuation.py` | B1 估值验算闸 |
| `python3 scripts/verify_intrinsic.py` | C1 终值验算闸 |

---

## 配置

详见 `config/config.yaml`：

- `screener.conditions.*` — 7 条门规阈值
- `screener.multi_strategy` — 多策略开关（默认 false，M2 收尾接入 orchestrator）
- `strategies.yaml` — growth/dividend/turnaround 三策略阈值（`thresholds`）与 `alpha_criteria`/`exit_triggers`
- `schedule.daily_update_time` — 每日运行时间（默认 15:30）
- `web.port` — 看板端口（默认 9527）
- `ai.*` — LLM API 配置（默认 OpenCode Zen 免费模型池）

---

## 技术栈

- **数据采集** — httpx + AKShare（东方财富/同花顺底层）+ 腾讯行情 API
- **存储** — SQLite（WAL 模式，`data/db/stock_dashboard.db`）
- **AI 分析** — OpenAI 兼容 API + OpenCode Zen 免费模型池（自动故障轮换，FreeModelPool）
- **Web 看板** — FastAPI + Jinja2（单模板 `_stock_list.html` 被多页 include，样式写在 partial 内）
- **调度** — 内置 `src/scheduler.py`（daemon 线程）+ systemd 常驻
- **验证** — `bash ~/work/gate.sh`（proot Debian Python 3.13 + pandas 2.3.3 + curl，基线 236 passed）
- **虚拟盘（M4a 规划）** — 自研 `src/paper/` 引擎，详见 `paper-trading.md`

---

## 与 AI Berkshire 的关系

量化筛选对齐 `quality-screen.md`，A 股适配：

1. **自动化** — Berkshire 需手动跑，本项目全自动
2. **本地数据仓库** — 逐日累积历史财务数据
3. **A 股适配** — 数据源换 AKShare/腾讯，规则兼容 A 股特性
4. **简化代理指标** — 利息覆盖/稀释率/FCF 使用 AKShare 可获取字段
5. **结构化分析** — 每只股票输出完整护城河/管理层/估值/策略 JSON

`tools/berkshire/` 保留上游工具对照（`quality-screen`/`financial_rigor` 等），A 股侧以 `src/` 实现为准。

---

## 开发

- 看板：`hermes kanban --board stock-trading list`（当前 1 ready + 5 todo，per_profile=2 并发）
- 分支：`main` 受保护（pre-push 钩子硬拦），迭代在 `nightly/*`（`git fetch origin && git rebase origin/main` 再推）
- 约束：单模块改动 + 单测 + 零 emoji（仅 → ↑ ↓ ✓）+ 全仓 pytest 零失败（基线 236 passed 只升不降）
- 踩坑铁律：见 `iteration-log.md` §踩坑铁律（6 条，含共享 partial CSS 放置与合并前查共改文件）
