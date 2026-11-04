# report-t40 · 生产 `daily_bars_raw` 回填（Qoder，P0·Batch 9 批复 1，依赖 T41）

分支 `worker/t40`（**待 review，未合并**），代码基线含 `worker/t41`（回填必须用修好窗口的管道，
否则写进现网的还是"每批 1/3 无从验料"的数据——见 §5 说明）。
完成态判据按派发原文：**`data raw-coverage --gate` 全绿** ⇒ 已达成（§3）。

## 0. 一句话结论

现网 `data.db` 的 `daily_bars_raw` 从 **0 行** 变成
**28,339 行 / 38 只 / 2023-09-25…2026-10-29**，其中 **28,264 行（99.74%）过了独立源同日对照**，
**拒收 0**；`--gate` 退出码 0、`unflipped_days=0`、`audit_complete=true`。
其余 37 行是**母库自己还没出到那些天**（`waiting_reference`，告警不停线，T+1 会翻）。
前后指纹核对：`daily_bars`（前复权）与 `minute_bars` **一个字节都没动**；
现网 `paper_account.db` 里 `parity:diff`/`data:migrate` 仍是 **0 行**（账本红线不变）。
回填后立刻用现网真数据重跑影子对账：**三笔真实成交、现金与费用两侧完全一致
（743,559.3428 / 66.6572，差值全 0）**。

## 1. 执行顺序（先干跑再动现网，且动手前留了冷备）

1. **母库参照深度先探底**（决定回填后还能不能全绿）：母库 `kline_daily`
   全库 10,548,954 行 / 5,193 只 / **2016-01-04…2026-10-29**，
   抽查 600036/000858/300750/605099 各自 1,470–2,625 行且 `close_raw` 全部有值
   ⇒ 三年窗口的参照是齐的（T41 的 `since` 生效前提）。
2. **干跑**：`sqlite3 .backup` 把现网库复制成 `/home/nec/tmp/t40_dry38.db`，
   在同一份参数（`--days 560`、全池 38 只）下跑一遍：
   `written=28,339 / checked=28,264 / no_reference=37 / 拒收 0 / unflipped=0 / gate exit 0`，耗时 35 s。
   ——先干跑是因为回填要动现网行情库：参数错了在副本上浪费 35 秒，比在现网上试错便宜。
3. **冷备**：`/home/nec/paper-trading/data.db.pre-t40-20261029-181759.bak`（7,380,992 B，
   sqlite backup API，不是 cp，避免与 cron 的写撞在半途）。
4. **现网回填**：同一命令、同一参数指向 `/home/nec/paper-trading/data.db`，44 s。
5. 事后指纹核对 + `--gate` + 影子对账重跑（§3、§4）。

## 2. 回填结果（现网）

```
请求 38 只 / written=28339  checked=28264  no_reference=37  拒收=0  非 ok 标的=[]
每只行数 737…746（最少年份是新股 605099/002128，属正常）
口径标签分布: [('raw', 28339)]          ← 全部带 raw 标签，没有一行混进 qfq
库内范围: 2023-09-25 … 2026-10-29 / 38 只   （现网 daily_bars 是 2023-10-09 起 ⇒ raw 略深，覆盖对齐）
flip_watch: stop_line=False  unflipped_days=0  waiting_days=37  no_reference_days=0  incomplete_days=0
```

**"缺口如实报告"**（派发原文要求）：

- 37 行未验料全部是**近端**的 `waiting_reference`，例如样例
  `000333: bar_last=2026-10-29, checked_last=2026-10-23, ref_asof=2026-10-23,
  waiting=[10-26, 10-27, 10-28]`——**母库对深市也只更到 10-23**（T39 报告里"母库滞后"的那件事，
  现在范围更大：18 只标的在等）。这不是我们的漏验，是参照源自己没到；
  T+1 起效路径是"母库补上 → 下一次 `fetch-raw` 重照 → checked 翻转"，
  没翻就会由 T41 的逐日闸报 `checked_not_flipped` 停线。
- 18 只在等：`000333 000651 000858 002415 002594 300750 600036 600276 600285 600519
  600887 600900 601012 601318 601398 601888 601899 603259`；另外 20 只母库已到 10-27/10-29。
- **`--days 560` 的真实含义**：窗口 = `today - (days*2+10)` 自然日 = 2023-09-25。
  这个参数含糊（T41 报告 §6 提过），本次按"覆盖 742 交易日"倒推取 560；
  若 T42/正式 cron 要精确对齐深度，建议加显式 `--start/--end`，等主 AI 定。

## 3. `--gate` 完成态（派发判据）

```
$ python -m paper_trading.cli --data-db /home/nec/paper-trading/data.db \
      data raw-coverage --gate --days 560 --symbols <38 只> --json
gate 退出码 = 0
audit = {since: 2023-09-25, audited_days: 28339, verified_days: 28264,
         unflipped_days: 0, waiting_days: 37, no_reference_days: 0,
         incomplete_days: 0, complete: true}
violations = []
```

**前后指纹核对**（证明只动了 `daily_bars_raw`）：

| 项 | 动手前 | 回填后 |
|---|---|---|
| `daily_bars` 行数 | 27,951 | 27,951 |
| `SUM(volume)` / `SUM(close)` | 1,334,451,068,853 / 2,089,804.06 | **同上，逐位一致** |
| `minute_bars` 行数 | 2,304 | 2,304 |
| `daily_bars_raw` 行数 | **0** | 28,339 |
| 现网 `op_log` 的 `parity:diff` / `data:migrate` | 0 / 0 | 0 / 0 |

现网账本最近三条流水是 `ai:plan 17:33`、`nav:close 17:32`、`ai:event 10:46`
（都是它自己的 cron 写的，不是我）——用来证明我在账本侧确实没落笔。

## 4. 回填后的影子对账重跑（派发第 3 项）

现网真数据、三个板块的标的、真实收盘价（`--today 2026-10-29`，2 s）：

```
plan: 买 000651 2000 股 / 买 300750 200 股（创业板 20% 带）/ 买 600519 100 股 / 卖 601318 100 股
rqalpha 现金 743,559.3428   旧引擎现金 743,559.3428
rqalpha 费用     66.6572    旧引擎费用     66.6572
diff: cash_delta=0.0  fee_delta=0.0  fill_delta=0  violations=[]  ok=true
成交 3 笔（两侧一致）：000651 @37.58 cost 19.5416 / 300750 @229.07 cost 11.9116 / 600519 @1354.0 cost 35.204
拒单 1 笔：601318 "仓位不足，目标平仓量为 100，可平仓量为 0"（旧引擎侧只有 "see_logger"，G-T13-8 的已知缺口）
evidence.cost: wrapped=["CS"] applied=12 added_total=10.25496
gates: ok=true errors=[] event_counts{BEFORE_TRADING 1, AUCTION 1, BAR 1, AFTER_TRADING 1, PRE_SETTLEMENT 1, SETTLEMENT 1, POST_SETTLEMENT 1}
```

逐笔手算复核（佣金万 2.5 + 过户费 0.001%，无最低佣金干扰）：
`75,160×0.00025=18.79 + 0.7516 = 19.5416`、`45,814×0.00025=11.4535 + 0.45814 = 11.9116`、
`135,400×0.00025=33.85 + 1.354 = 35.204`；合计 `66.6572` ✓ 与两侧一致。

**留痕去向**：这次仍写 **scratch 账户库**（`/home/nec/tmp/t40_shadow_acct.db`，
里面 1 行 `parity:diff` 已验证），现网 `paper_account.db` 保持 0 行——
"首个现网 `parity:diff`"是 T42 的验收项，不该在回填任务里顺手掉。

## 5. 需要主 AI 知道的两件事

1. **本次用的是 `worker/t41` 的管道**（未 review）。理由：main 上的采集窗口仍比参照窗口宽，
   直接回填会把"每批 1/3 无从验料"的数据固化进现网。
   风险评估：写入的表是 `daily_bars_raw`（原本是空的，不覆盖任何既有值），
   且 fail-closed 的口径闸/对照闸是 T31 就在主线里的；`daily_bars`/`minute_bars` 指纹逐位未变。
   如果你 review T41 后改了窗口语义，重跑一次 `fetch-raw` 即可（幂等 upsert，不产生重复行）。
2. **`waiting_reference` 需要一次 T+1 复跑才能翻**：母库补齐 10-26…10-29 之后，
   下一次 `fetch-raw`（同一 `--days`）才会把这 37 行标成 `checked=1`；
   T42 的"全链 ≥15:35 → shadow-day → gate"就是这个动作的日常形态。
   现在这份库里最"年轻"的未验料是 2026-10-26，都在 `--days 560` 的审计窗口内，
   所以母库一旦补上、而我们没重跑，闸会停线（这是 T41 的设计，不是隐患）。

## 6. 环境与红线

- 只写了两处：现网 `data.db` 的 `daily_bars_raw`（批复 1 明确授权的行情库）
  和我自己的 scratch（`/home/nec/tmp/*`、备份文件）。
- **没写现网账本**：`--account-db` 全程指到 scratch；`paper_account.db` 前后指纹一致
  （`parity:diff`/`data:migrate` 均 0 行）。没跑 `agent run/tick/fire`、`buy/sell`、
  `corporate apply`、面板 POST；没碰 Pi/secrets/cron/tag；没 force push；`config.yaml` 只读。
- 采集时刻 18:17（北京）在 cron 的 `agent tick` 活跃段（16:00–18:55）之内，
  全程没有撞到 `SQLITE_BUSY`；顺带发现 `DataDBManager._connect()` 没设 `busy_timeout`
  （并发写会直接报错而不是等）——不属 T40 范围，记在这里供你决定要不要派一件小修。
- 复算材料都在 NEC：`/home/nec/t40_prod.json`、`/home/nec/t40_prod_gate.json`、
  `/home/nec/t40_dry38.json`、`/home/nec/t40_shadow.json`、
  `/home/nec/paper-trading/data.db.pre-t40-20261029-181759.bak`，代码副本在 `/home/nec/tmp/pt-t41`。
