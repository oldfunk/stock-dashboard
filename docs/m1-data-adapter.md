# M1 数据适配与口径设计（T7 接管：主 AI，2026-10-27）

> 背景：员工复工交接，T7 由主 AI 接管实施。本文档回答 T7 的三个核心问题：
> rqalpha 数据接口长什么样、我们的口径怎么归口、"数据完整"如何证明。
> M1-A 实证：`spikes/rqalpha_ma_backtest.py`（门禁通过，见 §3）。

## 1. rqalpha 6.x 数据架构（源码取证，spike venv）

**组合注册模型**（不是单体抽象类）——`BaseDataSource` 注册一组 store：

| Store 接口 | 文件 | 数据 | 我们的来源（M2 实时线） |
|---|---|---|---|
| `AbstractDayBarStore`（日线 OHLCV） | `rqalpha/data/base_data_source/{storages,storage_interface}.py` | 原始价（**不复权**） | 母库 `close_raw` / akshare `adjust=""`；M1 回测用 bundle `stocks.h5` |
| `AbstractCalendarStore`（交易日历） | 同上 | 日历 | bundle `trading_dates.npy`（→2027-12-31，完整） |
| `AbstractDividendStore`（现金分红） | 同上 | 分红事件 | bundle `dividends.h5` |
| `AbstractSimpleFactorStore`（拆股/复权因子） | 同上 | split / ex_cum_factor | bundle `split_factor.h5` / `ex_cum_factor.h5` |
| `AbstractDateSet`（停牌） | 同上 | 停牌日集合 | bundle `suspended_days.h5` |
| ST 标记 / `Instrument` 元数据 | `instruments_mixin` 等 | ST/合约 | bundle `st_stock_days.h5` / `instruments.pk` |

**两个决定口径的消费点（源码实据）**：
1. `base_data_source/adjust.py::adjust_bars`——**读 bar 时按 `ex_cum_factor`
   自动复权**：store 里存原始价，策略看到的是复权价（`adjust_type` 可配）。
2. `mod/rqalpha_mod_sys_accounts/position_model.py:234`——**现金分红按
   dividend store 事件入账**（`_handle_dividend_book_closure`），拆股同理。

⇒ **rqalpha 的会计契约 = 原始价 + 因子 + 分红事件三件套同时给，它自己算对。**

## 2. 口径归口定案（T7 核心裁决）

| 线 | 价格来源 | 理由 |
|---|---|---|
| **回测（M1）** | **bundle 自洽三件套**（raw+因子+分红），不混我们两源 | 同一 store 内自洽 = 零尺度污染面；rqalpha 会计按其契约工作。**我们两源作为 parity 对照组**（同规则同窗口比对），不当喂料 |
| **实时/模拟（M2）** | **原始价**（母库 `close_raw` 优先、akshare `adjust=""` 兜底）→ 自定义 DayBarStore，复权视图交 rqalpha `adjust` | **D3 裁决（2026-10-28 修正，依据 T7 取证 route A 双重计入 2,802.42 元）**：引擎全线 route B——实盘撮合本来就是原始价，qfq 只留 AI 展示层。~~原写"喂 close_qfq"作废~~。入口过 R1.3 尺度闸 + NaN 门禁 |
| **AI 上下文（L2）** | 不变（`llm_context.recent_closes` 走我们两源 qfq，仅展示/论据） | 用户路线：数据供给是我们自己的层；qfq 不进引擎 |

- **除权日 walkthrough**（T7 验收项）：路线 B 下 ex-date 由 `adjust_bars` 调价 +
  `position_model` 分红入账，数学自洽——真实分红案例推演列入 **M1-C** 执行
  （在 parity 阶段用 bundle 里一只真实分红票走一遍）。
- 现网"qfq + 人工 `corporate apply`"语义保留为 **对照基线**，不喂引擎。

## 3. M1-A 实证：新项目在完整数据上跑通

`spikes/rqalpha_ma_backtest.py`（rqalpha 6.4.2 + 原生 bundle，零自定义数据）：

```
窗口 2026-01-05 → 2026-09-10｜10 只真实 A 股｜MA5/20 金叉死叉｜100 股整手
starting_cash 100,000 → total_value 94,453.10｜年化 -8.20%｜sharpe -2.16
max_drawdown 7.45%｜alpha -0.0946｜beta 0.0167（基准 000300.XSHG）
M1-A GATE PASSED: trades=108, summary 全有限值（EXIT=0）
```

两道门禁均实弹验证过：① NaN 门禁先在"无基准"配置下**正确开火**（alpha/beta=nan
→ 作废），配基准后放行；② no-trade 门禁键名修正后通过（trades=108）。

## 4. 数据完整性清单（用户红线"不能缺任何数据"）

| 数据项 | 来源 | 覆盖 | 状态 |
|---|---|---|---|
| 日线原始价 OHLCV | bundle `stocks.h5` | 全市场 | ✅ |
| 复权因子/拆股 | `ex_cum_factor.h5` / `split_factor.h5` | 全市场 | ✅ |
| 现金分红 | `dividends.h5` | 全市场 | ✅ |
| 停牌日 | `suspended_days.h5` | 全市场 | ✅ |
| ST 标记 | `st_stock_days.h5` | 全市场 | ✅ |
| 交易日历 | `trading_dates.npy` | →2027-12-31 | ✅ |
| 合约元数据 | `instruments.pk` | 全市场 | ✅ |
| 基准指数 | indexes + `mod.sys_analyser.benchmark` | 沪深300 | ✅ |
| 我们两源新鲜度（M2） | 母库 `close_qfq`/akshare + 尺度闸 | 池+持仓 | M2 接入 |

## 5. rqalpha 6.x 实战踩坑笔记（复现者必读）

1. **`accounts` 键必须 `"STOCK"`**：`parse_accounts` 只做 `upper()`，
   写老文档的 `CNStock` → `CNSTOCK` → `ACCOUNT_FIELDS_MAP` KeyError。
2. **基准在 `mod.sys_analyser.benchmark`**：`base.benchmark` 已弃用（有警告）。
3. **`get_position(ob, POSITION_DIRECTION.LONG)`**：6.x 必须带方向参数；
   `portfolio.positions` 已弃用（有警告）。
4. **`capital_gain_tax_rate` 显式设 0**（6.x 会警告要求配置）。
5. **NaN 的两类成因要区分**：无基准（配基准即可）vs 窗口越界/配置错
   （Qoder report-t5 §10.3：rqalpha 只吐 NaN 不抛错）——门禁先看配置再作废，
   否则会把"配置问题"误判成"数据问题"。

## 6. 下一步

- [ ] **M1-B parity**：同规则同窗口，旧 `Backtester` vs rqalpha 对照
  （成交数/费用/收益容差按 `refactor-plan` §5.1-6 钱类回归执行）
- [ ] **M1-C 除权日 walkthrough**：bundle 真实分红案例推演（T7 验收闭环）
- [ ] **M2 模拟线**：自定义 DayBarStore 接我们两源（新鲜 bar + 尺度闸 + NaN 门禁）
