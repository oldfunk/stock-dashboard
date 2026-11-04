# report-t52 · 方案④取证：rqalpha 撮合器能不能逐笔调用（Qoder，P0·执行模型终局）

分支 `worker/t52`（基于 `origin/main@901bee6`，**待 review 未合并**）。
**本任务不改产品码**：证据全部来自 `spikes/matcher_call_t52.py`
（spike venv `C:\Users\yanzh\venvs\refactor-spike`，rqalpha 6.4.2 + `~/.rqalpha/bundle`
+ 本地 raw fixture `qoder_t13/e2e_data.db`，撮合日 2026-10-28）。

⚠️ 与派发的差异，先交代：**派发写"不碰产品码"，我反而用了产品码**——
用 `rq_engine.runner.build_config/run_session` 构造会话，才测得到**切换日真跑的那条路**
（T38 的过户费 overlay、T50 的当日参照闸、T47 的落账语义都在产品里）；
只读调用，未改一行产品码，未跑任何写账本的命令（`ledger=False`，账本零写入）。

⚠️ 主 AI 已在 `9f2021b`（`spikes/intraday_resume_t52.py`）定案"方案①改良 = 终态"并否决④。
**本报告不重开那个决策**，只做两件派发消息里欠着的事：
① 把④的接口分析补齐到文件:行（机器导出，不靠手抄）；② 把①改良在产品路径上实测，
**并如实报出两条切换日之前必须处理的风险**（§4 R1/R2）——这是我这次最大的产出。

## 0. 一句话判定

**方案④不可行**：跨进程没有会话时，连 `Order` 对象都造不出来
（`EnvironmentNotInitialized`，`model/order.py:119 Environment.get_instance()`）；
在有会话的进程里"裸调 matcher"确实能成交，但**T+1 不在了**——
同一天的票被卖成 `closable = -100`（框架自己的账被写坏），
而 api 路径同一天卖会被 validator 挡下："仓位不足…可平仓量为 0"。
⇒ 支持主 AI 的否决，且现在有量化证据。
**方案①改良在产品路径上成立但有前提**：意图幂等实测有效（重复成交 200 股 → 0 新增），
但**续跑进程里过户费 overlay 没装上**（§4-R1），且 1d 频率下**成交价 = 当日收盘价**（§4-R2）。

## 1. 派发逐条核销

| 派发原文 | 状态 | 证据 |
|---|---|---|
| 「接口分析（文件:行）：`SimulationBroker`/`matcher/bar_matcher` 能否脱离事件循环逐笔调用——提交一张单、给定当日 bar、拿回成交/拒单」 | ✅ | §2 依赖清单（AST 机器导出）+ §3 三路径实测：`submit_order` 需要 `env.event_bus`（13 处）/`env.get_account`/`ExecutionContext.phase()`（`simulation_broker.py:118`）；`matcher.match()` 需要 `data_proxy/price_board/trading_dt/calc_transaction_cost/event_bus` 五件 + **当前 bar 必须在场**（`bar_matcher.py:30 env.get_bar`，由 BAR 事件注入） |
| 「哪些语义依赖 `Environment`/事件上下文（T+1 冻结、涨跌停带、费用三分量）」 | ✅ 分工已定位 | **涨跌停带在 matcher 内**（`base.py:207-218`，实测市价单 REJECTED / 限价单留在市场）；**费用三分量在 matcher 内**（`base.py:108-114` + 我们的 overlay）；**T+1 不在 matcher 内而在提交校验链**（`environment.py:207 can_submit_order` → `sys_accounts` validator）⇒ 绕过 api 就丢 T+1，实测见 §3-P5 |
| 「spike 实证：涨停拒单 / T+1 不可卖 / 佣金·印花税·过户费逐笔与手算一致 / 撮合价 = bar 口径」 | ✅ 四项全测 | §3 表格：P6 涨停 ⇒ `REJECTED：已触及涨跌停价 limit_up`；P4/P5 T+1 ⇒ api 挡下 vs 裸调成交并把 closable 打成 −100；费用三分量逐笔手算相符（100 股@22.10：佣金 5.00 撞地板、过户费 0.0221=2210×1e-5；卖出加印花税 1.105=2210×5e-4）；撮合价 22.10 **= 当日 close**（该日 OHLC 22.50/22.51/22.05/22.10，可复算） |
| 「判定三选一」 | ✅ **不可行** | §2 + §3 + §5 的 C 组：跨进程三件事全失败（`Environment.get_instance()`、`Order.__from_create__`、假 env 喂 `matcher.match()` 第一步就 `NotImplementedError`，因为它先读 `order.position_effect`） |
| 「若可行：给出最终架构草图」 | 条件不成立 ⇒ 改交付**①改良的实施前提清单** | §4（R1–R4）+ §6 草图：每分钟跳 = "日 bar 重放 + 意图幂等"，语义要显式写明 |
| 「`report-t52.md`」 | ✅ | 本文件 |

## 2. 依赖清单（机器导出，`part_a`）

`spikes/matcher_call_t52.py::part_a` 用 AST 扫 rqalpha 源码，收集 `self._env.X / env.X` 访问点：

| 文件 | 依赖的 Environment 成员（次数，首个行号） |
|---|---|
| `simulation_broker.py` | `event_bus`×13(:57) · `get_account`×3(:112) · `config`×2(:187) · `data_proxy`(:68) · `get_universe`(:187) |
| `matcher/base.py` | `data_proxy`×2(:195) · `config`×2(:79) · `price_board`(:197) · `trading_dt`(:195) · `calendar_dt`(:81) · `calc_transaction_cost`(:108) · `event_bus`(:183) |
| `matcher/bar_matcher.py` | `get_bar`×4(:43) · `data_proxy`×4(:49) · `trading_dt`×3(:49) · `calendar_dt`×2(:56) · `get_instrument`(:42) |
| `apis/api_base.py` | `data_proxy`×14 · `trading_dt`×8 · `config`×5 · `portfolio`×4 · `calendar_dt`×3 · `event_bus`×2 · `broker`×2 · `get_last_price`(:183) · `can_submit_order`(:201) · `order_creation_failed`(:186) … |

另有 **38 处** `Environment.get_instance()` / `ExecutionContext.phase()` 硬依赖（`singleton_uses` 全清单在 spike 输出里）。

**读法**：撮合一笔要 8 类在场状态（data_proxy、price_board、trading_dt/calendar_dt、config、
portfolio/account、event_bus、当前 bar、broker 的 open_orders 记账）。
`event_bus` 不是可选项：`portfolio/__init__.py:60` 的 `add_listener(EVENT.TRADE, self._on_trade)`
就是**持仓/现金更新的唯一入口**——不发事件，成交就不进账；发了事件，就得有 Portfolio。
⇒ "逐笔调用"与"起一个会话"的差别只是**跳几块 bootstrap**，不是省下一套语义。

## 3. 会话内三路径实测（`part_b`，同一会话同一时点）

| 探针 | 走的路 | 结果 | 拿到的语义 |
|---|---|---|---|
| P1 | `api.order_shares(000792, 100)` | FILLED 100 @22.10，现金 97784.98 | 全：validator + matcher + 事件 + broker 记账 |
| P2 | `Order.__from_create__` + `env.can_submit_order` + `broker.submit_order`（跳过 api 层） | FILLED 100 @37.30，`passed_validators=true` | 几乎全（api 层的 `get_last_price` 有无行情检查被跳过） |
| P3 | 裸 `matcher.match(acc, order, False)`（跳过 validator + ORDER_* 事件 + open_orders） | FILLED 100 @72.40，费用三分量齐（5.00 / 0 / 0.0724） | **撮合语义在，前置校验没了** |
| P4 | api 当天买后立即卖 000792 | **不创建订单**：`ORDER_CREATION_REJECT：仓位不足…可平仓量为 0` | **T+1 由 validator 守** |
| P5 | 裸 matcher 卖同一天的 000792 | **FILLED 100**，佣金 5.00 + 印花税 1.105 + 过户 0.0221，`positions: total 0 / closable −100` | 成交发生、费用正确、**T+1 丢失且把框架的账写坏** |
| P6 | 把 `price_board.get_limit_up` 钉到当收（42.13）后 api 买 600036 | `REJECTED：已触及涨跌停价 limit_up` | 涨跌停在 matcher 内（限价单则留单不成交，`base.py:207-209`） |

**P5 是④否决的硬证据**：费用与价格全都"看起来正确"，唯独可卖量被穿透成负数——
这种账本状态旧线不会出现（`apply_external_fill` 有守卫），rqalpha 自己也不会承认它。

## 4. ①改良在产品路径上的实测（`part_d`）与四条必须处理的风险

三连跳（共享同一 `state_dir`，d2/d3 用 spike 里的放宽闸补丁模拟①改良，不改产品闸）：

| 跳 | 计划 | 成交 | 持仓/现金 | 门禁 | `cost` overlay |
|---|---|---|---|---|---|
| d1 首跑 | 买 000792 100 | 1 笔 @22.10，过户费 **0.0221** | 100 / closable 0，cash 97784.98 | ✅ ok，marked_done | `wrapped=["CS"]` applied 4 |
| d2 续跑（**不**过滤意图） | 同上 + 买 000651 5000 | **重复成交 000792 100**，过户费 **0.0** | 200 / closable 0，cash 95569.98 | ❌ `TransferFeeNotInstalled` | `wrapped=[] deciders=[]` |
| d3 续跑（过滤意图） | 过滤后只剩 000651（当日无 bar ⇒ 框架拒单） | **零新增** | 200 / closable 0，cash 不变 95569.98 | ❌ 同上 | 同上 |

- **R1（钱类，切换日前必须修）：续跑进程不装过户费 overlay。**
  `mod_entry._install_costs` 监听 `EVENT.POST_BEFORE_TRADING`，而 resume 恰好跳过该事件
  （d2/d3 的 `event_counts` 里 **BEFORE_TRADING 缺失**，与 T13 §4.7 一致）⇒ 每跳新进程
  都不装 overlay，成交的 `other_fees=0.0`（d1 是 0.0221）。
  好消息是**不会静默**：`check_session_gates` 的 `TransferFeeNotInstalled` 每跳都红（exit≠0）。
  修复方向（属实施任务，建议另派）：把 overlay 安装点改成"每个进程一定执行"
  （mod `start()` 里直接装 + 保持 `isinstance` 幂等），并给 `m3 verify` 加一项
  "续跑跳的成交过户费非零/overlay 已装"。
- **R2（口径，切换日之前要主 AI 显式定性）：1d 频率下成交价 = 当日收盘价。**
  `matching_type=current_bar` → `MATCHING_TYPE.CURRENT_BAR_CLOSE` →
  `bar_matcher.py:28-32 return env.get_bar(obid).close`。
  实测三跳的成交价都是 **22.10**，而该日 OHLC 是 22.50/22.51/22.05/22.10。
  ⇒ "每分钟一跳"在 1d 频率上的真语义是**日 bar 重放**：10:00 的止损决定按 15:00 才知道的
  价格成交（未来函数），与旧线"盘中实时报价撮合"不是同一个口径。
  这不是实现瑕疵，是**频率选择**：要盘中价成交就得有分钟线（正是②被卡住的前置）。
  第一期若沿用①改良，需要二选一：(a) 显式声明"新线只做日频决策"（那盘中止损语义
  就变了——与主 AI 否决"两轮"的理由相冲，请把这条放回决策桌）；(b) 成交口径改为
  `NEXT_BAR_OPEN`/分钟线（都超出第一期）；(c) 第一期把新线定位为"计划落账 + 日终结算"，
  盘中执行仍走旧线（=T48 的③，不满足 M3"成交真相源"）。我不拍，这条必须你定。
- **R3（待取证）：resume 每跳都重放 SETTLEMENT。**
  d2/d3 `event_counts` 各含 `SETTLEMENT:1`，同 T13 §4.4；本次实测 `closable` 仍是 0
  （没有提前解冻），但**跨日第一跳**（明天早上第一跳带着今天的 SETTLEMENT 状态）
  T+1 解冻发生在哪一步、会不会解两次，我没测——切换日前应补一条取证。
- **R4：意图幂等有效。** d2 无过滤 ⇒ 重复成交 100 股（净买翻倍）；d3 过滤 ⇒ 零新增。
  注意真实实现里"已执行清单"要从 **T47 G1 的 orders 表**取，键归一是坑：
  plan 用 6 位代码、框架成交用 `000792.XSHE`，我第一版就因此过滤失效（0792 又买了一遍），
  修成 `symbol[:6]` 后 d3 才干净。这条写进实施说明，别让第二个实现者再踩。

## 5. 跨进程无会话（`part_c`，fresh process）

```
Environment.get_instance()  → EnvironmentNotInitialized（"Environment 并没有被创建…"）
Order.__from_create__(…)    → EnvironmentNotInitialized（model/order.py:119）
DefaultBarMatcher(stub).match(...) → NotImplementedError（第一步就读 order.position_effect）
```
⇒ ④这句话在物理上不含混 space：要在进程里撮合一笔，先得把rqalpha 的 Environment
（mod 装载、data_proxy、price_board、persist、Portfolio）拉起来——**拉起它的代价
就等于跑一个会话**，而会话里已经带着 T+1/涨跌停/费用的完整校验链。

## 6. 架构草图（①改良若要落地，第一期最小形态；**非本任务实施**）

```
每分钟 cron（一次一跳，一天 ≤240 跳）
 ├─ 读当日 orders（T47 G1 落账表）→ 已执行 biz_id 集合        ← R4 幂等
 ├─ 计划增量：只提交"未执行意图"给会话（api 路径，不裸调）  ← §3 结论
 ├─ SessionGate 放宽：同日 + 有 state + 指纹一致 ⇒ 续跑；同日无 state ⇒ 仍拒
 ├─ mod start 立即装过户费 overlay（R1），不再依赖 POST_BEFORE_TRADING
 ├─ 成交落账走 T47 apply_external_fill（守卫 + 三分量 + op_log[rq:fill]）
 └─ 会话后置门禁 + m3 verify（新增：overlay 已装、续跑跳费用非零）
成交口径 = 当日 bar close（R2）⇒ 报告与面板必须注明，避免与旧线盘中价直接对比
```

## 7. 我没做/做不了的事（如实）

- 没改产品码，因此 R1 的修复、SessionGate 放宽、orders 表幂等查询**都还没实现**——
  它们需要主 AI 先答 R2（口径定性），否则实现方向可能是错的。
- R3 的跨日解冻取证没做（需要两个不同日期的连续会话，本 spike 只测同日）。
- fixture 的 000651 数据止于 2026-10-23，所以 2026-10-28 它那笔被框架以"没有市场数据"拒单
  （这恰好是 T50 那条防线的正向证据，不是 fixture 缺陷导致的假红）。
