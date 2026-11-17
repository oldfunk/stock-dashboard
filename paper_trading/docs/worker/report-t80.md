# report-t80.md — 每日核对自动化 `m3 daily-check`（Qoder · 2026-11-05）

派发：Batch 20 · T80（P1·M4-②）——"T77 的核对项做成
`cli m3 daily-check`（只读：002192 顺延执行结果 / init 对齐 / 新意图全链 /
m3 verify 全项）+ 每日 cron 或手动清单"。分支 `worker/t75-t80-wrapup`，未合。

## 1. 一条命令 + 一把退出码

```
python -m paper_trading.cli m3 daily-check [--json] [--today D]
        [--state-dir var/rq_state/live] [--expect-work yes|no|auto]
```

退出码与 `m3 verify` **同一把尺子**：`0` 无红 / `1` 有红 / `3` 钱类红（§5.6 回退评估线）。
这样 cron、CI、人都读同一个结论，而不是各自解释截图——M4 验收口径①
（"连续 5 个交易日无未归因异常"）要的正是这张表。

内容 = `m3 verify` 的 12 项（钱/审计/链路）+ 本模块 3 项 rq 执行面：

| id | 判什么 | 红了往哪查 |
|---|---|---|
| `init_align` | 会话留痕 `init_align.kept` 是否覆盖账本全部持仓；有 `dropped_silently` 直接红；该有留痕却没有也红 | 形态/解析层又变了（report-t73 §2）；或先停 cron 走 `ledger reset-state --only rq_session_state` |
| `carry_over` | Q6 顺延过来的意图本跳有没有给出成交/拒单结论（顺延不兑现=漏环节） | 会话是否跑成 |
| `intent_chain` | 当日新意图状态分布 + 账本当日 filled 数对得上 | 逐笔归因表 |

三条都是只读（`file:...?mode=ro`），不写 `op_log`、不碰状态目录。
`--expect-work no`（队列本来就空、今天没开会话）时 `init_align` 判 **skip 不判红**——
报警本身不许失真，这是 T61/Q5 同一条纪律。

## 2. 现网只读实跑（这就是 T77 明早要看的那张表的今天版本）

```
m3 daily-check --today 2026-11-05 --state-dir /home/nec/paper-trading/var/rq_state/live
→ green 5 / warn 4 / red 4 / skip 2，rollback_required=True，退出码 3
paper_account.db 与 data.db 的 md5 前后同值（d3d98da6…）
```

红项与它们的含义：

- `init_align` 红：账本 7 只仓而**现网跑的是 main `7cd31a5`（T73 修复尚未部署）**，
  15:52 那份 `live/evidence` 里根本没有 `init_align` 留痕 ⇒ 缺仓照红。
  这条正好证明：**部署 + 挪开 `live` 状态**之后它才会转绿，工具不撒谎。
- `intent_queue` 红：`002192_SELL_100@15:09:20` 已积压 456 分钟——Batch 17 等"明天开盘"
  的那条；Q6 落地后它顺延到 11-06，本项会跟着变成"有结论"。
- `nav` 红：同日两行 NAV（`rq-nav` 09:32 + `nav:close` 16:30）。
  这是本轮新发现的**钱类缺陷，已写 questions.md Q7**（含"为什么不能只修一半"）。
  工具把它抓出来了，就是每日核对要的价值。
- `heartbeat` 红：`agent` 应跳未跳（阈值 18h，收盘窗口的 cron 近两天没跑成）——
  属既有项，交由主 AI 判断是否恢复。

黄项里 `raw_gate` 现在是"**母库参照滞后 3 个交易日（×10 只）**"这种带阈值的说法
（Q5 落地），不再是长期挂着的一条 `waiting_days=30`。

## 3. cron 草稿（我没有装，crontab 一行都没碰）

```
30 15 * * 1-5 /home/nec/paper-trading/deploy/daily-check.sh >> /home/nec/paper-trading/logs/daily_check.log 2>&1
```

`deploy/daily-check.sh` 干三件事：跑真 CLI、把结论压成**一行 JSONL**
（`logs/daily_check.jsonl`，含 `exit_code`，供 T79 的观察报告按天聚合）、
按 0/1/3 退出。它自己也是只读的。

## 4. 测试（`tests/test_daily_check_t80.py`，14 例）

每条都同时测绿与红——核对工具最容易犯的错就是"永远绿"：
留痕缺失（红 / skip / 队列空的三种情形）、被吞仓、账本有仓未进框架、
北交所剔除不误报、顺延未定局 vs 已成交、当日意图未定局 vs 全部定局 vs 当日无新意图、
汇总计数与 items 一致、钱类红 ⇒ `rollback_required`。

全量回归：836 passed / 11 skipped；`wiring_audit` 退出码 0。
