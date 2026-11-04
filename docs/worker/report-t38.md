# report-t38 · 影子留痕默认写 + 过户费 decider（Qoder，P0·Batch 8 裁决 4+5 联动）

分支 `worker/t38`（**待 review，未合并**）。基线 `origin/main@aaedea4`
（Batch 7 全并入，431 passed / 4 skipped）。

## 0. 一句话结论

`rq shadow-day` 现在**默认**把对账结论写进生产账本 `op_log[parity:diff]`（白名单锁死），
且过户费 0.001% 双向已进引擎——22 万元单子的实测：
**两侧费用逐分相等（`fee_delta=0.0`，各 57.2 元）、现金一致（779,942.8）**；
端到端 **16/16 PASS**，全套 **461 passed / 1 skipped**（装了 rqalpha 的 venv）
与 **453 passed / 9 skipped**（生产口径 venv，无 rqalpha）——零回归。

## 1. 裁决逐条核销清单（协议新条款，`docs/WORKER.md:22`）

对照 `docs/refactor-plan-open-source.md` §5.5 + `docs/worker/questions.md` 批复节
+ `docs/WORKER.md:583-599` Batch 8 派发文本，逐条：

| # | 拍板原文（要点） | 状态 | 落地点 |
|---|---|---|---|
| 裁决 4 / 批复 ① | 影子对账 **op_log 默认写**（生产账本），`record_to` 加 **action 白名单断言**，只许 `parity:diff` | ✅ 已落地 | `cli.py:1362`（`--no-op-log` 取代 `--allow-op-log`）、`cli.py:1846-1872`（默认调 `record_to`）、`shadow.py:35`（`OP_LOG_ACTIONS`）、`shadow.py:191-198`（断言 + `action` 形参）；测 `tests/test_fees_and_oplog_t38.py::test_record_to_rejects_any_other_action`（6 个非法 action）、e2e S1/S2/S4 |
| 裁决 4 / 批复 ① | 测试三件：默认落库 / 白名单拒绝其他 action / 关闭旗生效 | ✅ | 同上；e2e S1（默认 1 行且 `result.fee_delta=0.0`）、S2（`--no-op-log` → op_log 空）、S4（broker 从未被非法 action 触达） |
| 裁决 5 | **D4 过户费 decider**：自定义 `AbstractTransactionCostDecider` 补 0.001% 双向 | ✅（实现细节见 §2） | 新模块 `rq_engine/costs.py`；装载点 `mod_entry.py:43-56` + `mod_entry.py:125`（POST_BEFORE_TRADING）+ `mod_entry.py:139`（evidence 出 `cost`）；测 `test_overlay_*` 5 条（需 rqalpha，实测已跑） |
| 裁决 5 | **联动验收信号：`fee_delta` 归零** | ✅ 实测归零 | e2e S1：rq 57.2 = legacy 57.2，`fee_delta=0.0`；S3 变异（overlay 装不上）立刻 `fee_delta=-2.2` 且门禁停线 ⇒ 0 不是巧合 |
| 裁决 5 | "此前显式 0.001% 预期差逻辑相应收敛" | ✅ 已删除 | `shadow.py:33`（`FEE_TOLERANCE_RATIO` → `FEE_ABS_TOLERANCE=0.01` 绝对容差）；旧用例 `test_parity_diff_accepts_known_fee_gap` 前提被推翻，改写为 `test_parity_diff_rejects_the_old_transfer_fee_gap` + 新增 `test_parity_diff_clean_when_fees_match`（`tests/test_rqalpha_adapter.py:247-272`） |
| 批复 ② | 迁移"打开即自动"（默认）+ 落 `op_log[data:migrate]` | ✅ | `db_manager.py:20-29`（`self.migrations` 记录）、`db_manager.py:103/135/186-190`（三类迁移登记）、`cli.py:1153-1166`（`log_data_migrations`，取走即清空）、`cli.py:1642`（bridge 建好即留痕）；测 `test_migration_is_recorded` / `test_second_open_and_fresh_db_record_nothing` / `test_cli_helper_lands_one_op_log_row` |
| 派发文本 | `report-t38.md` + 测试 | ✅ | 本文件、`tests/test_fees_and_oplog_t38.py`（23 条）、`tests/test_minute_pk_migration.py`（+3）、`spikes/e2e_t38.py`（16 项） |
| 派发文本 | "若 T35 补丁更早就先做" | 不适用 | T35 已合入 main，本批只做留痕，未回头改 T35 的迁移本体 |

**没有"实现反解拍板"的余地**：唯一与拍板字面不同处是 decider 的继承基类
（`AbstractStockTransactionCostDecider` 而非裸 `AbstractTransactionCostDecider`），
理由与证据见 §2，不擅自判定"等价"，在此上报请主 AI 确认。

## 2. 过户费怎么进引擎（以及为什么不是"重造一个 decider"）

框架侧事实（rqalpha 6.4.2，逐行核过）：
`StockTransactionCostDecider.calc` 只返回 commission/tax（`deciders.py:112-113`），
**没有过户费这一项**；`TransactionCost.total = commission+tax+other_fees`
（`interface.py:745-752`），而现金扣减与资金充足性校验都读 `.total`
（`position_model.py:293/426/434`、`matcher/base.py:108-116`）⇒
把过户费放进 `other_fees` 是**唯一不需要改框架**的正规入口。

实现（`costs.py`）：
- `TransferFeeOverlay` **包住**框架已注册的 decider：`calc` 先委托再
  `+ price×quantity×rate` 进 `other_fees`；佣金/印花税/最低佣金的 per-order
  记账（`deciders.py:42-68` 的 `commission_map`）原样留在框架里。
  **为什么不另写一个 decider**：那要把佣金倍率、最低佣金、印花税率各抄一遍，
  `config.yaml` 一改就静默漂移——独立基准原则不允许我们复制真相源。
- 必须继承 `AbstractStockTransactionCostDecider`：`order_target_portfolio` 用
  isinstance 卡这个类型（`api/order_target_portfolio.py:270-280`），
  裸 `AbstractTransactionCostDecider` 会让 `order_target_*` API 直接 RuntimeError。
  同时 `batch_estimate` 也透传并加上过户费，估算与实扣同口径。
- **只对 CS 生效**：框架把股票 decider 注册给 CS/LOF/INDX/REITs（`mod.py:134-140`），
  而"0.001% 双向"这条真实规则只对股票成立；基金/REITs 我没找到可信口径 ⇒ 不加，
  现网池 10 只全是沪深主板股票，今天不受影响。哪天池子进 LOF/REITs，
  对账会用 `fee_delta` 喊出来（见 §4 遗留 1）。
- 装载时机是**顺序要求不是偏好**：`ptpaper` 的 `priority=50` 比
  `sys_transaction_cost`（默认 100）先 `start_up`（`mod/__init__.py:68` 按 priority
  排序后依次 start_up），所以 mod 自己的 `start_up` 里根本还看不到框架 decider。
  挂在 `EVENT.POST_BEFORE_TRADING`（`executor.py:85` 的盘前事件展开）上才稳定，
  且晚于框架 `pit_tax` 的 `PRE_BEFORE_TRADING` 税率更新。
- **fail-loud 门禁**：`check_session_gates(..., expect_transfer_fee=True)` 发现
  `evidence.cost.wrapped` 为空就停线（`runner.py:162-166`）。框架哪天改了注册方式，
  是它先响，而不是我们悄悄少收一遍费。
- 幂等：`make_overlay_class()` 缓存类对象（`costs.py:70-81` + `costs.py:113-114`），否则
  `isinstance(decider, Overlay)` 恒假 ⇒ 重复安装 = 收两遍过户费
  （`test_overlay_is_idempotent` 正是这样抓出来的，修前实测第二次安装返回 `['CS']`）。

## 3. 顺带撞破的 P1：产品化 runner 一直在用框架默认佣金（万八）

**事实**：`build_config`（T32 产品化时的版本）根本没有 `sys_transaction_cost` 段
⇒ rqalpha 用 `__config__` 默认 `stock_commission_multiplier=1`，即
**佣金万八**（`deciders.py:73 commission_rate=0.0008`），而现网是万 2.5
（`paper_broker.py:255-266` + `config.yaml trading.commission_rate`）。

**为什么 T29/T32 的实测没暴露**：那些单子金额太小，**两侧都撞上 5 元最低佣金**，
佣金差被 `max(..., 5)` 吃掉，于是"费用差恰好等于过户费"看起来完美成立。
量级：22 万元单子，万八 = 176 元 vs 万 2.5 = 55 元，**差 121 元**（本报告的 e2e
就是这个量级，修前必然报 violation）。

**归属**：这是我 T32 产品化的缺陷——把费率对齐留在了 `spikes/*.py` 的
`extra_mod` 里（`dual_run_t29.py:152`、`parity_t9.py:187` 等 5 处），
没有搬进 `build_config`。已按"费率进会话指纹"的原则修复：
`runner.py:93` 钉 `sys_transaction_cost`，`runner.py:135-136` 把
`cost` + `transfer_fee_rate` 加进 `session_fingerprint`（改了费率还想续跑昨天的
状态 = 两本账混着算，G-T13-5 语义）。

**同时修的一处 schema 细节**：`daily_bars` 的 `CREATE TABLE` 没带 `adjust` 列，
新库刚建好就立刻触发一次 `ALTER` ⇒ 每个新库都会误报一条"迁移"留痕。
已在 DDL 里补列（`db_manager.py:120`），ALTER 只服务 T31 之前建的老库，
留痕因此才有意义（`test_second_open_and_fresh_db_record_nothing` 锁住）。

## 4. 证据

**e2e（`spikes/e2e_t38.py`，16/16 PASS，每场景一个子进程）**
关键数字（000792 买 20,000 股 @11.00 = 22 万元，`--init-cash 1,000,000`）：

```
S1 rq 现金 779942.8 == legacy 现金 779942.8
   rq 费用 57.2 == legacy 费用 57.2（= 佣金 55 + 过户费 2.2）
   diff = {cash_delta 0.0, fee_delta 0.0, position_delta {}, fill_delta 0, violations []}
   evidence.cost = {transfer_fee_rate 1e-05, wrapped ["CS"],
                    deciders [{CS, rate 1e-05, applied 4, added_total 8.8}]}
   账户库 op_log 恰好 1 行：action=parity:diff，result.fee_delta=0.0
S2 --no-op-log → op_log 为空，对账结果与 S1 相同
S3 变异（子进程内把 install_transfer_fee 打成空操作）：
   门禁 errors = ["TransferFeeNotInstalled: ...（cost={'wrapped': [], ...}）"]
   fee_delta = -2.2（rq 55.0 / legacy 57.2）、cash_delta 2.2 > 1.0、ok=False
S4 六个非法 action（buy/sell/data:migrate/parity:diff:x/空/None）全部 ValueError，
   broker 未被触达；默认 action 正常写入
```

**`applied=4 / added_total=8.8` 的读法**（避免以后误当成"收了 4 遍费"）：
`calc` 除了真成交，还会被下单前估算调用（`model/order.py:323`、
`matcher/base.py:108` 的资金充足性校验）——`applied` 是**调用次数**，
真正进现金的是成交那笔的 `trade.transaction_cost`（57.2 里含 2.2）。
evidence 出这两个数是为了"装没装上"可核，不是为了计数收费笔数。

**测试**：`tests/test_fees_and_oplog_t38.py` 23 条（18 条不需 rqalpha，
5 条 overlay 真身在有 rqalpha 的 venv 里跑），`test_minute_pk_migration.py` +3 条，
`test_rqalpha_adapter.py` 旧前提用例改写 2 条。全套：
- 装了 rqalpha 的 venv（开发机 `refactor-spike`）：**461 passed / 1 skipped**
- 生产口径 venv（无 rqalpha）：**453 passed / 9 skipped**
- main 基线 431 passed / 4 skipped ⇒ 新增 22 条，零回归。

**变异检查（源码级，改完即还原）**：把 `FEE_ABS_TOLERANCE` 调成 1000.0 并把
`record_to` 的白名单断言改成 `if False:`（= 回到"相对 1% 容差 + 任何 action 都能写"
的旧形状），**7 条用例必红**（4 条白名单参数化 + 2 条容差 + 1 条 T32 改写用例）；
还原后 18 passed / 5 skipped。加上 e2e S3 的运行级变异（overlay 装不上 → 门禁停线 +
`fee_delta=-2.2`），新断言不是"写了就绿"。

## 5. 需要主 AI 定 / 遗留

1. **ETF/LOF/REITs 的过户费口径**：现网旧引擎对**所有**标的按 0.001% 收
   （`paper_broker.py:255-266` 不分成合约类型），而真实规则里基金类没有过户费。
   我只给 CS 加（`costs.py` 模块头写了理由），现网池全是股票所以今天无差；
   若以后池子进 ETF，旧引擎会**多收**——这是旧引擎的口径问题，要不要现在修
   `FeeModel`（会改历史账本的可解释性），请拍板。
2. **历史回测的印花税**：现网固定 0.05%，框架有 PIT 选项（2023-08-28 前千 1，
   `deciders.py:29-31/85-89`）。我显式 `pit_tax=False` 保持与现网同源；
   真要跑 2023-08-28 之前的历史回测，"正确"的是开 PIT 并承认旧引擎少收了。
3. **默认写 op_log 的量级**：影子期每天一行 `parity:diff` 进生产账户库
   （含手工补跑）。审计流里这是新增噪声但可过滤；面板现在不显示这个 action，
   要不要在面板加一行"今日双轨对账"展示（M3 面板件），请拍板。
4. **`rq day` 的费率来源**：现在 `rq day`/`rq shadow-day` 都从
   `bridge.broker.config`（即 `config.yaml trading.*`）取费率传给引擎
   （`cli.py:1846-1872`）。好处是单一真相源；代价是**改 config.yaml 就等于改引擎
   账本语义**（指纹会变、同日续跑会被拒）。若你更希望引擎费率独立于展示层配置，
   说一声我改成显式 `rq_engine.fee` 配置段。
5. **G-T13-5 副作用**：指纹新增 `cost`/`transfer_fee_rate` 后，
   任何"昨天留下的 state_dir"在今天都会判为漂移。NEC 上 M2 线还没起过会话
   （T39 才开始），所以生产无历史包袱；开发机 spike 的 state 目录是我自己的 scratch。

## 6. 环境与红线

- 只动本 worktree（`C:\Users\yanzh\AppData\Local\Temp\pt-worker-t38`，分支 `worker/t38`），
  不碰 `G:\opencode\paper-trading` 主树与 OpenCode/Hermes 的在制品。
- **没跑任何写现网账本的命令**：e2e 全部用 `--data-db/--account-db` 指向
  `%TEMP%\pt-t38-e2e` 下的 scratch 库（S1 写的 op_log 在 scratch 账户库里）；
  `config.yaml` 一字未动（e2e 用的是 scratch 目录里自己写的 config）。
- 为让"需要 rqalpha"的 5 条用例真跑起来，我在**开发机自己的** spike venv
  （`C:\Users\yanzh\venvs\refactor-spike`）里 `pip install --no-deps pytest pluggy
  iniconfig pygments`——`--no-deps` 是为了**绝不动 numpy/pandas**（该 venv 是
  numpy 2.5.4 / pandas 3.1.0，装前后均已核对）。NEC 生产 venv 零改动。
- 未合并、未建 PR、未 force push、未碰 Pi/secrets/cron/tag。
