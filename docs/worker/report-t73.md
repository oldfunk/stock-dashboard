# report-t73.md — init_positions 最后一环：真因是**形态**，不是顺序（Qoder · 2026-11-05）

派发：`docs/WORKER.md` Batch 18 · T73。核对基准=派发原文三条（取证 / 修复+四场景 /
回归+固化 / report）。全部改动在分支 `worker/t71-t73-init-positions`，**未合 main**。

## 1. 取证：mod `start_up` 与 `Portfolio(...)` 的先后（文件:行，rqalpha 6.4.2）

路径：`/home/nec/venvs/rq-engine/lib/python3.12/site-packages/rqalpha/`

| 事实 | 位置 |
|---|---|
| `Environment(config, ...)` | `main.py:134` |
| `mod_handler.set_env(env)`（按 `priority` 排序 mod） | `main.py:146` + `mod/__init__.py:68` |
| **`mod_handler.start_up()`**（我们的 `ptpaper` priority=200，最后起跑，但都在这一步） | `main.py:147` |
| `if not hasattr(env,"data_source"): set_data_source(BaseDataSource(...))` | `main.py:150-151` |
| `set_data_proxy(DataProxy(env.data_source, env.price_board, market))` | `main.py:155` |
| `publish_event(INIT_PORTFOLIO)`（全包**无任何监听者**，`set_portfolio` 只有 `main.py:174` 一处调用） | `main.py:171` |
| **`env.set_portfolio(Portfolio(config.base.accounts, config.base.init_positions, ...))`** | `main.py:172-176` |
| 期初持仓逐只取 `prev_date` 的 1d bar 当成本价 | `portfolio/__init__.py:78-81`、`portfolio/account.py:90` |

**结论：顺序无罪。** 我们的 `PaperDataSource` 在 `start_up`（:147）里注册，而
Portfolio 在 :174 才建，中间 :155 的 `data_proxy` 用的就是 `env.data_source`——
主 AI 怀疑的"Portfolio 构建时用的 data_source 不是我们的（顺序/注册时机）"
**排除**（`hasattr(env,"data_source")` 为真 ⇒ :151 的 `BaseDataSource` 兜底分支根本不进）。

## 2. 真因：`parse_init_positions` 只认字符串，dict 进去就是空账户

```
utils/config.py:213   config.base.init_positions = parse_init_positions(config.base.init_positions)
utils/config.py:271   def parse_init_positions(positions):
                          result = []
                          if not isinstance(positions, str) or not positions:
                              return result          # ← dict / list 一律变成 []
```
`run_func`（`__init__.py:208`）**必经** `parse_config`，所以每一次会话都过这一刀。
引擎 venv 实测（`/home/nec/tmp/t73_probe.py`）：

```
{'002192.XSHE': 100}      -> []
[('002192.XSHE', 100)]    -> []
'002192.XSHE:100'         -> [('002192.XSHE', 100.0)]
parse_config(我们 build_config 的形状).base.init_positions -> []   (type=list)
```

`build_config` 一直在给 dict ⇒ `Portfolio` 每次建的都是**空仓账户** ⇒
002192 卖单被"仓位不足"拒。也解释了为什么 ③④⑤ 三个 `get_bar` 修复"单测通过、
key 格式正确"却在现网没效果：`account.py:90` 那句 `get_bar(ob, prev_date)`
在生产路径上**从来没被执行过**（循环列表一直是空的）。

现网物证（`var/rq_state/live/`，11-05 15:52 那跳）：
`portfolio.state` = `"positions": {}, "total_cash": 100000.0`——框架既不知道账本 7 只仓，
也不知道账本现金是 51,590.80（10 万是 `INIT_CASH_DEFAULT`，那次跑的入口没带账本现金）。

## 3. 修复（同族缺口一次做完，逐条对应现网后果）

1. **形态**（`runner.init_positions_arg`）：dict → `"002192.XSHE:100,600519.XSHG:200"`；
   键必须带交易所后缀、数量必须正整数，`total_volume=0` 跳过（账本清仓留行是常态）；
   **排序**保证指纹稳定（dict 插入序随 sqlite 返回序变）。`build_config` 用字符串形态，
   同时把原始 dict 另存进 `mod.ptpaper.init_positions` 供复核。
2. **复发哨兵**（`runner.reconcile_init_positions` + mod `_check_positions_landed`，
   `InitPositionError`）：喂进去 N 只、解析后必须还是 N 只。两类缺失分诊：
   标的表里根本没有（开源 bundle `.BJ` 实测 0 只，`920156.BJ`/`920156.XSHG` 都查不到）
   ⇒ 如实剔除并 warn，会话照常；**其他原因的缺失 ⇒ 拒开这一跳**。
   第二道在 `POST_SYSTEM_INIT` **和** `POST_SYSTEM_RESTORED` 都挂：续跑跳的真账户是
   `persist_helper.restore`（`main.py:210`）换上去的，只挂前者等于拿"应该是多少"
   核对"应该是多少"——e2e 的 guard 就是这么发现漏挂的。
3. **一条口径**（`runner.ledger_init_state`）：现金/持仓/冻结三件套从账本读一次，
   交易所映射统一走 `utils/markets.order_book_id`。原来 `cli.py` 两处 + `spikes/_p0_scene.py`
   各抄一遍 `code[0] in '69'`（T74 P2 的根）：`920156` 会被拼成 `920156.XSHG`
   ⇒ 那笔仓"静默消失"。`rq tick` 现在读不出账本就**拒开会话**（emit error + exit 1）。
4. **T+1 冻结的时机**（`t1_freeze_plan` + `_apply_t1_freeze`）：旧写法在 `start_up` 里
   读 `env.portfolio`（此刻 Portfolio 还没建，异常被咽成 warning ⇒ 对齐从未生效，
   T74 P1 第二条坐实）；就算挪到 `POST_SYSTEM_INIT` 也不够——
   `Position.before_trading()` 在 PRE_BEFORE_TRADING 里把 `_non_closable` 清零
   （`portfolio/position.py:248`，"昨天的仓今天可卖"）。现在挂
   `POST_BEFORE_TRADING`（新会话）+ `POST_SYSTEM_RESTORED`（续跑跳），并且
   **只加不减**：把 `_non_closable` 调小等于解锁 T+0 卖出，那是拆合规闸。
   两个事件都不存在 ⇒ 有仓要冻就**拒开**；会话后置闸新增 `T1FreezeMissing`
   （账本冻结没有对齐留痕 ⇒ 不落账、exit 1）。
5. **供给并集**（`build_config`）：store 标的 = 意图标的 ∪ 期初持仓标的。
   否则 Portfolio 取 `prev_date` 成本价时 store 里没这只 ⇒
   `invalid init position …: no valid price` ⇒ 会话起不来（这是修通形态后必然撞的下一面墙）。
6. **1d 聚合的 prev_close 有限性**（`data_source.get_bar`，T74 P2）：非有限就返回 None
   并写进 `store.problems`——band 是撮合判涨跌停的唯一依据，NaN 蒙过去等于全天失真。
7. **指纹**（`session_fingerprint`，T74 P1）：`intraday=True` 时豁免
   `accounts / symbols / init_positions / init_frozen` 四个**账本派生量**（第一跳成交后
   它们必变，留在指纹里日内第二跳起被自己的漂移判据拒）；真语义锚
   （费率/撮合口径/frequency/ledger/数据文件）一个没放，豁免清单本身也进指纹。
8. **`RqAttrDict` 回读陷阱**（`run_session` 的闸参数）：`cfg["mod"]["ptpaper"]["init_frozen"]`
   在 `run_func` 之后不再是普通 dict（不可下标），改用它旁边的原始入参——
   这条是**真跑**才暴露的（第一版四场景全红在这里）。
9. **`ledger reset-state` 现在认 `live`**（`ledger_state._state_dirs`）：dd0c2c4 把状态目录
   从 `<日>` 改成固定 `live` 之后，我这个 T62 工具的 `rq_session_state` 只探 `<日>`，
   重置时会说"没有要清的"，而脱钩的 portfolio.state 正躺在 `live` 里。现在两个都探、都挪。

## 4. 四场景（现网库副本，NEC 引擎 venv，`--workdir /home/nec/tmp/t73/run5`）

账本副本形状取自现网口径：现金 50,000；002192 100/100（可卖）；300390 200/0（全冻）；
买入标的 000858（账本没有，11-05 现价 75.82）。**每场景一个子进程、独立 state 目录、
`ledger=False`**（不写生产账本、不写 op_log）。

| 场景 | 判据 | 实测 |
|---|---|---|
| A T+1 冻结拒 | 框架看得见 200 股但平不了 | `init_align.kept=[002192:100, 300390:200]`、`frozen_align.closable={"300390.XSHE":0}`、拒单原因"300390.XSHE 的仓位不足，目标平仓/行权量为 200，可平仓/行权量为 0.0"、trades=0 |
| B 超支拒 | 现金基线=账本 5 万而非框架默认 10 万 | 拒单"可用资金不足。当前资金: 50000.00，000858.XSHE 下单所需资金: 52583.67"、trades=0 |
| C 正常卖成交 | 可卖仓真成交且有三分量费用 | SELL 002192 100 @47.30，commission 5.0 / tax 2.365 / other_fees 0.0473 |
| D 正常买成交 | 账本买得起的单成交 | BUY 000858 100 @75.10，commission 5.0 / tax 0 / other_fees 0.0751 |
| （加测）脱钩拒跑 | 同 state 目录再跑一遍 C：框架已卖光、账本仍 100 | 第二跳报 `框架账户比账本少 1 只仓`，不开会话 |

**四场景全绿 = 修复完成**（派发口径），加测那条是 T72 要的 fail-closed 语义。

## 5. 固化与回归

- `tests/test_init_positions_e2e.py`：同一份代码两种跑法（pytest 入口 + `__main__`
  驱动）。CI 里 `find_spec("rqalpha") is None` ⇒ skip；NEC 上父进程用产品 venv、
  子进程用引擎 venv（G-T13-9 一天一进程）。只用临时目录里的账本副本与 data.db
  只读查询。`--out` 落文件（PaperBroker 的 INFO 日志走 stdout，混进 JSON 就没法机读）。
- `tests/test_init_align_t73.py`（15 例，CI 跑）：形态/排序/脏数据、
  `reconcile` 三分诊、`t1_freeze_plan` 只加不减与封顶、
  `ledger_init_state` 口径（含 `920156.BJ`）、指纹日内豁免与日模式仍锁基线、
  以及**有 rqalpha 时**断 `parse_init_positions` 契约（dict 进不来、我们的形态进得来）。
- `tests/test_decision_gate_t71.py` 9 例（见 report-t71）、`tests/test_m3_verify_t46.py`
  新增 9 例（Q5 分档）、`tests/test_engine_behavior_t68.py` 新增 1 例（Q1，做过变异验证）。
- **全量：806 passed / 11 skipped**；`python tests/wiring_audit.py` 退出码 0（无未豁免断层）。

## 6. 上线动作（部署与 cron 由主 AI/运维执行，我没碰）

1. 修好的形态**只对"新建的 state"生效**：续跑跳的账户从 `portfolio.state` 恢复，
   而现网 `var/rq_state/live/portfolio.state` 里是一本 `positions={}` + 10 万现金的账户。
   所以**先停 `rq tick` cron**，再 `ledger reset-state --only rq_session_state --yes
   --reason "…"`（本分支起它会挪开 `live`，改名 `live.pre-reset-<时刻>`，不删），
   然后部署、恢复 cron。下一跳从账本重建框架账户。
2. 不这么做的后果现在是**响的**而不是静默的：`框架账户比账本少 N 只仓` 会挡住每一跳。
3. Batch 17 留的"002192 意图已入队，明天开盘验证执行"——那条意图
   （`002192_SELL_100@2026-11-05T15:09:20`，created_at=decided_at=15:09）按现状会在
   11-06 09:02 那一跳被 `expire_stale` 判 expired，**等不到明天开盘**。这不是 T73 的
   缺陷，是两条既有口径打架，已写进 `questions.md` **Q6** 等裁决（要验证执行请明天
   盘中重新入队一条）。

## 7. 我没做的两件事

- **没合 main、没部署、没碰 cron/Pi/secrets**；生产树仍在 main，本分支待审。
- `_non_closable` 走的是框架内部态（`p._non_closable`），因为**框架没有公开 API**
  能设期初冻结量（`position_model.py` 只在 OPEN 成交时加）。这条依赖框架私有字段，
  已用 `T1FreezeMissing` 闸 + e2e 场景 A 兜住：框架哪天改了，我们先响，不静默。
