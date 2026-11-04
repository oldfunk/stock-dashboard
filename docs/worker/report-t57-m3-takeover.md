# T57（接管）M3 切换修复、验收与部署报告（2026-10-30）

> **背景**：`docs/worker/audit-20261030-m3-cutover.md` 照出 2026-10-30 那次
> "M3 切换 COMPLETE" 里有 3 条 P0 + 4 条 P1。用户令：「你直接开干，由你来接管，
> 回头把做了什么提交就行」⇒ 本报告是**接管执行记录**：修了什么、拿什么证据证明、
> 部署动了什么、怎么回退、还有什么要拍板。
> **分支**：`worker/m3-takeover`（未合并——按仓库规矩仍由主 AI review 后合 main）。
> **现网状态**：NEC 工作树已从 `56ae3b1` 切到 `b5e4378`（本分支），**main 引用未动**。

## 0. 一句话结论

三条 P0 都修掉了，并且都在**现网真数据**上跑过验收（不是只在单测里绿）；
两条盘后采集 cron 装上了（此前现网一条采集 cron 都没有）；
`paper_account.db` 在这整段接管里**一个字节都没变**（md5 前后同一值）。

## 1. 逐条核销（审计报告条目 → 修法 → 证据）

| 审计条目（原文要点） | 修法 | 证据（本次实跑，非推断） |
|---|---|---|
| **P0-2** 每跳重放当天 240 根分钟 bar ⇒ 16:49 的决定成交在 09:31 的价 | `_SessionState.decide()` 决策时点门（纯函数）：bar 时刻 < `decided_at` ⇒ 记 `WAITING_DECISION` 不提交；提交时刻写进 `submits` 归因表 | scratch 干净账本（20 万现金、空持仓）造 14:30 的意图 → 成交 `datetime=2026-10-30 14:30:00`、`price=45.04`，而当天 09:35 bar close=45.08（早盘价）——**成交价来自决策时点那根 bar**。变异①退回"永远 go" ⇒ 3 例红 |
| **P0-3** 意图按**标的**批量标 executed ⇒ 3 executed 对 1 笔成交，卖出机会被买单顺带吞掉 | 删掉 `mark_by_symbol`，改为 `queue.resolve_outcomes()`（纯函数）：按 (标的,方向,量) 逐笔认领、一条成交只认领一次、提交未成交 ⇒ `rejected`+框架原因、落账失败 ⇒ 一条都不许多标 | 同一 scratch 跑三条 14:30 意图（买 100 / 买 100 重复 / 无持仓卖 100）⇒ `fills=1 executed=1 rejected=2`，重复那条 `rejected: 同跳同款意图只提交第一笔（去重）`，卖那条 `rejected: NOT_CREATED`。变异②⑫⑬各自退回旧形状 ⇒ 全红 |
| **P1** 无队列/无消费者报警（静默故障） | 新增 `rq_tick` 心跳（消费跳每跳必写，**不进默认阈值表**）、`intent_queue`（executed 无落账 order_id ⇒ **钱类红**；积压 >20min ⇒ 红）、`minute_feed`（盘中无当日 bar ⇒ 红） | 部署后在现网跑 `m3 verify`：`❌ [钱] 意图队列与账本相符：标 executed 却无落账 order_id 的意图 3 条：['000858_BUY_100','002192_BUY_100@…16:45:25','002192_BUY_100@…16:49:24']` ⇒ 退出码 3。**这正是切换日那三笔假账**，报警把它们逐条点出来了。删掉人为加的假行 ⇒ 同一项回绿（反证） |
| **P1** 分钟线没有日内采集（新线撮合没有 bar 可用） | `minute.completed_slots()`（只认"已收完"的槽位）+ `ensure_today_minutes()`（库里已齐就不打接口）；`rq tick` 本跳要用的标的先自证供给；`minute sync --intraday` 可单独装 cron | 现网 scratch：删掉 002192 当日 48 根 → `minute sync --intraday --symbols 002192` → `written=48 latest=2026-10-30T15:00 missing=[]`，心跳 `minute` 落文件。变异⑦（把在走的 bar 也落库）⑧（已齐仍每跳打接口）⇒ 全红 |
| **P1** 涨跌停带 prev_close 降级 / 双活风险 | `rq tick` 读底座：非 `rqalpha` ⇒ **exit 2 拒消费**；底座值非法 ⇒ 报错不静默降级；AI 直接动作在 `rqalpha` 底座下**改投意图队列**（不再走旧撮合），底座读不出来 ⇒ 一条都不下 | 现网 scratch：`executor: legacy` → `engine.executor=legacy，rq tick 拒绝消费（防新旧双活）`；`executor: 想当然` → `exit=2 底座配置非法…收到 '想当然'`；AI 动作 → `已入意图队列（新线执行）`，`orders` 表里该标的 0 笔、`rq_intents` 里 1 条 queued |
| **P1** 当日 raw 日线没采（周一开盘 T50 闸会全线挡） | 装 cron `40 15 * * 1-5 … data fetch-raw --symbols $(本地 stock_pool) --days 560`；并当场补齐 2026-10-30 | 补前 `daily_bars_raw` 最新日=10-29（38 只全缺 10-30）；补后 `2026-10-30|38 行`、`checked=38`（逐日闸 `incomplete_days=0 audit_complete=true`）。另装 `50 15 * * 1-5 … minute sync --retention-days 25` |

**纪律自查**：钱类默认仍全部关闭（`rq day` 不带 `--ledger` 不落账、影子默认留痕但不改账本数字）；
本次现网**没有**跑任何写账本的命令（`paper_account.db` md5 前后同值
`9528464932e8650546d17b9976261868`，`op_log` 仍 349 行、`orders` 仍 13 行）——
落账/验收全部在 `/home/nec/tmp/pt-takeover` 的**库副本**上做。

## 2. 部署清单（每一步都可回退）

| 动作 | 细节 | 回退 |
|---|---|---|
| 代码切分支 | `git fetch origin worker/m3-takeover:worker/m3-takeover && git checkout worker/m3-takeover`；`56ae3b1 → b5e4378`。**main 未被移动、未合并、未强推** | `cd /home/nec/paper-trading && git checkout 56ae3b1`（原 HEAD 存在 `/home/nec/paper-trading/pre-m3-takeover-head.txt`） |
| crontab 增两行 | 以备份为底本追加（`40 15` fetch-raw、`50 15` minute sync），原有 7 行逐字未动，`rq tick` 行保留 | `crontab /home/nec/crontab.pre-m3-takeover-20261030-183811.bak` |
| 补 2026-10-30 raw 日线 | `data fetch-raw --symbols <本地 stock_pool 38 只> --start 2026-10-26 --end 2026-10-30`（幂等 upsert，只动 `data.db`） | 该命令是 upsert；如需回退可按 `source/timestamp` 删除本次新增行（未做，保留数据） |
| `engine.local.yaml` | **本次没改**（`executor: rqalpha` 是切换日就有的） | 一行改 `legacy` |

部署后现网自检：`venv/bin/python -m pytest -q tests/` ⇒ **647 passed, 9 skipped**（与本地同）。

## 3. 现网验收还照出两处我自己的残留（已修）

这一段如实记录，因为它们是"单测绿、现网错"的形状：

1. **会话内去重冒充成交**：`_SessionState` 把"账本当日已成交的 key"和"本次会话
   已提交的 key"混成同一个 `executed_ids` ⇒ 同跳的第二条同款意图被记成
   `SKIPPED_EXECUTED`，归因表据此标 executed，而账本里没有它的成交——
   **`executed=3 / fills=1` 在我的第一版修复后仍然成立**。现拆成
   `_ledger_keys`（真幂等，可标 executed）与 `_submitted_keys`（去重，记
   `SKIPPED_DUPLICATE` ⇒ 只能判 rejected）。
2. **一条意图多根 bar 反复定局**：日内续跑重放 240 根，同一 biz_id 先记真状态、
   后记 SKIPPED_*，`by_biz` 取最后一条 ⇒ 真状态被洗白。加 `_handled` 闸
   （一条意图一个会话只定一次局）+ `by_biz` 真状态优先。
3. 顺带：`waiting` 原始累积表把已定局的意图报成"还在等"（实测 `waiting_decision=3`
   而三条都已定局）⇒ 收进 `_SessionState.final_waiting()`。

补了 3 条**假 `rqalpha.api` 桩**的会话级单测（本地不装 rqalpha 也能跑 `handle_bar`），
并为每条各挂一条变异（⑫⑬）。教训写进 `questions.md`：会话侧的定局语义必须有
可注入的假 API 层，否则这类 bug 只有现网跑得出来。

## 4. 变异检查（退回旧行为，不是改数字）

`spikes/mutation_m3_takeover.py`：**13 条变异 13 条被捕获**，基线绿。
逐条对应"切换日现网真实发生过的那一幕"（重放早盘价、按标的批量标、
落账失败照标、静默吞单、跨日补执行、LIKE 去重死、半根 bar 入库、
打爆行情源、旧撮合并存、假红默认表、供给不查、去重冒充成交、反复定局）。

## 5. 请拍板（我没有自行处理的）

1. **切换日那三笔假账**（`rq_intents` 里 3 条 `executed` 无对应成交）：报警已能逐条点名，
   但**修正现网历史行属于写账本**，我没动。要么按 §5.1 回退演练一并处理，
   要么给一条 `ledger` 子命令做带留痕的更正（`op_log` 记旧值/新值/依据）。
2. **要不要保留 `rq tick` 的 5 分钟消费 cron 之外的盘中采集 cron**：现在
   `rq tick` 自证供给（只拉本跳要用的标的），我又装了 15:50 的全池补齐。
   如果希望整池日内都齐（面板/回测受益、行情源压力↑），再加
   `4-59/5 9-15 … minute sync --intraday --symbols $(本地池)`。
3. **影子对账 cron（runbook #7）没装**：切换后是否继续双轨是主 AI 的决定；
   现在 `m3 verify` 的 `parity:diff` 项因此持续报警（黄色，不是钱类红）。
4. **`worker/m3-takeover` 的 review/合并**：现网跑的是**未合并分支**。
   review 通过后合 main；若 review 认为不该上现网，回退命令见 §2。
5. 周一开盘前的 `m3 verify` 需要一次钱类全绿：现在唯一的红就是第 1 条那三笔历史假账。
