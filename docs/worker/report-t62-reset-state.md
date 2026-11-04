# T62 · 状态清理工具 `ledger reset-state`（2026-11-03，Qoder）

派发原文（Batch 13）：

> **T62（Qoder，P1·流程）状态清理工具**：本次峰值遗留的通用防御——账户重置/账本迁移/
> 新开账时必须清理的**状态字段清单**（`risk_state` 的 peak/daily_baseline、`engine_state`
> 熔断态等）+ 实现 `cli ledger reset-state`（显式命令 + 留痕 op_log[ledger:reset-state]）
> + 迁移流程文档补"重置后必清" checklist；测试锁定

一句话结论：清单落成 **14 项**（10 项必清 + 4 项只报不清），代码本体
`paper_trading/ledger_state.py` 是唯一真相源，CLI 与迁移文档都由它渲染；
现网库副本 4 轮试跑清掉 **59 项**、钱类 7 张表一字节未动、留痕 2 条不多不少，
**并当场照出我自己两处 CLI 接线失真**（都补了测试）。生产工作树没动，
本工具在未合并的 `worker/t59-t60-caliber @ 147b396`。

---

## 1. 逐条核销

| 派发点 | 状态 | 落在哪 |
| --- | --- | --- |
| 状态字段清单（peak/daily_baseline/engine_state 熔断态**等**） | ✅ 14 项 | `ledger_state.TARGETS`；四类 `halt`/`ghost`/`throttle`/`report`，每项带 `why`（留着会怎样）与 `action`（怎么清）——见 §2 |
| 清单必须是**通用防御**而非只补周一那一处 | ✅ | 除 peak/基线外，把同一形状的另外五处一并纳入：未决意图、已挂条件单、`var/rq_state/<日>` 会话目录、日内预算/冷却/触发标记、AI 评审节流；再加一项 `unknown_state_keys`（**清单没见过的键必须冒出来**，纪律 7 的兜底） |
| `cli ledger reset-state` 显式命令 | ✅ | `cli.py` ledger 子命令；默认 dry-run，`--yes` 才写，`--reason` 在 `--yes` 下必填，`--keep/--only/--today/--state-root/--heartbeat/--stamp` |
| 留痕 `op_log[ledger:reset-state]` | ✅ | `result` 里存 `reason/targets/kept/only/cleared{before 原值}/reports/state_dirs_moved/money_untouched`；无态可清时**不写**第二行（防刷屏，也防把 no-op 记成"清理过"） |
| 迁移流程文档"重置后必清" checklist | ✅ | `docs/migration-m3.md` §10（表格由清单渲染）+ `docs/m3-checklist.md` §5 加一条：账本重置类动作**不在 `m3 verify` 射程里**，必须跑 `ledger reset-state` |
| 测试锁定 | ✅ 26 例 | `tests/test_reset_state_t62.py`；本地全量 **704 passed / 9 skipped**（基线 678 + 本轮 26） |

## 2. 清单为什么是这 14 项——现网读出来的，不是推测

`/home/nec/paper-trading/paper_account.db` 副本（只读）实测：

| 位置 | 现网实况 | 归类 |
| --- | --- | --- |
| `risk_state.peak_total_value` | 100,936.9591（11-03 用户拍板重置后的值；重置前是 156,005.2264） | halt·必清 |
| `risk_state.daily_baseline:*` | **10 行**，其中 `daily_baseline:2026-10-27 = 156,005.2264` | halt·必清 |
| `engine_state` | `event:*` 26 行、`trigger-once:*` 9 行、`cooldown:*` 3 行、`budget:2026-10-26` 1 行 | throttle·必清 |
| `rq_intents` | 3 行全 `rejected`（切换日那三条），pending=0 | ghost·必清（pending 时） |
| `conditional_orders` | 49 行，其中 **armed 9 行**（到期 11-05） | ghost·必清 |
| `var/rq_state/<日>` | 存在 `2026-10-30`（会话 portfolio/global_vars/universe 快照） | ghost·必清 |
| `t1_freeze` | id=9：002192 100 股，`unfreeze_date=2026-11-02` 仍 `is_unfrozen=0` | **report** |
| 心跳/其他 | `var/heartbeat.json`：engine/rq_tick/minute 三键 | **report** |

周一的事故形状在表里看得很清楚：**同一把旧尺子写进了两张不同的表**。峰值 156,005.2264
是回撤熔断的分子，`daily_baseline:2026-10-27` 是日亏熔断的分子——只清 peak（当天用户
手工做的就是这一件）留下基线，下次重置照样会被基线咬一口。所以清单里两项都是必清。

**孤儿峰值怎么算死的**（`risk_manager.py:161-171` / `173-198`）：`check_drawdown` 拿
`(peak − total)/peak` 比 20%，`check_daily_loss` 拿 `(baseline − total)/baseline` 比 5%；
两个分子都是落库值，重启不丢——这本是审计 P0 的正确设计（内存态会让熔断形同虚设），
代价就是重置账本时必须显式处理它。`_peak_value <= 0` 才 no-op，所以"清"的正确动作是
**删键**（回到 0 ⇒ 从新账第一次观测重新锚定），不是写 0 之外的任何数字。

## 3. 三条写进代码而不是写进文档的规矩

1. **只清运行态，绝不动钱类**：`MONEY_TABLES = account/positions/orders/fills/
   nav_history/t1_freeze/op_log`。apply 前后各取一次快照（行数 + cash + 持仓总股数），
   不一致 ⇒ 抛错、**回滚事务、并把已改名的会话目录改回来**。
   测试用 monkeypatch 让 `_clear` 越界改一次 cash，验证三件事都发生
   （`test_money_guard_rolls_back_when_a_target_oversteps`）。将来谁往清单里塞
   动钱类的项，当场红，不用等现网炸。
2. **清之前抄现场**：`cleared[*].before` 存清理前的键与值。副本试跑落盘的第一条留痕里
   能看到 `[{"key":"peak_total_value","value":"100936.9591"}]`。
   理由写在模块头：**不留痕的清理等于毁证**——切换日那次把 3 条意图改成 `rejected`
   没有任何 `op_log` 痕迹（账本 md5 `95284… → 590f1… → cb624c9f…` 三跳，只有事后
   `result` 里的 note），事后审计只能靠 md5 反推，这是我不想再第二次的东西。
3. **该报的不许被当成"清掉了"**：4 项 `kind=report`，`--only` 点名也清不掉，
   第二次跑还会再报，且**有红报时退出码 1**（`m3 verify` 的 0/1/3 约定同族）——
   脚本不许把 0 当成"状态已一致"的凭据。
   - `t1_unfreeze_overdue`：解冻要写 `positions.available_volume`（`paper_broker.py:569-583`），
     那是 `run`/`agent tick` 结算段的活。reset-state 替它写持仓就是越界造钱类事实。
     正确处置是跑一次结算路径，或人工裁决——现网 002192 那 100 股现在 `available=0`
     卖不掉，见 §5。
   - `intent_ledger_conflict`：意图标 `rejected/expired` 而同源同日有成交，且原因不是
     幂等/去重。幂等/去重的形成本来就该与成交共存（`queue.resolve_outcomes` 规则 2/5），
     所以 SQL 里显式排除 `去重/幂等/duplicate`，否则每条正常去重都会变假红。
   - `heartbeat_file`：心跳是数据供给，不是账本状态。**删它有害**——`health check`
     与 `m3 verify` 的 minute 项按"装过才追问"判定（`cli.py` health 分支），
     键没了就退化成"没装"，采集停了也不响。
   - `unknown_state_keys`：新代码写了新键而清单没跟上 ⇒ 旧账运行态被静默留下，
     正是本次事故的形状。这项让"清单过期"这件事本身可见。

## 4. 现网副本 4 轮试跑（生产账本一字节未动）

方式：`git archive 147b396` 导出到 `/home/nec/tmp/pt-t62`（**不切生产工作树**），
把 `paper_account.db` 拷到 `/home/nec/tmp/t62-cli`，`--account-db` 指副本，
venv 用现网 `/home/nec/paper-trading/venv/bin/python`。

| 轮 | 命令 | 结果 |
| --- | --- | --- |
| 1 | dry-run | 列出必清 7 项 + 红报 2 项；`op_log` 里 reset-state 行数 **0**（没写）；退出码 1（有红报） |
| 2 | `--yes` 不给 `--reason` | `ok:false` + 明确错误文案，退出码 1，没清任何东西 |
| 3 | `--yes --only risk_peak` | 清 1 项；`daily_baseline` 10 行原样留着；留痕 1 条 |
| 4 | `--yes`（全清单） | 清 **59 项**（peak 1 + 基线 10 + 条件单 9 + 预算 1 + 冷却 3 + trigger-once 9 + event 26）；`risk_state`/`engine_state` 剩 0 行；armed 剩 0；红报仍 2 项；退出码 1 |
| 5 | 再 `--yes` | 打印"已执行（无可清的运行态，未写留痕）"，留痕总数仍 2（不刷屏）；钱类 7 表行数与首轮一致（cash 67,422.95906 / 持仓 800 股 / orders 13 / fills 12 / nav 16 / t1 9） |
| — | 生产 md5 | `d3b85438950377dcc8806a0028317a26` 试跑前后同值 |

> md5 说明（免得被反推成"我写过现网"）：上面那对值取自我这轮试跑的**前后同一时刻**。
> 收盘后（13:00 之后）现网 md5 会变成别的值——那是线自己在写：现网 `op_log` 最后三行是
> 12:42/12:51 的 `llm:ask`（面板问答），而我那两条 `ledger:reset-state` **只存在于副本**
> （副本 `op_log` 386 行、现网 387 行且最后一条是 `llm:ask`，实测对比过）。

会话目录这项在这轮试跑里 count=0——因为现网只留着 `2026-10-30`，而 `--today` 是 11-03。
这是**正确**行为（只有当日目录可能被 `intraday_resume` 接走），也说明用法上的一条约束：
为哪一天重开账本就显式给 `--today <日>`，否则那一天的会话目录不会被挪。

## 5. 现网照出来的两处自打脸（都已修 + 加测试）

1. `plan() got an unexpected keyword argument 'stamp'`——dry-run 分支把同一份 kw
   连 `--stamp` 一起塞给 `plan()`，第一次跑真命令就 TypeError。
   教训：22 条模块级测试全绿也抓不到 **argparse↔handler 接线**。已补 3 条走真
   `cli.main()` 的用例（`9e3ae70`）。
2. no-op 那次自称"dry-run（--yes 才动手）"——`--yes` 明明给了，只是没东西可清。
   操作者会以为自己漏参数，然后重复敲 `--yes`。标签改成跟事实走（`147b396`），
   三种模式：已清并留痕 / 已执行（无可清项）/ dry-run。

## 6. 两件必须上报的现网遗留（`reset-state` 只报不清，等人裁决）

1. **002192 的 100 股卖不掉**：`t1_freeze` id=9 `unfreeze_date=2026-11-02` 仍 `is_unfrozen=0`，
   `positions.002192.available_volume=0`。解冻只在 `run`（`cli.py:392`）和
   `agent tick`（`agent/loop.py:794`，且 `dry_run` 跳过）里发生，而 11-03 的 agent 心跳
   缺键（`var/heartbeat.json` 只有 engine/rq_tick/minute）⇒ 11-02 收盘窗口没跑成
   （那天 crontab 里 16-18 那条是被 `--symbols \` 拼坏的重复行，11-03 11:26 已清理）。
   这笔冻结会在下一次 `agent tick` 落地时自动解开；但如果切换后 agent tick 长期不进
   结算段，**新线上就没有解冻入口**——这是 M3 之后的结构性缺口，请主 AI 定夺
   （要不要把 `unfreeze_t1` 挪进 `rq tick` 首跳或 `m3 verify` 加一项红）。
2. **切换日 3 条意图与账本口径冲突**：`002192_BUY_100@16:45:25` 与 `@16:49:24` 都标着
   `rejected`、result 写"no ledger fill"，而订单 #13 / 成交 #12（002192 BUY 100 @44.94，
   2026-10-30T16:49:26）就在账里。一次成交对两条被拒意图，必有一条是假的。
   `m3 verify` 的 `intent_queue` 项已经把这块报成钱类红过（T57）；现在探测固化进
   `reset-state`，但**谁真谁假要主 AI 裁决**——我不会替账本改判。

另两件如实自报（同族问题，供审计）：
① 11-03 上午排障时我 `rm -f var/heartbeat.json` 一次（想看"心跳文件缺失时工具是否
fail-loud"）。文件随后由 engine/rq_tick/minute 三键自愈，现网 md5 与行为未受影响，
但**删运行态文件不该由我临时决定**，写在这里；这正是 T62 把 heartbeat 划入"只报不清"
的原因。② 生产 `paper_account.db` 的写入我只留了两条授权例外（T40 回填、T42 parity 留痕），
T57/T59/T60/T62 全程用副本，md5 证据在上面各轮。

## 7. 交付物与回退

- 分支：`worker/t59-t60-caliber`（**未合并**，等 review）
  `d635d60` 清单+工具 → `9e3ae70` CLI 接线修复+3 例 → `147b396` no-op 标签+1 例。
  上一批（T59/T60）已由主 AI 合入 main（生产工作树 `main @ 6895564`）。
- 文件：新增 `paper_trading/ledger_state.py`（445 行）、`tests/test_reset_state_t62.py`（26 例）；
  改 `paper_trading/cli.py`（ledger 子命令 + 渲染 + 退出码）、
  `paper_trading/integration/rq_engine/ledger.py`（archive 现在报自己清不掉的会话目录，
  `reset_state_pending`/`reset_state_reports`）、`docs/migration-m3.md` §10、`docs/m3-checklist.md` §5。
- 生产改动：**无**（工作树未切分支、cron 未动、账本 md5 不变）。合并后运维要用的就是
  `ledger reset-state` 一条命令，无状态、无常驻进程。
- 回退：`git revert` 三个提交即可；`ledger archive` 的新参数有默认值，旧调用形状不变。
- 测试：本地 `python -m pytest -q tests/` **704 passed / 9 skipped**（无 rqalpha 环境；
  基线 678 + 本轮 26，其余测试零改动）。

## 8. 待拍板（不进本轮实现）

1. `reset-state` 的 4 项红报要不要并进 `m3 verify`（同一把尺子，切换日照读清单时
   少一个命令）；我倾向并，但那会让 verify 在"钱对不对"之外多问"状态齐不齐"，
   语义边界请主 AI 定。
2. `--state-root` 目前默认 `var/rq_state`（与 `rq day/tick` 的默认一致）。是否配置化
   进 `config.yaml`——注意我的红线：**不改 config 默认值**。
3. 条件单默认全取消（`cond_armed` 是 ghost 类）。若主 AI 认为"价挂单属于研究资产、
   重置后该留"，把它挪到 `required=False` 即可（清单与文档表会同步跟着变，测试会红一次
   提醒改文档——这正是 纪律 7 要的效果）。
