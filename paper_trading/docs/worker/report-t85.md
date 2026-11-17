# report-t85.md — daily-check 判别力补强（Qoder · 2026-11-12）

派发：Batch 21 · **T85（Qoder，P1）T81 P2 三项收口**——"daily-check 判别力补强
（check_intent_chain 的一致性断言 / carry_over 的未顺延过期意图 / 第三项按 T81
报告）——**小改 + 测试**"。
分支 `worker/t82-t85-signoff`（基于 main@26120c4），**未合**。

## 0. 逐条打勾（对着派发原文，不反推）

| 派发原文 | 对应 T81 残留 | 处置 | 证据 |
|---|---|---|---|
| "check_intent_chain 的一致性断言" | P2-1 名不副实：docstring 承诺"落账笔数与 executed 数一致"，代码只数 `filled` | ✅ 补**逐笔**一致性断言（比原承诺更严：不只比数量，还比标的/方向/数量） | `daily_check.py:141-245`；测试组① 9 条 |
| "carry_over 的未顺延过期意图" | P2-2 `is_carried=False` 的 pending/queued 计入 stale 却仍 green | ✅ 有 stale 未决 ⇒ **warn**（不再绿） | `daily_check.py:269-276`；测试组② 3 条 |
| "第三项按 T81 报告" | P2-3 `check_init_align` 只查"少"不查"多" | ✅ **两个方向都查**：核对侧补多仓红 + 引擎侧 `InitPositionError` 拒开 | `daily_check.py:89-124`、`mod_entry.py:195-207`；测试组③ 3 条 + ③b 4 条 |
| "**小改 + 测试**" | — | ✅ 产品代码 4 文件 **+209/−24**；测试/扫描器 4 文件 +91/−17；新增测试 1 文件 33 条 | 见 §4 |

**附带做了 T81 P2-4/P2-5**（不在派发三项里，但彩排必须用到，顺手收口）：

| T81 残留 | 处置 |
|---|---|
| P2-4 `ENG` 路径硬编码 | `deploy/daily-check.sh:19` 改 `ENG=${PT_ENGINE_PY:-…}` |
| P2-5 用 `cp` 复制正在被写的 `data.db` | 改 **`VACUUM INTO`** 一致快照（源库 `mode=ro`）；快照失败 ⇒ `SC_RC=3`，**不拿半个库跑四场景** |

## 1. 三项补强到底补了什么判别力

### ① `check_intent_chain`：从"数数量"到"逐笔对凭据"

原来：`当日 N 条：{状态分布}；账本当日 filled=M`——**N 与 M 从不比较**，
`executed` 说成交了、账本没有，也照样绿。

现在三条断言（`daily_check.py:141-245`）：

1. **认领成交的意图**：`result.order_id` 必须指向账本**当日 filled** 的那一笔，
   且标的/方向/数量相符 ⇒ 否则**钱类红**。
2. **幂等命中的意图**（`reason` 含"幂等命中"，不带 `order_id`）：当日账本必须
   真有同款成交（`intent_key` 归一）⇒ 否则**钱类红**。
3. **反方向**：当日 filled 里没有任何意图认领的 ⇒ **warn**（条件单/旧线也会成交，
   不能一律当假账，但必须说得出口，不许静默）。

两个实现细节是彩排逼出来的，不是设计时想到的：

- **认领关系要跨日看**：昨天盘后决定、今天顺延执行的意图，`created_at` 是昨天，
  不在"当日新意图"里，但那笔成交确实是它认领的 ⇒ `claimed` 集合扫**全部**
  `status='executed'`，不只扫当日。
- **幂等命中不带 `order_id`**：现网 11-12 的 9 条 executed 里有这种，
  按"无凭据"判会假红 ⇒ 单独走 `idem_keys` 通道。

### ② `check_carry_over`：过期未决不再报健康

`expire_stale` 漏跑（或会话根本没起）时，昨日及以前的 pending/queued 会一直挂着。
原来这一项 green，等于把"没人清理队列"报成健康。现在有 stale ⇒ **warn**，
detail 点名 `biz_id`，`on_red` 指向"查 rq tick 是否跑成、expire_stale 是否被跳过"。

### ③ `check_init_align` + 引擎侧：多仓也拒开

- **引擎侧**（`mod_entry.py:195-207`）：`_check_positions_landed` 现在逐只比
  `framework.quantity > ledger.expected` ⇒ 抛 `InitPositionError`
  "框架账户比账本多 N 只仓…"，并把逐只 `extra` 写进留痕。
  多仓的危害是**按不存在的持仓卖出 / 按虚高市值算风险**，与少仓同为钱类。
- **核对侧**（`daily_check.py:89-124`）：留痕有逐只 `extra` 就用它；
  旧留痕只有计数，则用**同期计数**兜底（见 §2 的假红教训）。

## 2. 彩排照出两个额外缺陷（都已修，其中一个是我自己改出来的）

### 2.1 CI 永久门禁在 main 上是红的（T78 遗留，P0 级影响）

`python tests/ci_gate_selfcheck.py` 在**未改动的 main** 上就失败：
`build_fixture` 把分钟线/日线硬编码在 **11-02/03/04**，而 `checked_dates` 与
`today` 取 `date.today()` ⇒ 过了那几天，`minute_feed`/`raw_gate` 必红。
**门禁自己先红了，就没有资格判别人红**（纪律 4）。

修：

- `recent_trading_days(today, n=3)`（用 `trading_calendar.is_trading_day`，
  不是"减 1/2/3 天"——周末会造出非交易日）；
- 分钟线按 `minute.expected_slots("5")` 造**当日 48 个右边缘槽**，
  不再依赖跑测试的钟点（CI runner 是 UTC，`check_minute_feed` 的
  `in_session` 判据看墙上时钟）；
- 心跳用 `hb.touch(name, path=…)` 写进**临时目录**并显式 `--heartbeat` 传进去
  ——原来会回落到 `resolve_heartbeat_path` 的**仓库根 `var/heartbeat.json`**，
  也就是读开发者本机的真实心跳（本地绿 / CI 红的不确定源）；
- `--heartbeat` 必须放在 `daily-check` **子命令之后**（放全局 `common` 会被
  argparse 当成 command 选择项，报 `invalid choice`）。

**防回归**：`spikes/date_flaky_scan_t53.py` 的扫描范围原来只有 `test_*.py`，
门禁文件叫 `ci_gate_selfcheck.py` ⇒ **扫描器看不见它**。已加 `EXTRA_FILES`，
并加测试 `test_ci_gate_selfcheck_is_scanned_and_clean` 锁住"它在扫描范围内且判 SAFE"。

### 2.2 我自己补的多仓兜底判据会天天假红（已修）

第一版写的是 `fw_n > len(ledger_positions(...))`——**拿会话期初的快照与"此刻"的
账本比**。现网 11-12 当天卖光两只仓，期初 8 只、此刻 6 只 ⇒ 健康的一天被判
**钱类红**"框架持仓 8 只 > 账本 6 只"。门禁成了永久非零的壁纸，比不判更糟。

修成**同期比同期**：

- 多/少仓一律用留痕里同一时刻的 `positions_landed.checked`（期初应有）
  与 `framework`（期初实有）比；
- 当前账本只用来查"期初该有的仓没进框架"，且**豁免当日买成交新建的仓**
  （新增 `today_buys()`，期初留痕里不可能有它们）。

四条同期回归测试锁住：当日卖光 ⇒ 绿；期初就少 ⇒ 红；当日新建仓 ⇒ 绿；
老仓从没进框架 ⇒ 红。

## 3. NEC 彩排（候选树 + 生产库副本，全程不写生产）

环境：候选树 `/home/nec/tmp/pt-t85/tree`（本分支代码），库副本
`/home/nec/tmp/pt-t85/db/{data.db,account.db}`（`VACUUM INTO` 自生产），
`engine.local.yaml`/`strategy.local.yaml` 一并复制（否则 `executor` 解析成
`legacy`，`minute_feed`/`carry_over` 会 skip——第一次彩排就踩了这个）。
**没有复制 `secrets.local.json`**，没有动 crontab，没有跑任何写账本命令。

`deploy/daily-check.sh` 端到端跑通：

```
[daily-check] 2026-11-12 daily_check exit=3
[daily-check] 2026-11-12 scenes exit=0
counts: {'green': 12, 'warn': 1, 'red': 2, 'skipped': 0, 'rollback_required': True}
```

四场景 **A 冻结拒 / B 超支拒 / C 卖成交 / D 买成交 全 GREEN**，
rqalpha 底座守卫（非 rqalpha 拒跑）也绿。

三项补强在**真实生产数据**上的结论：

```
[green] init_align:   框架持仓 8 只/账本 6 只；T+1 冻结留痕 0 条
[green] carry_over:   今日无顺延意图（昨日及以前未决 0 条，过期处理已生效）
[green] intent_chain: 当日 9 条：{'executed': 9}；账本当日 filled=9；
                      executed 逐笔有账本凭据，成交全部有认领
```

两个红 + 一个黄，**都不是我方代码缺陷**，已上报 `questions.md`：

| 项 | 钱类 | 结论 | 归因 |
|---|---|---|---|
| `nav` | **是** | 红 | **Q8**：11-12 唯一一行 NAV 是 09:30 盘中快照（昨日价、早于当日全部成交）。我这次新加的收盘口径断言**报得对**——Q7 之后这件事从"响着的两行"变成"静默的一行错数"。**钱类不自修**（Q7 批复：主 AI 亲做） |
| `raw_gate` | 否 | 红 | **Q9**：母库 raw 参照停在 2026-10-30，正好是 `config.yaml` 股票池全体 10 只。我方 42 只 raw 池 `bar_last=checked_last=2026-11-12`、`unflipped=0` ⇒ **母项目侧停更，跨仓移交** |
| `parity_trace` | 否 | 黄 | 11-12 没有 `parity:diff`（影子 cron 没跑或用了 `--no-op-log`）——观察期已知项 |

其余 12 项绿：`fees`（9 笔逐分相符，容差 0.01 元）、`t_plus_one`（当日建仓 2 只
可卖量=0）、`duplicate_fills`、`minute_feed`（1967 行/41 只，最新 15:00）、
`intent_queue`、`audit_trail`、`plan_chain`、`cond_chain`、`heartbeat`、
`carry_over`、`intent_chain`、`init_align`。

## 4. 测试与门禁

| 跑什么 | 结果 |
|---|---|
| 全量 `pytest`（改完后再跑一遍） | **868 passed / 13 skipped**（179.55s） |
| `tests/test_t85_check_power.py`（新增） | **31 passed / 2 skipped** |
| T85+T80+T53 三件 | **53 passed / 2 skipped** |
| `python tests/ci_gate_selfcheck.py` | `{"items":15,"rc":0,"forced_red_rc":3,"rollback_required":true,"red_ids":["init_align"]}` + **4 项全过** |
| `python tests/wiring_audit.py --quiet` | **断层：无**，exit 0 |

新增测试按缺陷分组（`tests/test_t85_check_power.py`，共 33 条）：
① executed↔账本一致性 9 条 ② 顺延/过期 3 条 ③ init_align 多仓+计数兜底 3 条
③b 同期回归 4 条 ④ NAV 收盘口径 4 条 ⑤ rqalpha 守卫 2 条
⑥ CI fixture 日期/钟点稳健性 8 条（4 个跑测日期 × 4 个钟点参数化）。
2 条 skip 是组⑤（需要 rqalpha bundle），CI 无 bundle 时按设计 skip。

## 5. 残留与移交（不在 T85 范围内，已写进 questions.md）

1. **Q8（P0·钱类）**：NAV 收盘口径。四个独立堵点（盘中写入路径未闸 /
   让位闸被抢占 / rq 侧 D2 与 raw 采集时序不匹配 / 16:30 plan 跳提前返回）。
   三个选项已列，我倾向 ①"`nav_history` 只放收盘口径"。**我不自修**，
   只交付检测器让它每天 15:30 自己响。
2. **Q9（非钱类·跨仓）**：母库核心池 raw 停更 8 个交易日 ⇒ 移交母项目。
3. **两个部署阻塞（主 AI/运维）**：现网 `deploy/daily-check.sh` **没有执行位**
   （`-rw-rw-r--`，cron 直接调用 ⇒ 11-13 15:30 首跑会 permission denied）；
   且部署副本仍是 T85 之前的版本。**T82 明日要读的就是这次首跑**，
   所以这两条必须在明日 15:30 前落地。仓库索引里执行位我已改 `100755`。
4. **文档卫生**：`docs/WORKER.md` Batch 21 末尾粘进了一段 PowerShell 残片
   （含 `git add -A` / 推 main）。**我没有执行它**，请主 AI 删掉。

## 6. 结论

T81 P2 的 1/2/3 三项**全部收口**，附带收口 4/5；判别力从"数数量"升级到
"逐笔对凭据 + 同期比同期 + 收盘口径"。彩排额外照出并修掉两个缺陷
（CI 门禁自身日期/钟点敏感、我自己引入的跨期假红）。
**daily-check 现在能在生产数据上把 Q8 这种"绿着的假账"报成钱类红**——
这是 T85 最有价值的一条：门禁不再只会在坏日子里响，也不再在好日子里乱响。