# report-t7 — rqalpha `AbstractDataSource` 取证 + 口径归口设计（T7，M0b 关键路径）

执行人：员工 AI（Qoder）｜2026-10-27 深夜
环境：`C:\Users\yanzh\venvs\refactor-spike`（Python 3.13 + rqalpha **6.4.2** + bundle `rqbundle_202610`，3.3 GB）
取证方式：读已安装的 rqalpha 源码（AST 枚举 + 定点读）+ 直接打开 bundle 的 h5 取**真实市场数据**
红线遵守：**未写任何产品代码**；未碰 Pi / 生产库 / secrets

> ## 口径结论（先看这个）
> **走路线 B：撮合/NAV/涨跌停基准吃「不复权真实价 + 复权因子 + 分红元数据」，
> 前复权只作为"指标视图"由 rqalpha 现场换算（`adjust_type='pre'` + `adjust_orig=当日`）。**
> 理由一句话：**只有 B 能在除权日把净值算对，同时让回测可复现、与现有账本同口径。**
> 代价：`data.db.daily_bars` 要从"存前复权"改成"存不复权"，并且**涨跌停带与除息参考价
> 必须由我们的适配器显式计算**（rqalpha 只消费，不替我们算）。
> 与文档的冲突点：AGENTS 3.2 现写"母库日线取价优先 `close_qfq`"——按本结论，
> **价格入库口径要改成不复权**（qfq 退为展示/指标口径），请主 AI 决定是否同步改 AGENTS。

---

## 1. `AbstractDataSource` 强制接口清单（文件:行，共 21 个方法）

`C:\Users\yanzh\venvs\refactor-spike\Lib\site-packages\rqalpha\interface.py`，
`class AbstractDataSource` 定义于 **L279**，方法均无 `@abstractmethod`（**不实现就运行期炸**，
不是导入期——M0 spike 必须真跑一条策略才能暴露缺哪个）。

| 行 | 方法 | 我们必须给 / 可用 bundle |
|---|---|---|
| L286 | `get_instruments(id_or_syms, types)` | **bundle** `instruments.pk` |
| L294 | `get_trading_calendars()` | **bundle** `trading_dates.npy`（实测 20050104→20271231） |
| L301 | `get_yield_curve(start,end,tenor)` | **bundle** `yield_curve.h5`（股票路径不调用） |
| L313 | `get_dividend(instrument)` | **bundle** `dividends.h5` ／母库分红表补窗口（字段契约见 §2） |
| L347 | `get_split(instrument)` | **bundle** `split_factor.h5` |
| L379 | `get_bar(instrument, dt, frequency)` | **我们**（`daily_bars` / 母库 `kline_daily`） |
| L394 | `get_open_auction_bar(instrument, dt)` | ✗ 给不了（无集合竞价数据）→ 退路见 §4-3 |
| L402 | `get_open_auction_volume(instrument, dt)` | ✗ 同上 |
| L416 | `get_settle_price(instrument, date)` | ✗ 期货专用，不做（AGENTS 不做清单） |
| L429 | `history_bars(..., skip_suspended, include_now, adjust_type, adjust_orig)` | **我们** ← 核心，语义见 §3 |
| L479 | `history_ticks(instrument, count, dt)` | ✗ 无 tick（`data/minute.py` 有 5 分钟，可先不给 tick） |
| L491 | `current_snapshot(instrument, frequency, dt)` | **日内/模拟盘必需**：可由 `data/realtime.py`（腾讯实时）供 |
| L509 | `get_trading_minutes_for(instrument, trading_dt)` | ✗ 期货专用 |
| L522 | `available_data_range(frequency)` | **我们**（SQLite min/max；bundle 范围作校验上限） |
| L532 | `get_futures_trading_parameters(...)` | ✗ 期货专用 |
| L544 | `get_merge_ticks(...)` | ✗ tick 专用 |
| L556 | `get_share_transformation(order_book_id)` | **bundle** `share_transformation.json`（转债换股等） |
| L564 | `is_suspended(order_book_id, dates)` | **bundle** `suspended_days.h5` ← **我们完全没有这张表** |
| L567 | `is_st_stock(order_book_id, dates)` | **bundle** `st_stock_days.h5` ← **我们完全没有**（AGENTS #4/#5 的 ST 口径靠它） |
| L570 | `get_algo_bar(id_or_ins, start_min, end_min, dt)` | ✗ 分钟算法专用 |
| L574 | `get_exchange_rate(trading_date, local, settlement)` | ✗ 外币专用 |

**能不给的就 8 个（期货/tick/集合竞价/汇率）**——都是我们不做或 AGENTS 明写不做的；
但必须**在 spike 里确认引擎在股票日线路径不会调它们**（无 abstractmethod ⇒ 缺方法只在被调用时抛）。

**bundle 的读写走 `h5py`，不依赖 PyTables**：
`data/base_data_source/storages.py:29,176,186`、`data/bundle/__init__.py:25,152,181,209`。
（我一开始用 `pandas.read_hdf` 读，报 `Import pytables failed`——那是我的工具选择，
不是 rqalpha 的隐藏依赖。rqalpha `requires_dist` 里也确实只有 `h5py + hdf5plugin`。）

## 2. 分红字段契约（决定钱对不对，逐字段）

`interface.py:313-346` 规定 `get_dividend` 返回结构化数组，字段与语义：

```
book_closure_date        '<i8'  股权登记日 YYYYMMDD
announcement_date        '<i8'  公告日
dividend_cash_before_tax '<f8'  税前现金分红（元）——按 round_lot 计
ex_dividend_date         '<i8'  除权除息日
payable_date             '<i8'  派息日
round_lot                '<f8'  分红最小单位（10 = 每 10 股派 X 元）
```

引擎侧的消费点（**净值就在这里形成**）：
`mod/rqalpha_mod_sys_accounts/position_model.py`
**L58** `dividend_reinvestment=False`、**L59** `dividend_tax_enabled=False`、
**L67** `_dividend_receivable: deque[(date, float)]`、**L72** `_daily_dividend`、
**L76-77** `_historical_dividends: Series[date, dividend_per_share]`、
**L86-90** `dividend_receivable`、**L97** `equity = super().equity + dividend_receivable`、
**L115** 盈亏计算里 `+ self._daily_dividend`。
⇒ **持仓权益显式包含"应收分红"**。这一条是判路线 A 死路的决定性事实。
（两个开关要显式定：`dividend_reinvestment`（分红再投）与 `dividend_tax_enabled`
（红利税，A 股按持股期限差别化 0/10/20%）——**我们的 AGENTS 合规表 #16 目前完全没有红利税模型**，
默认 `False` 是"不建模"，须在文档里如实标注为"简化"，不许声称与实盘一致。）

## 3. 复权语义：rqalpha 不替数据源做复权，是**数据源自己做**

`data/base_data_source/adjust.py:23`（`PRICE_FIELDS` 含 `limit_up/limit_down`）、
`:35-66`：按 `ex_factors['ex_cum_factor']` 相乘得复权价，`adjust_type=='pre'` 时
以 `base_adjust_rate = 因子(adjust_orig)` 归一（`:41-45`），**成交量取倒数**（`:64`）。
`data/data_proxy.py:209,220,240`：`history_bars` 一律带 `adjust_type='pre'` 且 **`adjust_orig=dt`（当日）**。

⇒ 设计含义（对我们的价值）：**策略拿到的历史序列是"相对当日的前复权"，
而当日那根价 = 真实价**。也就是说"指标要连续、成交要真实"这个两难，
rqalpha 用 `adjust_orig=当日` 天然解决了——**前提是我们喂 raw**。

## 4. 除权日推演（真实数据，茅台 600519.XSHG，2026-06-26 除息）

bundle 实取（`stocks.h5` / `dividends.h5` / `ex_cum_factor.h5`，均为真实市场数据）：

```
dividends: (book_closure 20260625, ann 20260622, cash 280.2423, ex 20260626, payable 20260626, round_lot 10)
           ⇒ 每股税前现金分红 DPS = 280.2423 / 10 = 28.02423 元
bars（raw 不复权）:
  2026-06-25  prev 1207.68  open 1207.00  close 1212.10   limit_up 1328.45  limit_down 1086.91
  2026-06-26  prev 1184.08  open 1199.00  close 1168.63   limit_up 1302.49  limit_down 1065.67
ex_cum_factor:  …(20250626 8.5372) → (20251219 8.68256) → (20260626 8.88806)
```

三条可核对的事实：
1. `prev_close(6-26) = 1184.08 = 1212.10 − 28.02` ⇒ **交易所除息参考价已写进 bar 的 prev_close**；
2. `limit_up = 1184.08 × 1.1 = 1302.49`、`limit_down = 1184.08 × 0.9 = 1065.67`
   ⇒ **涨跌停带是相对"除息参考价"算的，不是相对昨日收盘**（主板 10%，AGENTS #4）；
3. 因子 8.68256 → 8.88806 ⇒ 以 2026-09-30 为基准，6-25 那根的前复权价 = `1212.10 × 0.976879 = 1184.07`。

持有 **100 股**（成本按 6-25 收盘 1212.10，市值 121,210 元）跨除息日：

| 路线 | 除息日价格路径 | 现金分红 | 净值变化 | 判定 |
|---|---|---|---|---|
| **B：raw + 因子 + `get_dividend`** | 参考价 1184.08（账面 −2,802.42） | **入账 +2,802.42**（`position_model.py:97`） | **0**（−2,802.42 + 2,802.42） | ✅ 与现实一致：除权不创造盈亏 |
| **A：喂前复权序列 + 因子置中性 + 仍给 dividend** | qfq 序列里这天**没有跳空**（跌幅已被回溯抹平） | 仍入账 +2,802.42 | **虚增 +2,802.42（+2.31%）** | ❌ **同一笔分红计两次**，正是 `L97` 权益含应收分红导致的必然结果 |
| **A2：喂前复权 + 抑制 dividend** | 无跳空 | 不入账 | 0，但**现金永远不到账** | ❌ 分红被"价格化"：与交割单/我们的账本口径不符，且 `nav_history` 与真实可取现金不一致 |
| **A3：喂前复权 + 关掉引擎复权（因子中性）** | 历史价**每次未来除权都被重述**（本次 −2.31%） | — | 同一天历史 K 线在两次同步之间变了 **2.31%** | ❌ 破 M2-2 可复现性（我们的回测指纹会变），也破 AGENTS 3.2"回测吃已存日线快照"口径 |

**A3 的连锁反应要特别提示主 AI**：我们现在用 qfq，`cli.py:_detect_revisions` 的阈值是
`abs(delta)/prev > 0.001`（0.1%），茅台这次重述幅度 2.31% ⇒ **每次除权都会触发一批
`data:revision`**。这证明现状本身就在承受 qfq 重述；改 raw 后这个噪音消失（除权变成引擎内
的一次分红入账事件，而不是历史价重写）。

**并有一条现存隐患**（与新引擎无关，今天就在）：把 qfq 的 `bars[-2].close` 当涨跌停基准
在"数据已全量重述"时是**碰巧正确**的（§4 第 3 条：qfq(6-25)=1184.07≈除息参考价 1184.08）。
**一旦改喂 raw，`bars[-2].close = 1212.10 ≠ 参考价 1184.08，涨跌停带会偏 2.31%**，
卖单可能被误判超跌停或漏判。⇒ 迁移时必须把 `prev_close` 改成**显式的除权参考价口径**
（bundle 已给；自算则需 `昨收 − DPS(税前) ± 送转` 的正确公式）。这条我建议直接进 M1 门禁。

## 5. 难点清单（我们给不了什么 / 退路）

1. **`limit_up` / `limit_down` / `prev_close` 我们的库没有**
   `daily_bars` 现有列：`symbol/timestamp/open/high/low/close/volume/turn`（`data/db_manager.py:44-49`）。
   bar dtype 需要 `('datetime','open','close','high','low','prev_close','limit_up','limit_down','volume','total_turnover')`
   （`model/bar.py:32` NAMES；`base_data_source/storages.py:236` 附近字段表）。
   ⇒ 适配器要**现算**：`prev_close`（含除权参考价）、按板块分档涨跌停
   （6/0→10%、300/688→20%、8/4→30%，AGENTS #4/#5/#6，**含 2026-07-06 起 ST 主板 10% 新规**）、
   `total_turnover` 可由 `turn`/`amount` 供。**风险**：算错即涨跌停失真；建议单元测试锁 3 个板块 × 除权日。
2. **停牌/ST 元数据我们没有**：`is_suspended`/`is_st_stock` 靠 bundle 的 `suspended_days.h5`/
   `st_stock_days.h5`。⇒ 这是 bundle 白送的能力，顺带根治我刚报的 **M-2/M-3**
   （`data/quality.py` 的 `gap`/`stale` 把停牌当缺日、把新股首日当脏数据——
   有了停牌表与 `listed_date`，两类误报都可消失）。**但口径归口后要定死：
   元数据只从 bundle 取，禁止与母库停牌混用。**
3. **无集合竞价**：`get_open_auction_bar/volume` 给不了。退路：撮合用
   `sys_simulation` 的 `current_bar`/`close` 模式，不用 `next_open` 依赖开盘竞价的语义；
   在报告与 `mother-interface.md` 里如实记为"简化"（AGENTS #14 本就是简化项）。
4. **bundle 是月度、我们价格是每日 ⇒ 元数据滞后窗口**（最危险的一条）：
   若某次除息落在 bundle 未覆盖区间，引擎不会入账现金分红
   ⇒ 净值出现 **−2.31% 级别的假跌**（§4 的同一组数字，方向相反）。
   退路：分红/送转用**母库 `dividend` 表 + akshare 分红明细**每日增量补齐，
   并加一条门禁：**"有除权参考价(`prev_close ≠ 昨收`) 但无分红记录"⇒ 当日拒用该票数据并告警**
   （这正是"不静默"的路线图纪律 4）。
5. **无 abstractmethod ⇒ 缺方法在运行期才炸**：M0 适配器 spike 必须**真跑一条跨除权日的策略**，
   不能只 `import` 成功就算过。
6. `current_snapshot`（L491）是**日内模拟盘**的关键，只能由 `data/realtime.py` 供；
   它失败（网络抖动）时的语义必须定死：返回 None ⇒ 引擎跳过该 bar，**不许回退成日线收盘**。
7. **红利税未建模**（§2 的 `dividend_tax_enabled=False`）：A 股差别化税（≤1 月 20%、
   1 月–1 年 10%、>1 年 0%）需要持仓期限 ⇒ 我们 FIFO 期限数据有（`t1_freeze/entry_day`），
   但框架默认关。**要么开、要么在 AGENTS 合规表加一行"简化"**，不许默认不管还声称一致。

## 6. 数据流（路线 B 的落地形状，供主 AI 画 M0b/M1 任务）

```
akshare/母库(不复权 close) ──写入──> data.db.daily_bars(raw)
                                     │
   bundle.instruments.pk ──┐         ├─> 适配器 AbstractDataSource.get_bar/history_bars
   bundle.trading_dates ───┤ 元数据   │      （raw + 自算 prev_close/limit_up/limit_down）
   bundle.suspended/st_days┤ 只从这一 ├─> is_suspended / is_st_stock / get_instruments
   bundle.ex_cum_factor ───┤ 处取     │
   母库 dividend(每日补窗口)┘         └─> get_dividend / get_split（字段契约 §2）
                                             │
                                    rqalpha 撮合/账户/净值（引擎内，纪律 3）
                                             │
                            AI 上下文/面板：仍展示 qfq（由 adjust_type='pre'+当日基准现算）
```

## 7. 我建议进 M1 门禁的新增三条（在归因报告 §6 七条之上）

8. **除权日现金分红必须入账**：回归用例取 §4 的真实数据（600519 2026-06-26，
   DPS 28.02423，参考价 1184.08），断言 100 股持仓在该日**净值变化 = 0**、
   `dividend_receivable = 2,802.42`、且**涨跌停带基准 = 1184.08 而非 1212.10**。
9. **禁止双重计息**：若价格序列来源标为 qfq，则 `get_dividend` 必须返回空且显式登记为
   "价格已含权"模式——两者只能选一个，混用即测试红。
10. **元数据覆盖窗口校验**：每日同步后检查 `dividends/suspended/st` 的最新覆盖日期，
    落后超过 N 天 ⇒ `op_log[meta:stale]` + 拒用该票撮合（不许静默假跌）。

---

### 附：本报告的取证命令（可复现）

```bash
V=/c/Users/yanzh/venvs/refactor-spike
SP=$V/Lib/site-packages/rqalpha
# 21 个方法 + 行号（AST）
$V/Scripts/python.exe -c "import ast;[print(m.lineno, m.name) for n in ast.walk(ast.parse(open(r'$SP/interface.py',encoding='utf-8').read())) if isinstance(n,ast.ClassDef) and n.name=='AbstractDataSource' for m in n.body if isinstance(m,ast.FunctionDef)]"
# 复权由数据源做
sed -n '23p;35,66p' $SP/data/base_data_source/adjust.py
sed -n '209p;220p;240p' $SP/data/data_proxy.py
# 分红入账（净值形成点）
grep -n "dividend\|_daily_dividend" $SP/mod/rqalpha_mod_sys_accounts/position_model.py | head -12
# 真实除息推演数据
$V/Scripts/python.exe - <<'PY'
import os,h5py,numpy as np
b=os.path.expanduser("~/.rqalpha/bundle")
for fn in ("dividends.h5","stocks.h5","ex_cum_factor.h5"):
    with h5py.File(os.path.join(b,fn)) as f: print(fn, f["600519.XSHG"][()][-2:])
PY
```

**待主 AI 决策的两点**（我不擅自动）：
① 价格入库口径改 raw 是否采纳（若采纳，AGENTS 3.2 "优先 `close_qfq`" 需要改写为
"入库不复权、qfq 由框架现算供展示/指标"）；② 红利税与集合竞价两项，
是**补建模**还是在 AGENTS 合规表新增两行"简化"。
