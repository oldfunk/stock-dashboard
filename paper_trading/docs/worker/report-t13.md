# report-t13 — rqalpha 模拟盘运行模式取证 + 原型（M2 关键路径）

执行人：员工 AI（Qoder）｜2026-10-28｜分支 `worker/t13`（**不合并，待 review**）
harness：`spikes/live_sim_t13.py` + `spikes/rqalpha_mod_t13sim.py`
（rqalpha 6.4.2 @ `C:\Users\yanzh\venvs\refactor-spike` Py 3.13 + bundle 202610）
数据：**生产库真实日线**——NEC `~/paper-trading/data.db` 的 `daily_bars` 只读导出
（27,929 行 / 38 标的 / 2023-10-09→2026-10-28），落成本地 fixture 后喂进引擎。
**未执行任何写库命令**（无 agent run/tick/fire、无 buy/sell、无面板 POST）。

> **一句话结论**：**跑得通**——用我们 SQLite 的"今日"驱动 rqalpha 模拟线，
> 下单→成交→日终结算→T+1 冻结→**跨进程解冻续跑**全链路实测通过，
> 21 项门禁全绿（§3）。但 M2 上生产前必须先解决 **3 个 P0**：
> **① 我们的 `daily_bars` 是前复权价，不是 rqalpha 契约要的原始价**（84.3% 的
> 标量-日与真实原始价不符，最坏低估 67.6%）；**② rqalpha 6.4.2 core 里没有
> persist provider 实现**，断点续跑必须我们自带；**③ 复权因子/新鲜度无法自证**
> （bundle 之后 15 个交易日的原始价只能"外推"）。
> 另有 5 条 P1 是**常驻线特有的坑**（重复结算、同日重跑重复成交、拒单原因不落
> Order、涨跌停带缺数据时 fail-open、进程内重入日志炸）。

---

## 1. 取证：持续运行一天模拟到底需要什么（全部 `文件:行`）

rqalpha 的"模拟盘"不是另一套引擎，而是**同一个回测内核 + 一日窗口 + 态存储**。
下表是这条路径上的每一个铰链点（venv 相对路径 `rqalpha/`）。

| 环节 | 出处 | 事实 | 对常驻线的含义 |
|---|---|---|---|
| 运行类型 | `utils/config.py:290-297`、`cmds/run.py:39` | `b/p/r` → BACKTEST / **PAPER_TRADING** / LIVE_TRADING | `p` 就是"模拟盘"，但它**不换内核** |
| 谁注册撮合器/事件源 | `mod/rqalpha_mod_sys_simulation/mod.py:35-36,66-74` | `run_type==LIVE_TRADING` 时 **早退**（不注册 broker/event_source）；其余（含 `p`）注册 `SimulationBroker` + `SimulationEventSource` | 模拟线走 `p` 或 `b` 都一样；**`r` 需要外部网关 mod，core 没有** |
| `p` 与 `b` 的真实差别 | `model/bar.py:211`、`apis/api_base.py:173,883`、`portfolio/__init__.py:294` | 只有 4 处分支，且都是期货/退市语义 | **PAPER_TRADING 不是新运行模式**，别指望它有常驻语义 |
| 事件生成 | `mod/.../simulation_event_source.py:90-104` | 按 `data_proxy.get_trading_dates(start,end)` 逐日产出 `BEFORE_TRADING(00:00)→OPEN_AUCTION→BAR(15:00)→AFTER_TRADING(15:30)` | **窗口就是"今天"**：start=end=今日 ⇒ 恰好一个交易日 |
| 驱动循环 | `core/executor.py:37-62` | `for event in event_source.events(...)`，**生成器耗尽即结束**；结束时若 `trading_dt==end_date` 补发 `SETTLEMENT` | "持续运行一天" = 跑完一个生成器 = **进程退出**；跨日靠重启进程 |
| 日终结算触发 | `core/executor.py:64-82` | `_ensure_before_trading`：跨入新交易日前先**把上一交易日 rewind 出来补发一次 SETTLEMENT** | ⚠️ 跨进程续跑会**重复结算**（§4.4，实测 2 次） |
| T+1 冻结 | `mod/rqalpha_mod_sys_accounts/position_model.py:176-177`（买入累加 `_non_closable`）、`:118-123`（`closable = quantity - 挂单 - non_closable`） | 冻结量记在持仓对象上，随状态一起持久化 | 撮合层无需我们插手，**T+1 判定在框架内** |
| T+1 解冻 | `portfolio/position.py:244-248`（`before_trading()` 里 `_non_closable = 0`） | 解冻发生在 **BEFORE_TRADING**，不是 settlement | 续跑必须让新的一天**真的触发一次 before_trading**（实测成立，§3 S4） |
| 结算动作 | `portfolio/account.py:123-124` + `position_model.py:182-226`、`portfolio/__init__.py:62` | `SETTLEMENT` → 逐仓 `settlement()`（退市/换代码/管理费）+ 资本利得税 | 分红/除权在 **before_trading**（`position_model.py:160-171`），不在结算里 |
| 涨跌停判定 | `mod/.../matcher/base.py:207-216` + `utils/price_limits.py:13-36` | `price_limit=True`（`sys_simulation/__init__.py:29`）时撮合前判 reach limit | 判定在框架 ✅ |
| **涨跌停价的来源** | `data/bar_dict_price_board.py:33-40` → `get_bar(...).limit_up`；`data/bundle/utils.py:13`（STOCK_FIELDS 含 limit_up/limit_down） | 框架**从不计算** band——它是 **bar 的数据字段** | ⚠️ 我们的数据必须供 `limit_up/limit_down/prev_close`；供不上就 **fail-open**（§4.5 实测） |
| 费用 | `mod/rqalpha_mod_sys_transaction_cost/`（默认佣金万 8 地板 5、印花税 0.05% 卖出单边、**无过户费**） | 与 AGENTS 合规表差一条过户费 | T29 已量化（差值恰为 0.001%），M2 配 `stock_commission_multiplier` |
| 数据热更新 | `data/base_data_source/data_source.py:143-144`（`register_day_bar_store`）、`environment.py:95-96`（`set_data_source`）、`main.py:150-151`（mod 先 start_up，之后才默认 BaseDataSource） | **官方扩展点**：mod 里换 store / 换 data_source 即可，不必 fork 框架 | 本原型就是这么接的（§2） |
| "今日"能不能超出 bundle | `data/base_data_source/data_source.py:406-414` | `available_data_range` **只问 INDX store** 的 `000001.XSHG` 区间；`main.py:52-64` 用它夹窗口 | bundle 过期时"今日"进不来（实测大声报错，§4.6）→ **适配层必须覆盖** |
| 断点续跑：注册了哪些对象 | `main.py:74-95` | `user_context / global_vars / universe / portfolio / executor` + 凡 `Persistable` 的 `event_source / broker / mod` | 账本、挂单、`_non_closable` 全在里面 ✅ |
| 断点续跑：何时落盘 | `utils/persisit_helper.py:28-34` | `REAL_TIME` 模式挂在 `POST_BEFORE_TRADING / POST_BAR / POST_AFTER_TRADING / POST_SETTLEMENT / DO_PERSIST` | 每天跑完自动落盘（实测 state 可读，§5） |
| 断点续跑：怎么恢复 | `main.py:209-221`、`utils/persisit_helper.py:62-79` | `persist_helper.restore(None)` 逐对象 `set_state`；`kept_current_init_data` 决定要不要重放 init 日志 | 恢复语义完整，**但 provider 得自己给** |
| **provider 实现** | `interface.py:643-682` 只有抽象类；全库 grep `AbstractPersistProvider` **仅 1 处命中（定义处）**；`cmds/` 里 grep `persist` **0 命中**（CLI 无 `-p` 开关） | 配置 `persist: true` 而无人 `env.set_persist_provider` → `main.py:77-79` 抛 "Persist provider 未加载"（**实测，§3 S9**） | **P0：断点续跑的核心件在开源发行里是缺的** |
| `should_resume/should_run_init` | `interface.py:669-682` | 定义了，但全 core **无人调用** | 死接口，别按文档设计恢复逻辑 |

**回答任务的问题"持续运行一天模拟需要什么"**：需要 ①一个 mod 在 start_up 里
`set_data_source`/`set_persist_provider`；②窗口取 `start_date==end_date==今日`
（事件源天然只产出这一天）；③日线 store 供 **原始价 + prev_close + limit_up/limit_down + volume(+成交额)**；
④静态元数据（交易日历/合约/分红/拆股/复权因子/停牌/ST）从某处来；
⑤幂等闸（rqalpha 只跳 `before_trading`，不防同一天重跑）。

---

## 2. 原型做了什么（不碰产品代码，只在 `spikes/`）

```
spikes/rqalpha_mod_t13sim.py   ——  rqalpha 官方 mod 扩展点，三件套：
                                    · SqliteDayBarStore   我们的 daily_bars → 原始价 bar 结构化数组
                                                           （补 prev_close / limit_up / limit_down，
                                                            band 按 AGENTS #6/#7：主板10% 创业/科创20%
                                                            北交30%，四舍五入到 0.01）
                                    · T13DataSource       bundle 静态元数据 + 热替换 CS store，
                                                           并覆盖 available_data_range=(我们的首日, 今日)
                                    · FilePersistProvider AbstractPersistProvider 的文件态实现
                                                           + 会话闸（幂等 + 配置指纹，fail-closed）
spikes/live_sim_t13.py         ——  子命令 init-db（生产 CSV→我们的 schema）/
                                    day（跑一个模拟日，打 JSON 证据）/
                                    selftest（串起 9 组 21 项门禁，子进程=真重启）
```

策略侧只用公开 API（`order_shares`），证据来自事件监听
（`ORDER_*`/`TRADE`/四阶段计数）+ 账户快照（cash/frozen/qty/closable）。
空转断言按 T29 的教训制度化：**每个成交场景都要求 `handler_dates` 非空 + 有正向对照**，
防止"0 成交 0 报错 exit 0"。

---

## 3. 实测结果：一天模拟跑通（21 项门禁全绿）

复现（fixture 由生产库只读导出建起，命令可直接抄）：

```bash
# 0) 只读导出生产日线（不写任何库）
ssh nec "sqlite3 -header -csv ~/paper-trading/data.db \
  'select symbol,timestamp,open,high,low,close,volume,turn from daily_bars order by symbol,timestamp'" \
  > daily_bars.csv

PY=C:/Users/yanzh/venvs/refactor-spike/Scripts/python.exe
$PY spikes/live_sim_t13.py init-db \
    --csv ~/AppData/Local/Temp/qoder_t13/daily_bars.csv \
    --out ~/AppData/Local/Temp/qoder_t13/t13_daily_bars.db
$PY spikes/live_sim_t13.py selftest --fixture ~/AppData/Local/Temp/qoder_t13/t13_daily_bars.db
# 实测输出：`==== T13 selftest: 21/21 PASS ====`（exit 0）
# 单跑一个模拟日（今日=生产库最新日 2026-10-28，比 bundle 晚 15 个交易日）：
$PY spikes/live_sim_t13.py day --fixture <db> --today 2026-10-27 \
    --state <dir> --plan '[{"action":"buy","symbol":"000792","volume":100}]'
```

| 场景 | 断言 | 实测 |
|---|---|---|
| **S1** bundle 过期 | 不覆盖 `available_data_range` → 大声拒绝；覆盖 → 新鲜日可跑 | `CustomException: 未在 2026-10-28 和 2026-10-28 区间内查询到数据…` / 覆盖后 `handler_dates=['2026-10-28']` |
| **S2** 一个完整日 | `handle_bar` 被调 1 次；`BEFORE_TRADING/BAR/AFTER_TRADING/SETTLEMENT` 各 1 | `{"BEFORE_TRADING":1,"AUCTION":1,"BAR":1,"AFTER_TRADING":1,"PRE_SETTLEMENT":1,"SETTLEMENT":1,"POST_SETTLEMENT":1}` |
| **S3** 买入 + 当日 T+1 | 成交 1 笔 @22.21（**我们的原始价**）；日内卖单**根本没被创建**，原因可读；持仓 100 且 `closable=0`；现金 100,000→97,774（含佣金 5.00） | `reason=订单创建失败：000792.XSHE 的仓位不足…可平仓/行权量为 0` |
| **S4** **跨进程续跑** | 重启后读到昨日持仓 100；`before_trading` 时 `closable` 已 =100（**T+1 解冻**）；卖出成交 @22.10 费用 6.105（5 佣金 + 1.105 印花税）；现金 97,774→99,977.895；持仓清零 | 全绿；同时 **`SETTLEMENT` 计数 = 2**（见 §4.4） |
| **S5** 幂等 | 同日重跑被会话闸拒（exit≠0）；`--force` 绕过后**同日重复成交**、持仓变 200 | 闸文案含"rqalpha 的 resume 只跳 before_trading，handle_bar 会再执行一次" |
| **S6** 涨跌停带来自我们的 store | 002648 2026-06-23 一字板（prev 22.89→limit_up 25.18，low==25.18）市价买 → `REJECTED 已触及涨跌停价：limit_up`；**同日对照组 000651 成交 @37.29** | 与 T29-R2 在 bundle 上的结论一致，这次是**我们的数据供的 band** |
| **S7** band 缺失 fail-open | store 不供 `limit_up/limit_down`（NaN）→ 同一涨停日同一买单 **照样成交** | ⚠️ §4.5 |
| **S8** 数据缺失可见 | 000333 在 2026-10-28 无 bar → 不成交且给出可读原因（`api_stock.py:90-94`） | `订单创建失败: 当前合约[000333.XSHE]没有市场数据。` |
| **S9** provider 缺口 | `persist: true` 且无 provider → 起不来，`handler_dates=[]` | ⚠️ §4.2；且失败路径会引出 §4.7 |

**结论：M2 的"模拟盘执行线"在 rqalpha 上语义完备、无需 fork——前提是适配层补齐
store/provider/band 三件，并按 §4 逐条上门禁。**

---

## 4. 卡点清单（P0 必须先解，P1 是常驻线特有的坑）

### 4.1 【P0】`daily_bars` 存的是**前复权**，而 rqalpha 契约要**原始价**

- 实测对照（我们的导出 vs bundle `stocks.h5` 原始价，同日同标的）：
  可比 **27,420** 个标量-日，其中 **23,118 个（84.3%）** 偏差 >0.5%；
  最坏 **002594 比亚迪 2024-02-29 只有真实原始价的 32.4%（低估 67.6%）**。
- 恒等式验证（不是"看着像"）：`ours_t ≈ raw_t × F_t / F_latest`（F=bundle `ex_cum_factor`），
  5 只样本最大相对误差 **3.1e-4**（600519 4e-6 / 000333 1.1e-4 / 601318 1.5e-4 / 605099 3.1e-4 / 000792 0）。
  ⇒ 口径**确定是前复权**，代码处也自证：`paper_trading/data/akshare_fetcher.py:60` `adjust: str = "qfq"`、
  `paper_trading/integration/mother.py:14` 写明 daily_bars 是前复权。
- 为什么这是 P0 而不是"回头改"：rqalpha 的 DayBarStore 存原始价，读的时候由
  `base_data_source/adjust.py:30-77` 按 `ex_cum_factor` **再复权一次**。把 qfq 当原始价喂进去
  = **双重计入**，正是 D3 裁决里被否掉的 route A（实测 2,802.42 元偏差）的事故家族；
  而且 `limit_up/limit_down` 是从（错的）`prev_close` 算出来的 ⇒ 撮合闸整体漂移，
  尺度污染事故的第三条防线（`max_price_deviation`）也救不了引擎内部。
- **要求**：M2 数据侧交付 `close_raw`（母库列已存在，见 `mother.py:406-441` 的口径优先级），
  或交付复权因子让我们除回去；**入口必须带口径标签**（`adjust="qfq"/"raw"/"hfq"`）并让
  适配层在标签≠raw 时**拒绝启动**（不许默默换算）。

### 4.2 【P0】开源发行里没有 persist provider —— 断点续跑的必需件缺失

- 证据：全库仅 `interface.py:643` 一处 `AbstractPersistProvider`；`cmds/` 无 `persist` 开关；
  实测 `persist: true` 直接 `RuntimeError: Persist provider 未加载`（S9）。
- 判定：provider 是**纯状态序列化**（store/load 两个方法），不含撮合/计价/判定 ⇒
  属 L2 胶水的合法边界（`refactor-plan` §1"胶水层不可避免"），但它落在**交易执行的关键路径**上，
  必须按钱类件对待：写进 M2 门禁、要有单测（重启后 cash/qty/closable 与落盘前逐字段相等）。
- 本原型的实现与语义见 §5。

### 4.3 【P0】原始价在"bundle 之后"无法自证（新鲜日尺度只能外推）

- 现象：store 用 bundle 因子把 qfq 除回 raw，但因子只覆盖到 bundle 末日（2026-09-30）。
  38 只里 **7 只** 的 `scale_last ≠ 1.0`（最大 600398 = **1.0459**），
  即"bundle 末日的我们价 ≠ 原始价"，其后 6~15 个交易日的原始价只能**沿用最后一段 scale**；
  若其间发生除权除息，我们**无从得知**，喂进去的就是系统性错价。
- 另一面：`daily_bars` 每标的新鲜度不齐——2026-10-28 只有 **29/38** 只有 bar，
  其余停在 10-15/10-16/10-23/10-26（S8 的缺 bar 就是这么撞上的）。
- **要求**：M2 加"因子新鲜度"门禁（因子末日 < 今日 ⇒ 拒跑当日，不许外推）；
  或在数据侧供 `close_raw` + 当日分红/拆股事件。**外推不是降级，是错价**（AGENTS 纪律 4）。

### 4.4 【P1】跨进程续跑会**重复结算上一交易日**（实测 SETTLEMENT=2）

- 机制：`executor.py:61-62` 在会话结束时补发 SETTLEMENT；次日重启时
  `executor.py:68-79` 又根据恢复的 `last_before_trading` 把时间 rewind 回昨日**再发一次**。
- 对账本的影响：本原型实测**无害**（`Position.settlement` 基类返回 0，
  `position_model.py:182-226` 只在退市/换代码时动作；分红/除权在 `before_trading` 不在结算）；
  但**退市持仓、换代码、管理费**这三类语义会被重复应用——不能当"永远无害"放过。
- 对指标的影响：**致命且已实测**——`sys_analyser` 在 `POST_SETTLEMENT` 上挂 `_collect_daily`
  （`mod.py:255`），重复结算 ⇒ 日记录数与基准序列长度不一致 ⇒
  `rqrisk/risk.py:30 assert len(daily_returns)==len(benchmark_daily_returns)` 炸 ⇒
  `Mod tear down failed` ExceptionGroup ⇒ **一天跑完了、账本对了、进程按失败退出**。
- **要求**：常驻线**禁用 `sys_analyser`**（指标继续用我们的 `nav_history` 记录器），
  或每日窗口不重复结算（改 start/end 语义 = 动框架，不取）。本原型已按禁用处理并留了开关。

### 4.5 【P1】涨跌停带缺数据时 **fail-open**（涨停照样买得进）

- 机制：`price_limits.py:14-16` `if not (is_valid_price(price) and is_valid_price(limit_up)): return False`
  —— band 是 NaN 就判定"没到涨停"；band 的来源是 bar 字段（§1），框架不补算。
- 实测 S7：同一一字板日、同一笔市价买，store 不供 band ⇒ **成交**。
- 含义：`limit_up/limit_down/prev_close` 是**合规字段**，不是可选字段。适配层要
  "缺 band 就不跑"（本原型默认供 band，`--bands false` 只为暴露缺陷而存在）。
  并与 T29-P1/gate-8 呼应：**bar 级尺度污染 + 带缺失**都要求 L2 的绝对量级 sanity
  不随迁移删除。

### 4.6 【P1】"今日"依赖 `available_data_range`，而它只看指数 store

- `data_source.py:406-414`：区间来自 INDX `000001.XSHG`；`main.py:52-64` 用它夹窗口。
  bundle 落后时"今日"进不来（S1 实测：**报错是响的**，文案却把人往"换回测区间/升级 bundle"上带，
  真实原因是指数日线缺失）。
- 我们的覆盖（`available_data_range=(我们首日, 今日)`）是**必需的适配职责**；
  长期要么同时热替换 INDX store（母库有 `market_index`），要么保持覆盖 + 门禁。

### 4.7 【P1】进程内重入第二个会话时，真实错误会被日志掩盖

- 机制：`main.py:301-307` `cleanup_resources()` 在 `finally` 里把 `Environment._env` 置 None，
  而 `utils/logger.py:38` 的 `user_log_processor` 每条用户日志都要 `Environment.get_instance()`
  ⇒ 会话结束后再有任何用户日志 emit，就抛 `EnvironmentNotInitialized`（"Environment 并没有被创建"），
  把真正的错误（本例：Persist provider 未加载）盖在下面。
- 实测：selftest 的 S9 场景里稳定复现（断言明细直接打了
  `masked_by_env_not_init=True`）：子进程先把真因 `Persist provider 未加载`
  写进 stderr，随后在同一进程的日志收尾处死于 `EnvironmentNotInitialized`。
- **要求**：M2 的常驻线**一天一个进程**（与 cron 天然一致），runner 里不要在同一进程
  循环跑多个 rqalpha 会话；异常处理与收尾日志用我们自己的 logger，别借 rqalpha 的 user_log。

### 4.8 【P1】前端风控拒单**不产生 Order**，原因只在日志与一个事件里

- 机制：`environment.py:134-137` `submit_order` 在 `can_submit_order=False` 时**返回 None**
  （没有 order_id、没有状态、没有 broker 事件）；原因经 `environment.py:155-158`
  → `user_system_log.warn(...)` + `EVENT.ORDER_CREATION_REJECT`（只有 `order_book_id`+`reason`）。
  T+1 挡卖单的文案见 `position_validator.py:33-55`，缺行情见 `api_stock.py:90-94`。
- 实测：S3/S8 的 `order_shares` 返回 `None`，只有事件里有中文原因；**i18n 本地化**
  ⇒ 任何按英文关键字匹配的解析都会漏（"closable" 匹配就错过一次）。
- **要求**：L2 审计链（`op_log`/决策落库）必须监听 `ORDER_CREATION_REJECT` 事件抓原因，
  **不要**按文案关键字匹配；下单返回值 `None` 要当成"被拒"而不是"成功"。

### 4.9 【P1】`daily_bars` 无成交额 ⇒ VWAP 撮合口径不可用

- `bar_matcher.py:40-46` 的 VWAP 成交价 = `total_turnover / volume / contract_multiplier`；
  我们的 schema（`db_manager.py:39-47`）只有 `turn`（换手率），**没有成交额**。
  本原型用 `close×volume` 近似（已在代码里标注），仅够 mechanics 验证，
  **不足以支撑 `matching_type=vwap` 的钱类结论**。
- 要求：要么数据侧补 `amount`，要么 M2 明确**只用 current_bar 撮合**并写进文档。

### 4.10 【P2】顺带记录（不阻塞）

- `RUN_TYPE.PAPER_TRADING` 与 BACKTEST 的差别只有 4 处分支（§1），文档给人的"模拟/实盘"期待
  与实现不符；`should_resume/should_run_init` 是死接口。
- 默认费用无过户费（T29 §3 已量化，差值 0.001% 精确可补）。
- 北交所标的：bundle 无 `.BJ` 合约，本池无此类标的，M2 若要纳池需另证。

---

## 5. 断点续跑方案（M2 落地设计，原型已实现并可跑）

**状态是什么**：rqalpha 自己序列化的 8 个对象（`main.py:81-92`）——
`portfolio`（账户/持仓/`_non_closable`/分红应收）、`executor`（`last_before_trading`）、
`broker`（未成交挂单）、`user_context`、`global_vars`、`universe`、
`sys_analyser`、`sys_scheduler`。全是 `jsonpickle` 字节串，一 key 一文件即可。

**什么时候落盘**：`persist_mode: real_time`（`utils/persisit_helper.py:28-34`）在
`POST_BEFORE_TRADING / POST_BAR / POST_AFTER_TRADING / POST_SETTLEMENT / DO_PERSIST`
各落一次（内容有 md5 去重，`:43-44`）。⇒ **进程崩在半路，状态只到上一个阶段**，
重启是"继续"而不是"重放"。

**怎么恢复**：新进程 `run()` 在 `user_strategy.init()` 之后 `restore(None)`
（`main.py:209-221`）；`executor._last_before_trading` 决定新交易日是否补发
`before_trading`（T+1 解冻由它触发，`position.py:244-248`）。

**幂等与安全（我们的会话闸，`FilePersistProvider` + `meta.json`）**：

1. `meta.json` 记 `last_day` + **配置指纹**（init_cash/accounts/bundle/fixture 路径/口径/band 开关的 sha256）。
2. 请求的 `today <= meta.last_day` ⇒ **拒绝启动**（S5 实测：否则同日重复成交、持仓 200）。
   重跑当天必须显式 `--force`，且在文案里写清后果。
3. 指纹不一致 ⇒ **拒绝启动**（防止"换现金基数/换费率/换数据文件"接在旧账本上）。
4. 只在 `EXIT_SUCCESS` 后推进 `last_day`（`tear_down(code, exception)`，`mod/__init__.py:80-94`）。
5. 状态文件与 `meta.json` 同目录、临时文件 + `os.replace` 原子写（与我们 heartbeat 同一套做法）。

**与现网账本的关系**（M2 建议，不拍板）：rqalpha 状态是**引擎内部态**，我们的
`paper_account.db`（fills/nav/op_log/t1_freeze）是**审计态**。两者每天对一次账
（cash/qty/费用逐字段），差异>阈值就告警并停线——这是"双轨影子对照"在常驻期的延续，
也是唯一能证明 provider 没丢字段的办法。

---

## 6. 我建议进 M2 验收单的门禁（按本报告的编号）

| 门禁 | 判据 | 覆盖 |
|---|---|---|
| G-T13-1 口径 | 数据侧交付 `close_raw` 或因子；适配层校验口径标签≠qfq，否则拒启 | §4.1 |
| G-T13-2 新鲜度 | 因子/行情末日 ≥ 今日 − 1 交易日；不许外推 | §4.3 |
| G-T13-3 band 完整性 | 每个可交易标的当日 `prev_close/limit_up/limit_down` 有限且>0；缺失拒跑 | §4.5 |
| G-T13-4 provider | 自带 provider 的单测：重启后 cash/qty/closable/frozen 与落盘前逐字段相等 | §4.2 §5 |
| G-T13-5 幂等 | 同日重跑被拒（exit≠0）；`--force` 需在文案里承认重复成交后果 | §4.4 S5 |
| G-T13-6 结算唯一 | 常驻线禁 `sys_analyser`；退市/换代码场景另做专项（SETTLEMENT 重放语义） | §4.4 |
| G-T13-7 空转断言 | 每个成交场景 `handler_calls>0` + 正向对照（沿用 T29 harness 规矩） | §3 |
| G-T13-8 拒单可审计 | 监听 `ORDER_CREATION_REJECT` 入 `op_log`；`order_*` 返回 None 记为拒单 | §4.8 |
| G-T13-9 进程模型 | 一天一进程；不在同进程重入第二个会话；收尾日志用我们自己的 logger | §4.7 |

---

## 7. 我不做的事

- 不改 `paper_trading/**` 产品代码，不动 `config.yaml`，不碰生产库写路径、不碰 NEC/Pi 部署、不碰 cron。
- 不合并、不建 PR；只推 `worker/t13` 分支（本文件 + `spikes/live_sim_t13.py` + `spikes/rqalpha_mod_t13sim.py`），
  并在 `docs/WORKER.md` 勾掉 T13 的四格。
- §4.1/§4.3 属于"数据供给"层（L2 我们的地盘），修不修、怎么修由主 AI 拍板；
  本报告只把数字和不可自证性摆出来。§4.2 的 provider 实现若要进产品，请把 `FilePersistProvider`
  当参考件评审，别当我已经交付的产品代码。
