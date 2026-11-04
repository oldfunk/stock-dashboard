# 架构地图（T43，2026-10-29）

工具：`spikes/arch_map_scan.py`（纯标准库 AST，产数据到 `spikes/arch_map_data.json`）；
基线 `main@b5983f6`。地图里的**每个数字都能重跑出来**，跑法：

```bash
python spikes/arch_map_scan.py .        # 72 模块 / 16,517 行 / 45 个测试文件
```

**方法与已知误差**（先说这个，否则后面的数字会被当成客观真理）：
脚本只解析 `import` 语句，所以**包 `__init__.py` 的再导出会看不见**——
我一开始据此把 `integration/analysis.py`、`llm/presets.py` 判成死代码，
人工复核发现它们分别经 `integration/__init__.py:8` 与 `llm/__init__.py:8` 再导出在用，
**误判已撤回**。所以本图的"死代码"一栏只用**人工逐个 grep 验证过**的结论，
不用启发式的 `never_referenced`（那把 `engine/__main__.py` 也误判了——
它是 `python -m paper_trading.engine` 的 cron 入口）。

## 1. 模块清单（按包）

| 包 | 模块数 | 行数 | 层 | 一句话职责 |
|---|---|---|---|---|
| `engine/` | 11 | 2,883 | **L1 待退役 + L2 门禁混装** | 自研回测、规则引擎、条件单、provenance、optimize |
| `integration/` | 16 | 2,836 | L2（含新 L1 适配层） | 母库适配器、`rq_engine/`（rqalpha 接线 1,336 行）、池/口径 |
| `cli.py` | 1 | 2,408 | L2 | 58 个 `add_parser` 的子命令总入口 + `TradingBridge` |
| `data/` | 9 | 1,944 | L2 | akshare 采集、`raw_feed` 管道、`db_manager`、实时快照、质量 |
| `agent/` | 7 | 1,908 | L2 | AI 决策链（context/prompts/loop/runtime/events/schedule） |
| `dashboard.py` | 1 | 1,519 | L2 | stdlib `http.server`（`Handler` 在 :1010，`do_GET/do_POST` 两个入口） |
| `utils/` | 9 | 980 | L2 | 日历、市场时段、心跳、锁、配置、jsonx、logger |
| `broker/` | 2 | 823 | **L1 待退役** | 现网账本 + 自研撮合/T+1/涨跌停/费用（`paper_broker.py` 819 行） |
| `strategy/` | 4 | 320 | L2 | MA 交叉等规则集与 scheme |
| `llm/` | 4 | 286 | L2 | provider/密钥/预设 |
| `risk/` | 2 | 202 | L1 判定（框架缺口，登记自研保留件） | 风控状态与判定 |
| `main.py` | 1 | 188 | **疑似死码** | 见 §5 |
| `models/` | 2 | 153 | L2 契约 | `Bar/Order/Position/TradingConfig` |
| `portfolio/` | 2 | 65 | L1 待退役 | NAV 记录（61 行，很薄） |

合计 **72 模块 / 16,517 行**（不含 tests 与 spikes）。

## 2. import 实测依赖图（顶层流向，AST 数出来的 49 条内部边）

```
入口层    cli.py ──┬── dashboard.py ──┐        （两者互不 import，各自 new TradingBridge/读库）
           engine/__main__.py ────────┤
           main.py（自引用，见 §5）    │
                                       ▼
编排层    agent.loop / agent.runtime / engine.runner / engine.conditional
                                       │  用
                                       ▼
能力层    broker.paper_broker  portfolio.portfolio  risk.risk_manager
          data.raw_feed  data.db_manager  data.akshare_fetcher  data.realtime  data.quality
          integration.mother / analysis / pool / universe / sources / settings
          integration.rq_engine.{runner,shadow,store,provider,costs,bands,data_source,mod_entry}
          strategy.*  llm.*  engine.{backtest,rules,config,provenance,optimize,state}
                                       │  只依赖
                                       ▼
契约/工具  models.types（16 个入边，全仓第一）
           utils.*（14 个入边：日历/时段/心跳/锁/logger/jsonx/config）
```

- **扇入最大的是 `models` 与 `utils`**（契约与工具，方向正确：下层不回头依赖上层）。
- 实测**没有** `data → integration` 的反向耦合，但 `integration.mother` 被
  `data.raw_feed` 引用（口径对照），这条是刻意的"独立基准"依赖，不是环。
- `rq_engine/mod_entry.py` 由 rqalpha 按 `rqalpha_mod_ptpaper` 名字装载
  （`runner.py:25-41` 预注册 `sys.modules`），**所以它在静态 import 图里是孤儿**——
  这是本图最需要读的人知道的一条：静态图看不见框架反射装载。

## 3. L1 / L2 现状 vs 目标（按 `refactor-plan-open-source.md:15-27` 的定义）

定义原文：**L1**「撮合/账本/T+1/涨跌停/费用/滑点/回测/风控**判定**……任何影响成交价、
持仓、盈亏、下单/拒绝结论的**数值**，必须来自框架 API 返回值」；
**L2**「AI 决策链、数据供给、面板/CLI/op_log、cron 运维、**安全门禁**」。

| 能力 | 现状（自研，L1 位置） | 目标 | 迁移状态 |
|---|---|---|---|
| 撮合 / 部分成交 / 价格限制 | `broker/paper_broker.py:284`（自带涨跌停幅度：688/300=20%、8/4=30%、其余 10%）、`:310` 涨跌停检查 | rqalpha `sys_simulation` + `utils/price_limits.py` | 适配层已建（`rq_engine/store.py` 供 band），**但新线不写账本**（T45 §0 G1） |
| T+1 | `paper_broker.py` `t1_freeze`/`unfreeze_t1` | rqalpha `position.py:244-248`（`before_trading` 解冻） | 影子对账已证两侧一致（T32/T42） |
| 费用 | `engine/backtest.py:22 class FeeModel` + `models/types.py:119 TradingConfig` | 框架 `sys_transaction_cost` + 我们的过户费 overlay | T38 已把现网费率钉进 `build_config`（`costs.py:158` 行） |
| 回测 | `engine/backtest.py` 696 行 + `engine/optimize.py` + `provenance.py` | rqalpha 回测（M1 行原文"rqalpha 接管日线回测"） | **未接管**：`cli.py:1461/2122/2240` 的 `engine backtest` 仍走旧 `Backtester` |
| 风控判定 | `risk/risk_manager.py:198` | 框架缺口 → 登记「自研保留件」+ parity 门禁（定义里允许） | 保持，需 parity 门禁在案 |
| NAV | `portfolio/portfolio.py:61` + `paper_broker.record_nav` | 新线侧记录器**不存在**（T45 §0 G2） | 缺口 |
| AI 链 / 门禁 / 面板 / op_log / cron | `agent/`、`engine.rules+conditional`、`dashboard.py`、`cli.py`、`utils/` | **L2，全程不动**（M2 原文） | 不动 ✓ |
| 数据供给 | `data/`（akshare/母库对照/raw 管道/分钟线）+ `integration/mother.py` | L2 ✓ | T31/T34/T41 加固 |

**量化一下"待退役"的体量**：`paper_broker.py` 819 + `engine/backtest.py` 696 +
`engine/runner.py` 646 + `engine/conditional.py` 238 + `engine/rules.py` 326 +
`portfolio/portfolio.py` 61 + `risk/risk_manager.py` 198 ≈ **2,984 行**，
其中只有 `risk`（框架缺口保留件）与 `rules/conditional` 的**判定输入**属于"该留的 L2"，
真正的 L1 数值/状态机部分约 **1,500–1,800 行**是切换后要封存只读的。
新线替代品目前只有 `rq_engine/` 的 **1,336 行**——差的那块就是 G1/G2。

## 4. 重复实现清单（都是"两处真相源"，将来会漂移的地方）

| 主题 | 处数 | 具体位置 | 我的判断 |
|---|---|---|---|
| 费率配置 | ~~3~~ → **1**（T51 已收敛） | 原三处：`models/types.py:119-122`（TradingConfig）、`engine/backtest.py:22`（FeeModel 镜像）、`integration/rq_engine/costs.py:31-36`（新线常量）。T51 后：数值唯一 = `TradingConfig`，公式唯一 = 新 `utils/fees.py`（broker/`FeeModel`/`m3_verify` 三路共用），构造唯一 = `utils/config.py:trading_config_from_dicts`；`costs.py` 只剩框架基准率当除数。当时判断"改 `config.yaml` 的回测/面板路径仍可能与分析路径不同源"——**实测比判断更糟**：那三处读的是不存在的键 `load_config(...)["trading"]`，所以一直吃硬编码兜底（`report-t51.md §1`，端到端 5.08→50.23） |
| 价格映射 | **2** | `cli.py:325 TradingBridge.price_map`、`engine/runner.py:49 price_map(bridge, quotes, symbols)` | 后者是前者的另一份逻辑；尺度污染事故（`op_log data:incident`）正是这一族的问题 ⇒ 该合，且合并前两条都要有单测 |
| 股票池解析 | 3 处入口但**1 个实现** | `integration/pool.py:89 resolve_pool` 是唯一实现，`cli._resolve_syms` / `dashboard` / `engine.runner` 都调它 | ✅ 不是重复（工具报了 3 处，人工复核为"一处实现三处调用"） |
| 交易日历 | **1 个实现** | `utils/trading_calendar.py:230 is_trading_day`（唯一 `def`），`market_time.py` 只管时段不重复日期判定 | ✅ 不是重复（工具的"19 处命中"是引用不是定义）——**这条是我自己先误判、复核后撤回的** |
| 心跳/健康 | 1 实现 2 消费 | `utils/heartbeat.py`（`touch/age/due/DEFAULT_THRESHOLDS`）被 `agent.loop`、`cli health`、`utils/m3_verify` 用 | ✅ 正常 |

## 5. 死代码候选（只用人工验证过的）

| 候选 | 证据 | 建议 |
|---|---|---|
| `paper_trading/main.py`（188 行，`class PaperTradingEngine`） | 全仓（含 tests/spikes/docs/*.md）只在该文件自己出现：`main.py:27` 定义、`main.py:175` 自用；没有任何 import、cron、文档指向它 | **确认是遗留入口**，但按 L2 硬规则"门禁/审计零删除"与"旧模块只读保留一个版本周期"（M3 行原文），**建议先标 deprecated 注释、M3 后再删**，不在本任务删 |
| `agent/schedule.py` | `AGENTS.md:58` 明说生产不再读该队列，但 `cli.py:2050/2063` 仍提供 `agent schedule list/set` 兼容 | **不是死码**（有意的兼容面），别顺手删 |
| `integration/analysis.py`、`llm/presets.py` | 分别经 `integration/__init__.py:8`、`llm/__init__.py:8` 再导出在用 | **误判撤回**（见开头的方法误差） |

## 6. 测试盲区（启发式 + 人工核对）

工具口径：72 模块里 **51 个**被某个测试文件（45 个 `test_*.py`，当前 499 用例）
按"模块路径或其 ≥8 字符的公开符号"提到；剩下**有公开符号却没被提到**的 5 个：

| 模块 | 行数 | 人工判断 |
|---|---|---|
| `integration/rq_engine/__init__.py` | 28 | 只是门面（`rq_alpha_available`），其子模块都有测试 ⇒ **伪盲区** |
| `llm/presets.py` | 70 | 真盲区：预设 URL/键名没有断言，改坏了要到运行时才发现（低危，但值得一条） |
| `main.py` | 188 | 死码候选，不需要测试（与 §5 同一决定） |
| `portfolio/portfolio.py` | 61 | **真盲区，且是钱类**：NAV 快照公式（市值=Σ持仓×价）没有直接测试，只被 `agent`/`cli` 间接经过；M3 后它是"净值断供"的中心（G2），建议补 3 条 |
| `strategy/base_strategy.py` | 36 | 抽象基类，伪盲区 |

**真正的盲区排序（我建议补的顺序，都属钱类或门禁）**：
① `portfolio.portfolio`（NAV 公式）；② `engine/runner.py` 的 `price_map`（尺度污染族）；
③ `data/quality.py` 的 gap 检测（面板显示"缺 6 个交易日"这类判据的源头）；
④ `llm/presets.py`。

## 7. 这张图缺什么（别把它当完备）

- 静态 import 图**看不见**：rqalpha 反射装载的 mod（`rqalpha_mod_ptpaper`）、
  `importlib` 动态导入、母库侧的表结构依赖、NEC/Pi 的跨机运行边界（见 `migration-nec.md`）。
- 它也不表达"谁写哪张表"——那部分我另写进了 `migration-m3.md` §3 与 `m3-checklist.md`，
  因为切换风险主要在那里，不在 Python 的 import 上。
- 数字随分支变化：本图基于 `main@b5983f6`；`worker/t41/t42/t45/t46` 合入后
  会多出 `utils/m3_verify.py`、`rq_engine/costs.py` 的行数，**重跑脚本即可**。
