# report-t54 · `m3 verify` 生产预跑（Qoder，P1·小）

分支 `worker/t54`（基于 `origin/main@901bee6`，**待 review 未合并**）。
本地全套 **599 passed / 9 skipped**（基线 587 ⇒ 本任务净增 12 条，零回归）。
NEC 现网以**当前旧线状态**跑通工具，并修掉预跑照出的 **4 个工具缺陷**（其中 3 个会造成假红）。

## 0. 一句话结论

工具在生产环境可用：现网数据副本、生产 venv、**734 ms** 跑完十项，
修完之后 **红 0 / exit 0**；上一交易日（2026-10-29，已收盘）**8 绿 / 2 警 / 0 红**。
但预跑当场照出三条"盘中假红"——**切换日第一小时清单正是在盘中跑**，
不修的话第一条就会指挥操作者"立即回退"。已全部修掉并各配反向测试。

## 1. 怎么跑的（红线：不写现网）

| 步骤 | 做法 |
|---|---|
| 代码 | `git bundle`（`worker/t54`）+ scp 到 `/home/nec/tmp/t54.bundle`；在 `/home/nec/tmp/pt-t54b` 用 `git clone --no-hardlinks` 出 scratch 树再 `git fetch <bundle>` ⇒ **不碰生产 `.git`** |
| 数据 | `sqlite3 .backup`（只读 URI 打开）把现网 `data.db`/`paper_account.db` 复制成 `/home/nec/tmp/pt-t54/*.db`，工具只吃副本 |
| 环境 | 生产 venv `/home/nec/paper-trading/venv/bin/python`（**未装 rqalpha**，工具本就不需要） |
| 现网未被动过 | 工具内**零写 SQL**（`m3_verify.py` 409 行 grep `INSERT|UPDATE|DELETE|DROP|CREATE` 无命中）+ 本地有一条断言"跑完全套账户库字节不变"（§3#4）；跑前跑后都记了 sha1。⚠️ 诚实说明：两次 sha1 之间现网库本身在变（engine cron 每分钟一跳），所以 sha 差**不能**当"我没写"的证据，真正的证据是那两条 |

## 2. 预跑照出的四个工具缺陷（都已修）

跑前基线：`M3 验证（2026-10-30）绿 5 / 警 3 / 红 2 / 跳 0` + `❗钱类项红 ⇒ 立即回退`，exit=3。
而那条线根本没坏（engine 心跳 6 秒前刚跳）。

| # | 缺陷 | 为什么会误报 | 修法 | 反向断言（不许把闸门卸掉） |
|---|---|---|---|---|
| 1 | `check_nav` 盘中把"今天没有 NAV 行"判成**钱类红** | 旧线收盘 NAV 由 16-18 点 `agent tick` 收盘窗口写（crontab `*/5 16-18 * * 1-5`），14:35 查必然没有 | `check_nav(..., now=)`：`today==真实今天` 且 `< 18:05` ⇒ warn 并写明"收盘窗口未过 + 当前时刻" | 过了 18:05 仍无行 ⇒ 照旧红；查**历史日**缺失 ⇒ 中午查也红 |
| 2 | `check_parity_trace` 未到影子时点就指控"影子 cron 没跑" | 影子管道 runbook §3 是 15:40；14:35 前当然没有 | 未到 15:45 ⇒ 文案改"时点未到"；`now` 可注入 | 过点/历史日保持原指控文案；delta 非零仍红 |
| 3 | `check_heartbeat` 读不到文件时报**红**"应跳未跳 ['engine']" | `heartbeat.default_path()` 返回**相对** `var/heartbeat.json`（docstring 假设 cwd 就是仓库根）；操作者从 `~`/scratch 跑 ⇒ `load()` 缺文件回退空 dict ⇒ ages 全 None ⇒ 判停滞。实测：同一条命令加 `--heartbeat /home/nec/paper-trading/var/heartbeat.json` 立刻绿 `engine=6s` | 新增 `resolve_heartbeat_path()`：显式 `--heartbeat` ＞ `cwd/var/` ＞ **仓库根 `var/`**；都没有 ⇒ **skip** 并列出试过的路径（"没证据"≠"线挂了"），detail 里写出用的哪个文件 | 显式路径存在时仍照判；`cwd/root` 可注入，三条分支各有测试 |
| 4 | 人读输出不写"池从哪来" | 从 `~` 跑时 cwd 无 `config.yaml` ⇒ 池解为空 ⇒ `raw_gate` 报"股票池为空"，看不见来源就会被读成"没什么要查的" | `format_text` 头部加 `｜池 <pool_source>` | 新测试断言头一行必须出现来源 |

## 3. 修完后的现网复跑（贴原文）

**F) 仓库根等价环境（cwd 有 `config.yaml` + `var/heartbeat.json`）**

```
M3 验证（2026-10-30｜池 config）  绿 6 / 警 4 / 红 0 / 跳 0
⚠️  [钱] 成交费用三件套重算：当天没有成交可验（0 笔）
⚠️  [钱] NAV 收盘记录：2026-10-30 尚无 NAV 行——收盘窗口未过（18:05 之后才该有），当前 14:47
⚠️  [ ] 行情口径与新鲜度闸：waiting_days=33 incomplete_days=0 complete=True
⚠️  [ ] 双轨影子留痕连续：2026-10-30 尚无 parity:diff——影子时点未到（15:45 之后才该有），当前 14:47
✅ [钱] T+1 冻结正确：当日建仓 0 只均可卖量=0（entry_day 缺失时按旧线不判）
✅ [钱] 旧新双线未双活：无重复成交
✅ [ ] 成交⇔审计流水齐备：成交 0 笔；流水 [{"ai:decide": 6}]
✅ [ ] AI 计划执行链：[{"plan_date": "2026-10-30", "status": "pending", "n": 1}]
✅ [ ] 条件单触发链：[{"status": "armed", "n": 9}, {"status": "cancelled", "n": 7}, {"status": "expired", "n": 24}]
✅ [ ] 引擎/代理心跳：engine=57s（应跳） agent=无记录（不在时段）｜文件 /home/nec/tmp/t54run/var/heartbeat.json
exit=0
耗时 734 ms（--json 全套）
```

**G) 上一交易日 2026-10-29（已收盘语义）**：`绿 8 / 警 2 / 红 0`，
其中 `NAV 收盘记录：2026-10-29 恰 1 行且自洽；库里共 12 个净值日` ✅、
`双轨影子留痕连续：1 行，delta 全零` ✅ ⇒ 这两项在"该有时"确实会绿，放宽没卸闸门。

**A) 从 `$HOME` 跑（旧实现假红的那条路径）**：现在是
`绿 5 / 警 3 / 红 0 / 跳 2`，两条 skip 的 detail 明说
`找不到心跳文件（试过：/home/nec/var/heartbeat.json; /home/nec/tmp/pt-t54b/var/heartbeat.json）——请用 --heartbeat 指定，或在仓库根运行`，
exit=0 ⇒ 不会再指挥人回退。

**D) `--money-only` / `--json`**：money-only 只列 4 项钱类 ✅；
`--json` 可被 `json.load` 解析并给出 `items=10 red=0 money_reds=[] rollback=False` ✅。

## 4. 预跑顺带照出的两件**运维事实**（不改，交给主 AI；我不碰 cron）

1. **影子 cron 没装**：`crontab -l | grep -c "shadow-day"` = **0**，
   而 runbook §3 第 3 条要求影子管道（采集 → `rq shadow-day` → gate，15:40）在影子期每天跑；
   现在 10-29 那条 `parity:diff` 是手工跑出来的。
   ⇒ 切换日第一周的"新线是否漂移"唯一外部对照就会断档。要么装上，要么在 §3 明确"手工日跑"。
2. **现网 engine cron 是 `* 9-15 * * 1-5`（每分钟一跳，到 15:59）**，
   而 `heartbeat` 的 `AGENT_DUE` 从 20:30 才开始追问 agent——
   今天 14:47 那次报 `agent=无记录（不在时段）` 是对的（9:30-9:55 的 tick 窗口已过、
   16-18 点窗口未到）。只是提醒：切换日看板上"agent 无记录"不等于没跑。

## 5. 交付物

| 文件 | 作用 |
|---|---|
| `paper_trading/utils/m3_verify.py` | 四条修复：`check_nav(now=)`、`check_parity_trace(now=)`、`resolve_heartbeat_path()` + `check_heartbeat(cwd=,root=)`、`format_text` 写池来源；`verify_all` 透传 `now` |
| `paper_trading/cli.py` | `m3 verify --heartbeat` 的 help 说清解析顺序 |
| `tests/test_m3_verify_t54.py`（新，12 条） | 每条放宽都配"过点必须仍红"的反向断言；心跳三分支；工具只读断言；池来源格式断言 |
| `docs/worker/report-t54.md` | 本文件（预跑命令、原文输出、缺陷清单、两件运维事实） |

## 6. 我没有做的（划清边界）

- 没装/没改任何 cron（红线）；没碰 Pi；没改 `config.yaml`；没写现网账本或行情库。
- 没在现网目录里跑 `m3 verify`——现网 cwd 下的 `var/heartbeat.json` 只被读，
  其余全部在 scratch 副本上跑。
- 切换日 `m3 verify` 若再报"钱类红"，那是真红（本任务把三条假红拿掉了）。
