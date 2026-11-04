# report-t51 · 费率三真相源收敛（Qoder，P1·纪律 7 口径唯一）

分支 `worker/t51`（堆叠在 `worker/t47` ⇒ `worker/t48` ⇒ `worker/t50`，**待 review 未合并**）。
全套 **586 passed / 9 skipped / 1 failed**（基线 `worker/t47` 同时刻 **567/9/1** ⇒ 净增 19 条，零回归）。
⚠️ 那 1 条红**不是本任务引入的**，且在干净的 `worker/t47` 上同样复现：
`tests/test_mother_contract.py::test_daily_bars_mother_first_then_local` ——
夹具写绝对日期 `2026-10-26`、判定用墙钟，机器过午夜到 10-30 就翻红
（机制与影响面见 `questions.md` 的 2026-10-30 条目；我没动它，等派不派）。
`spikes/mutation_t51.py` 十个"回退成旧写法"的变异逐个被杀（§5）；
`spikes/e2e_t51.py` 是端到端证据（纯临时目录，**没碰现网任何库、没改 `config.yaml`**）。

## 0. 一句话结论

费率数字现在只有一份定义（`models/types.py` 的 `TradingConfig`）、费用公式只有一份定义
（`utils/fees.py` 的 `buy_cost/sell_cost`）、config→对象的构造只有一处
（`utils/config.py` 的 `trading_config_from_dicts`，里面**一个数字都不写**）；
反射测试用 AST 扫全树，把"又抄一遍"的四种写法变成 CI 红。

**并且修掉一个比"可能不跟着变"更严重的事实**（§1）：派发原文说"改 config 费率
回测/面板**可能**不跟着变"——实测是**一定不跟着变**，因为那三处读的是一个不存在的键。

## 1. 撞破的事实：三处"各自 new"一直在吃兜底数字（可复算）

`utils/config.py` 的 `load_config()` 返回的键是
`['agent', 'raw', 'risk', 'stock_pool', 'strategy', 'trading_config']`——**没有 `trading`**。
而这三处全都这么写（T43 地图点名的 `cli.py` 两处 + `dashboard.py:906`）：

```python
full_cfg = load_config(args.config)
trading = full_cfg.get("trading") or {}            # ← 恒为 {}
fee = FeeModel(commission_rate=float(trading.get("commission_rate", 0.00025)), ...)
```

⇒ `config.yaml` 的 `trading:` 段对**回测、参数网格、walk-forward、面板优化**完全无效，
四处硬编码兜底值就是实际计算用的费率，且不报错、不留痕。T34 的
`test_caliber_consistency.py` 之所以一直绿，是因为它比的是"代码默认 vs config 值"，
而这两者恰好数值相同——**它锁的是相等，不是唯一**；相等掩盖了"根本没读到"。

端到端证据（`python spikes/e2e_t51.py`，同一份规则/同一笔成交，只换 config 费率档）：

```
load_config 返回的键：['agent','raw','risk','stock_pool','strategy','trading_config']
  其中 'trading' 键存在吗：False   ← 修前三处读的就是这个不存在的键
配置 A（万 2.5 / 地板 5 元）：total_fees = 5.08   成交 1 笔
配置 B（万 10  / 地板 50 元）：total_fees = 50.23  成交 1 笔
✅ 5.08 → 50.23（×9.89），地板 5→50 元也进了结果
✅ 面板 _fee_of 同样跟着 config 变（修前恒为兜底值）
```

⚠️ **给主 AI 的连带影响**：既往任何用过 `engine backtest / optimize / walkforward`
或面板网格的数字，费用口径都是"万 2.5 + 地板 5 元 + 千 0.5 + 0.001%"这套硬编码。
今天 `config.yaml` 写的正是同一套值 ⇒ **历史结论数值不变、仍可比**（`test_repo_config_yaml_agrees_with_code_default`
把"config 与代码默认无分叉"钉住了）；但这个修复让"改 config"第一次真的生效，
以后改档就要注明口径变更日。

## 2. 收敛后的形状（谁引用谁）

```
config.yaml  trading: 段
      ↓ 唯一构造点：utils/config.py:trading_config_from_dicts（不含任何数字，未知键 warning）
models/types.py:TradingConfig      ← 费率**数值**的唯一来源（含默认值）
      ↓ utils/fees.py:fee_dict / as_fee_dict（dict 或对象都收，缺项即 ValueError）
   ┌──────────────┬────────────────┬─────────────────┬──────────────────┐
   │ PaperBroker  │ engine/backtest│ rq_engine/      │ utils/m3_verify  │
   │ _calc_*  →───┴─ fees.buy_cost/─┴─ sell_cost ────┴──（同一把尺子）  │
   │ (旧线记账)   │ FeeModel       │ runner.build_config / shadow_day  │ 重算校验
   └──────────────┴────────────────┴─────────────────┴──────────────────┘
                    rq_engine/costs.py：只剩框架基准率（RQ_*=除数），现网率必须外部传入
```

关键点：`m3_verify.check_fees` 与 broker 记账现在调**同一个函数**——
这条是有代价的（独立基线 vs 口径唯一），写在 §6-Q1 请主 AI 拍板，不是我擅自定的。

## 3. 反射测试怎么"锁死"（`tests/test_fee_single_source_t51.py`，17 条）

1. `test_fee_rates_are_defined_exactly_once_in_the_tree`：AST 扫 `paper_trading/**/*.py`
   全树（`models/types.py` 除外），抓四种写法——
   ① 赋值 `COMMISSION_MIN = 5.0`（**大小写都抓**，costs.py 原来就是大写那种）
   ② 形参默认 `def f(commission_rate=0.00025)`
   ③ 关键字调用 `FeeModel(commission_rate=0.00025)`
   ④ 兜底读法 `d.get("stamp_duty_rate", 0.0005)`
2. `test_scanner_actually_bites`：**正控制**——临时植入四种写法各一遍，扫描器必须报 3+1 条。
   没有正控制的反射测试是一条永远绿的空断言（T43"静态启发式只给候选"的教训反用：
   这次要证明它不是候选而是判定）。
3. `test_fee_formula_is_defined_exactly_once`：扫 `max(...commission_rate...commission_min...)`
   出现在 `utils/fees.py` 之外 ⇒ 红；同样带正控制。
4. `test_costs_module_owns_no_production_rate`：`hasattr(costs, "COMMISSION_RATE")` 必须为假，
   `RQ_STOCK_COMMISSION_RATE` 仍在（它是除数不是现网率）。
5. 行为面：yaml 写了就用 yaml、没写就用数据类；未知键 warning 不静默；
   **回测 + 面板跟着 config 变**（§1 的那条）；broker/FeeModel/fees 三路逐金额对照
   （4 个量级参数化）；`m3 verify` 按 config 费率写的成交判绿、改成另一档算的判红。

## 4. 破坏性改动清单（API 变紧了，以及我为什么动别人的测试）

| 位置 | 旧 | 新 | 为什么必须变 |
|---|---|---|---|
| `costs.stock_cost_mod_config` | 三项费率自带默认 | 三项**必填**（`TypeError`） | 兜底数字 = 第二真相源 |
| `costs.install_transfer_fee(env, rate)` | `rate=TRANSFER_FEE_RATE` | `rate` 必填 | 同上 |
| `costs.transfer_fee_amount(amount, rate)` | `rate=TRANSFER_FEE_RATE` | `rate` 必填 | 同上 |
| `runner.build_config/run_session(fee=…)` | 不传 ⇒ 用 `costs` 抄来的默认 | 不传/缺项 ⇒ `ValueError`（`as_fee_dict`） | 影子/引擎会话不许"半份费率混默认" |
| `shadow.shadow_day(fee=…)` | 不传 ⇒ `TradingConfig()` 默认 | 必须显式给（`TradingConfig` 本体或四项齐备的 dict） | 两侧同尺子才是对账 |
| `engine/backtest.FeeModel` | 四个数字写在这里 | 字段默认**引用** `TradingConfig`；新增 `from_trading_config()` | 数值唯一 |
| `utils/config.load_config` | 八处 `.get(key, <数字>)` | `trading_config_from_dicts()`：零数字 + 未知键 warning | 兜底唯一 + "改了没生效"必须响 |
| `cli.py` `rq day`/`rq shadow-day` | 手搓四项 dict | 直接交 `bridge.broker.config` | 手搓过一次就把键名搓丢了 |
| `cli.py` `engine backtest`/`optimize`/`walkforward`、`dashboard._fee_of` | `load_config(...)["trading"]`（不存在的键） | `load_config(...)["trading_config"]` | §1 的修复本体 |

**改动的既有测试**（4 个文件，逐条都是"断言所守护的契约换了写法"，不是把红改绿）：
- `test_fees_and_oplog_t38.py`：`_fee()` helper 从 `TradingConfig` 取现网率；
  新增 `test_stock_cost_mod_config_requires_every_rate`、
  `test_build_config_refuses_missing_or_partial_fee`（旧测试验的是"不传也有默认"，
  新契约是"不传必报错"——这两条把新契约接住而不是绕开）；
  `test_transfer_fee_amount_matches_legacy_formula` 改成与 `utils.fees` 交叉核对。
- `test_postclose_defense_t50.py::test_costs_module_still_untouched_by_t50`：护栏本意
  "别把 T38 费率带偏"，改成"倍率仍 = 现网率/框架率，且数字来自 `TradingConfig`、
  `costs` 不再自持常量"（`not hasattr(costs, "TRANSFER_FEE_RATE")`）。
- `test_rqalpha_adapter.py::_cfg`、`test_ledger_gaps_t47.py` 的指纹测试：补 `fee=`。

## 5. 变异验证（`python spikes/mutation_t51.py`）

```
① FeeModel 自带费率数字        → 1 failed   ② costs.py 重长出现网费率常量   → 2 failed
③ broker 内联佣金公式           → 1 failed   ④ m3 verify 内联佣金公式        → 1 failed
⑤ 面板忽略 config 费率          → 1 failed   ⑥ 缺项费率被 0 补齐             → 3 failed
⑦ fee 缺省重新兜底              → 3 failed   ⑧ yaml 费率被忽略               → 2 failed
⑨ 加载器重长出费率数字           → 1 failed   ⑩ 未知 trading 键不吭声         → 1 failed
还原后 → 68 passed, 5 skipped（四个测试文件基线）
```

## 6. 请主 AI 拍板的三件（同步进 questions.md）

- **Q1 口径唯一 vs 独立基线**：`m3 verify` 的费用项原本**独立重写**一遍公式（与 broker
  不同实现），T51 后它与记账共用 `utils/fees.py` ⇒ 它仍能查出"库里记的费用与成交价量不符"
  （比如引擎算错、双活、手工改过），但**查不出"公式本身错"**（比如地板该不该参与最低佣金
  这类规则性错误）。这是两条纪律的真冲突，不是我的疏漏。我的倾向：口径唯一赢——
  公式的正确性交给外部基线（券商交割单口径），仓库内两份实现只会让"红"变成不可解释的漂移。
  **实测现状**：手算期望值只散在 `test_fees_and_oplog_t38.py` 两处
  （`cost.commission == approx(100000 * 0.00025)`、过户费 `== approx(1.0)`），
  `test_compliance_regression.py` 里**一条费用断言都没有**（我原本以为有，grep 全文件零命中）；
  `test_backtest_cash.py`/`test_m1_correctness.py` 只做关系式核对（现金变化 = 额 ± 费）。
  ⇒ 要不要我补一条**独立锚点表**（几组金额 → 佣金/印花/过户费的期望硬值，
  按 AGENTS 合规表与券商口径手算）？它能在"公式本身错"时响，也不违反口径唯一。
- **Q2 匿名数字扫不到**：反射测试只认"绑在费率键名上的数字"与"含两个费率键的 max()"。
  谁写 `amount * 0.00025`（不带键名）扫不出来。目前全树扫下来没有这种写法，
  但要不要把**值本身**（0.00025/0.0005/1e-05/5.0）也列进禁区？代价是
  `RQ_STOCK_TAX_RATE = 0.0005` 这类合法的框架基准会一起挨枪，需要白名单机制。
- **Q3 历史回测数字的标注**：这次让"改 config"第一次真正生效。是否在主 AI 侧记一笔
  "自 worker/t51 起回测/网格费用口径 = config.yaml"，避免以后有人拿旧报告数字对比
  新跑结果时误判成漂移？（今天数值相同，所以不影响既有结论，但机制变了。）

## 7. 交付物清单

| 文件 | 作用 |
|---|---|
| `paper_trading/utils/fees.py`（新） | `FEE_KEYS`、`fee_dict`、`as_fee_dict`（缺项即报错）、**唯一费用公式** `buy_cost/sell_cost` |
| `paper_trading/utils/config.py` | `trading_config_from_dicts()`：唯一构造点、零数字、未知键 warning |
| `paper_trading/engine/backtest.py` | `FeeModel` 数值引用 `TradingConfig` + `from_trading_config()` + 公式外提 |
| `paper_trading/broker/paper_broker.py` | `_calc_buy_cost/_calc_sell_cost` 改调 `utils.fees` |
| `paper_trading/utils/m3_verify.py` | `check_fees` 改调 `utils.fees`（见 Q1） |
| `paper_trading/integration/rq_engine/costs.py` | 删掉四个现网费率常量；三个函数费率必填 |
| `paper_trading/integration/rq_engine/runner.py` | `build_config/run_session` 用 `as_fee_dict`（缺项 ValueError） |
| `paper_trading/integration/rq_engine/shadow.py` | `shadow_day` 先归一费率再喂两侧 |
| `paper_trading/cli.py` | 三处 new + 两处手搓 dict 全部改为 config 驱动 |
| `paper_trading/dashboard.py` | `_fee_of` 改为 `FeeModel.from_trading_config(load_config(...)["trading_config"])` |
| `tests/test_fee_single_source_t51.py`（新） | 17 条：反射扫描 ×3（含正控制）+ 行为 ×14 |
| `spikes/e2e_t51.py`、`spikes/mutation_t51.py`（新） | §1 端到端证据、§5 十个变异 |
| `tests/test_fees_and_oplog_t38.py` 等 4 个既有测试 | 契约更新（§4 末表，逐条给了理由） |
