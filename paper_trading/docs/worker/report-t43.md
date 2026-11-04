# report-t43 · 架构地图（Qoder，P1·Batch 9 独立穿插，补交 Batch 5 欠件）

分支 `worker/t43`（基于 `origin/main@b5983f6`，与其它 T4x 无依赖，可单独合并）。
产出：`docs/architecture-map.md` + 可重跑的取证脚本 `spikes/arch_map_scan.py`
+ 它的输出 `spikes/arch_map_data.json`（13.6 KB，数字都能复算）。

## 0. 一句话结论

地图按派发的四件事都做了（模块清单工具统计、import 实测依赖图、L1/L2 现状 vs 目标、
重复/死代码/测试盲区），量出来的底盘是 **72 个模块 / 16,517 行 / 45 个测试文件**；
**待退役的 L1 自研约 2,984 行**（撮合+账本+回测+规则+NAV+风控），
而新线 `rq_engine/` 只有 **1,336 行**——差的那块正是 T45 §0 报的 G1/G2（不写账本、没有 NAV 记录器）。
另外这轮我**先误判后撤回**了三个"重复/死码"结论，所以地图开头先把工具误差写明白。

## 1. 逐条核销清单（`docs/WORKER.md:692-694` T43 原文）

| 派发原文 | 状态 | 落地点 |
|---|---|---|
| 「模块清单工具统计」 | ✅ | 地图 §1：按包 14 行的表（模块数/行数/层/职责），合计 72/16,517；数字来自 `spikes/arch_map_scan.py`，非手抄 |
| 「import 实测依赖图」 | ✅ | §2：入口层→编排层→能力层→契约/工具的四层图 + 49 条内部边 + 扇入排名（`models` 16、`utils` 14）；并写明**静态图看不见 rqalpha 反射装载的 mod**（`rqalpha_mod_ptpaper`），别把图当完备 |
| 「L1/L2 现状 vs 目标」 | ✅ | §3：引 `refactor-plan-open-source.md:15-27` 的 L1/L2 原文定义，八项能力逐条对照（撮合/T+1/费用/回测/风控/NAV/AI 链/数据供给），每项给现状位置、目标、迁移状态；其中**回测至今仍是旧 `Backtester`**（`cli.py:1461/2122/2240`）与 M1 行"rqalpha 接管日线回测"不符，是地图里最扎眼的一条 |
| 「重复/死代码/测试盲区」 | ✅ | §4 重复（费率 3 处真相源、价格映射 2 处；另有 3 项经复核**判为不是重复**）；§5 死码（只有 `main.py` 188 行经人工验证成立，`agent/schedule.py` 是有意兼容面）；§6 盲区（51/72 模块被测试提到，真盲区排序给到 `portfolio.portfolio` NAV 公式等 4 条） |

## 2. 这轮我自己犯的错（写进地图开头，不是隐藏）

工具用 AST 只看 `import` 语句，**看不见包 `__init__.py` 的再导出**，于是：
- 误判 `integration/analysis.py`、`llm/presets.py` 是死代码 ⇒ 实际分别经
  `integration/__init__.py:8`、`llm/__init__.py:8` 再导出在用，**已撤回**；
- 误判"交易日历重复 19 处" ⇒ `is_trading_day` 全仓只有
  `utils/trading_calendar.py:230` 一处定义，19 次是引用；
- 误判"股票池解析 3 处重复" ⇒ `integration/pool.py:89` 是唯一实现，三处是调用；
- 启发式的"全仓零引用"把 `engine/__main__.py` 也报了 ⇒ 它正是 cron 的
  `python -m paper_trading.engine --once` 入口（`AGENTS.md:53-70`）。

**教训**：静态启发式可以给我候选，但"死代码/重复"这种要动手删的结论必须逐个 grep + 读码验证。
我在 §4/§5 里把"工具报的"和"人工确认的"分开写，就是为了下次不再犯同样的误判。

## 3. 地图里最有行动价值的三条

1. **回测还没换**：M1 行写"rqalpha 接管日线回测"，但 `engine backtest` 子命令仍跑旧
   `Backtester`（696 行）。这与 M2/M3 的"cutover 就绪"判定不是一回事——
   面板/分析层的**回测数字**至今还是自研撮合，切换评审时别把它当成"已经用框架了"。
2. **费率有三个真相源**：`models/types.py:119-122`（现网账本）、
   `engine/backtest.py:22 FeeModel`（回测，字段镜像）、
   `integration/rq_engine/costs.py:31-36`（新线缺省常量）。
   T38 把 CLI 两侧统一到 `broker.config`，但 `cli.py:2173/2228` 与
   `dashboard.py:906` 仍各自 `FeeModel(...)` ⇒ **改 `config.yaml` 的费率，
   回测/面板路径可能不跟着改**。建议派一件小任务把三者收到一处（我建议都从 `TradingConfig` 取）。
3. **NAV 公式没有直接测试**：`portfolio/portfolio.py` 只有 61 行、只被间接经过；
   而它正是切换后最容易断的东西（T45 §0 G2、T46 清单 2.3）。补三条断言很便宜。

## 4. 边界

- 纯文档 + 只读脚本，**没改任何产品代码**，没删任何东西（含 `main.py`）。
- `spikes/arch_map_scan.py` 只读源码、只写 `spikes/arch_map_data.json`。
- 数字基于 `main@b5983f6`；T41/T42/T45/T46 合入后行数会变，重跑脚本即可
  （地图 §7 写了这条，避免以后被当成过期真理）。
