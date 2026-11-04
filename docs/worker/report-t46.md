# report-t46 · 切换后检查清单 + `m3 verify` 工具（Qoder，P0·Batch 9）

分支 `worker/t46`（堆叠 `t45`→`t42`→`t40`→`t41`，**待 review 按序合并**）。
生产口径 venv 全套 **499 passed / 9 skipped**（main 基线 453/9 ⇒ 新增 17（T41）+29（本任务），零回归）。
**按派发要求：这套工具请先给 Hermes 审一遍再上生产**（`m3 verify` 会读现网账本，
但它本身**只读**，见 §4）。

## 0. 一句话结论

`python -m paper_trading.cli m3 verify` 落地了：10 项检查、4 项标钱类、
**退出码把"该不该回退"变成机器可读**（0 绿 / 1 非钱类红 / 3 钱类红=立即回退 / 2 工具自己跑不起来）；
配套 `docs/m3-checklist.md` 三段（第一小时 6 项 / 第一天 9 项 / 第一周 8 项），
**每项都写了自己的"红了怎么办"**（回退 / 归因 / 容忍），能自动化的进工具，
判不了的明确写"人工"，并且专门写了一节"工具不覆盖的"防止被当成"已经验过"。

## 1. 逐条核销清单（`docs/WORKER.md` Batch 9 · T46 原文）

| 派发原文 | 状态 | 落地点 |
|---|---|---|
| 「`docs/m3-checklist.md`：第一小时 / 第一天 / 第一周三段逐项——T+1 冻结正确、费用三件套扣对（佣金地板/印花税单边/过户费）、NAV 收盘记录、条件单触发链、AI 计划执行链、审计留痕完整、面板数据一致、影子对账记录连续」 | ✅ 八项全覆盖 | 清单：费用三件套 2.1（含地板/单边/双向，`m3_verify.check_fees`）、T+1 2.2、NAV 2.3、条件单 2.8、AI 计划 2.7、审计留痕 2.4+3.1、面板一致 3.8+1.5、影子连续 2.6+3.1；另加派发没点名但切换日更要命的 **双活检测 1.1/1.2** |
| 「可自动化项做成命令（如 `python -m paper_trading.cli m3 verify`：逐项检查出绿勾/红叉），人工项写清判定方法」 | ✅ | `paper_trading/utils/m3_verify.py`（新模块，10 个检查 + 汇总 + 文本渲染）、`cli.py` 新增 `m3 verify` 子命令（人读/`--json`/`--money-only`/`--days`/`--today`/`--symbols`/`--heartbeat`）；人工项每条写"怎么跑/期望/红了怎么办" |
| 「每一项标注"红了怎么办"（回退 or 归因 or 容忍）——**任何钱类项红 ⇒ 立即回退**（§5.6 底线）」 | ✅ | 每项带 `money` 与 `on_red` 字段；`verify_all` 汇总出 `rollback_required/money_reds/on_red` 映射；CLI 用 **exit 3** 表达"钱类红"、exit 1 表达"非钱类红"，cron 可分别告警；人读输出末尾直接印「❗钱类项红 ⇒ 按 §5.6 底线**立即回退**（步骤见 docs/migration-m3.md §5.1）」 |
| 「`report-t46.md`；工具给 Hermes 审一遍再交付」 | ⚠️ 报告已交，**Hermes 审这一步在我这边没法自证** | 本文件；我在 §0 与 WORKER 勾项里显式写了"请先 Hermes 审"，不自审通过 |

## 2. 十个检查项（`m3_verify.py`）

| id | 检查什么 | 钱类 | 红的含义 → 动作 |
|---|---|---|---|
| `fees` | 用 `config.yaml trading.*` **逐笔重算**当天每笔成交的佣金/印花/过户费，与账本存值比 | ✅ | 引擎算错钱 → 回退 |
| `t_plus_one` | 当日建仓必须 `available_volume=0`；可卖量不得大于持仓 | ✅ | T+1 错=违规卖 → 回退 |
| `nav` | 当天恰 1 行、`total_value=cash+market_value` 自洽 | ✅ | 面板断供/净值错 → 回退 |
| `duplicate_fills` | 同 (标的,方向,价,量,分钟) 出现多行 = **两条线都在下单** | ✅ | 双活 → 回退（顺序铁律） |
| `raw_gate` | 复用 T41 的 `flip_watch_with_mother` + `raw_freshness`：新鲜、台账完整、无未翻转 | — | 价不对则账不可能对 → 回退（on_red 里写明） |
| `parity_trace` | 当天 `op_log[parity:diff]` 存在且三 delta 全零、`ok=1` | — | 未归因分歧 → 归因 |
| `audit_trail` | 有成交就必须有当天执行类流水（成交 ⇔ 审计） | — | 无痕 → 归因 |
| `plan_chain` | 当天有 AI 计划行且状态可解释 | — | 停错 cron 半边 → 归因 |
| `cond_chain` | 已触发的条件单当天应有对应成交（否则多是撤单/涨停挡下） | — | 悬空触发 → 归因（可容忍要写清） |
| `heartbeat` | `engine`/`agent` 在**应跳时段**内未超时（复用 `utils/heartbeat.age/due` 与阈值 300s/64800s） | — | 线没跑 → 归因 |

**关键取舍（写进代码注释，不是临场发挥）**：
- **0 笔成交不许报"通过"** → `fees` 在无成交时给 `warn`；
- **缺表一律 `skip` 并说明**，不静默绿（AGENTS 纪律 4）；
- **不在应跳时段就明说"未判停滞"**（`warn`），否则第一个早上全员看到假绿；
- 钱类项一律 `money=True` 且 `on_red="回退"`，非钱类红不改账本判定。

## 3. 真实跑的证据（`spikes/smoke_m3_verify.py`，scratch 库，可重跑）

```
① 一切正常            → 7 绿 / 2 警 / 0 红 / 1 跳   exit=0
   ✅[钱] 成交费用三件套重算：1 笔逐分相符（容差 0.01 元）
   ✅[钱] T+1 冻结正确 / ✅[钱] NAV 恰 1 行且自洽 / ✅[钱] 未双活
   ⚠️ 双轨影子留痕：2026-10-29 没有 parity:diff（影子 cron 没跑，或用了 --no-op-log）
   ⚠️ 心跳：当前不在应跳时段，未判停滞
   ⚪ 条件单触发链：conditional_orders 表不存在
② 把佣金从 18.79 改成 99.0 → ❌[钱] fees：{"delta":{"commission":80.21}} → 红了：回退
   末行印「❗钱类项红 ⇒ 立即回退」                exit=3（不是 1）
③ --money-only        → 只列 4 个钱类项，rollback_required=True      exit=3
④ --json              → {green/warn/red/skipped, rollback_required, money_reds,
                         on_red:{id:动作}, items[...]}（供 cron/告警消费）
```

测试：`tests/test_m3_verify_t46.py` **29 条**（每条红路径都单独断言，包括
"万八佣金必须红"、"卖出漏印花税必须红"、"当日可卖>0 必须红"、
"NAV 缺失/重复/不自洽三种形状分别红"、"双活两行成交必须红"、
"心跳应跳未跳=红、不在时段=警"、"母库有参照却没重跑 ⇒ raw_gate 红"）。

**变异检查**（源码级改回 bug 版，实测；脚本 `spikes/mutation_t46.py`，改完即还原）：

| 变异 | 必红条数（实测） |
|---|---|
| ① 印花税不分买卖方向 | 2 failed / 27 passed |
| ② NAV "当天 0 行"也判绿 | 1 failed / 28 passed |
| ③ 双活阈值 `HAVING n>1` 改成 `n>2` | 1 failed / 28 passed |
| ④ `rollback_required` 写成 `bool(reds)`（非钱类也回退） | 1 failed / 28 passed |
| ⑤ T+1 去掉 `entry == today` 判据 | 1 failed / 28 passed |
| ⑥ 无成交时费用检查报绿 | 1 failed / 28 passed |
| 全部还原 | **29 passed** |

（①杀两条：单独的方向用例 + 汇总用例；其余各杀一条，说明每项都有独立断言、
没有靠同一条用例覆盖两个判据。）

## 4. 安全边界（这个工具会连现网账本）

- **只读**：`sqlite3.connect(...)` 后只跑 `SELECT`；没有任何 INSERT/UPDATE/DDL。
  工具自己建的表也没有——所有表由既有模块建。
- 心跳文件路径可注入（`--heartbeat`），默认走仓库相对 `var/heartbeat.json`；
  在现网跑时建议显式给 `--data-db/--account-db` 绝对路径（清单 §4 的 cron 行已这么写）。
- 交付分支不合并；**按派发先给 Hermes 审**；审的时候最值得盯的三点：
  ① `fees` 用 `fills.direction` 判卖出是否与账本写入约定一致（`-1`）；
  ② `t_plus_one` 依赖 `positions.entry_day`（旧线才有这列，缺列时我不判红，
     新线若不写这列就永远查不出 T+1 问题——这是 G1 的衍生缺口，值得 Hermes 提意见）；
  ③ `raw_gate` 会**读母库**（经 T41 的 `flip_watch_with_mother`），母库不可用时
     它给 `warn`+`incomplete_days`，不是红——这个松紧是否合适请审。

## 5. 与本批其它任务的关系

- T45 runbook §6 的 7 项"当天必须看"在本清单里全部有归属（1.1-1.6 / 2.1-2.9），
  并且工具退出码就是 runbook §5 回退判据的机器表达。
- T42 的 P1（当日跨源对照恒不成立）**不在本工具里判**——它是数据供给层的问题，
  `raw_gate` 只能看到"当日那根还没翻转"（warn），看不到"防线被自家新鲜度闸关掉"。
  等主 AI 定 (a)/(c) 之后，我再把对应的判据补进 `raw_gate`（届时 `money` 属性要重估）。
- T45 §0 的 G1/G2/G3/G4 不关闭，`m3 verify` 上线第一天就会稳定地红 `nav`
  与 `audit_trail`——这不是工具的错，是它把缺口照出来了。
