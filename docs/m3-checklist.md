# M3 切换后检查清单（T46，2026-10-29）

配套：`docs/migration-m3.md`（切换 runbook）、`python -m paper_trading.cli m3 verify`（可自动化项）。
**判定规矩**（§5.6 底线，逐字）：「任何一项红 ⇒ 立即回退并归因」——本清单里
**标 `钱` 的项红 = 立即回退**（`m3 verify` 用退出码 **3** 表达），
非钱类项红 = 退出码 **1**（先归因，不自动回退）。

图例：`✅/❌/⚠️/⚪` = 绿/红/警/跳过；`[钱]` = 钱类项；`自动` = `m3 verify` 覆盖；
`人工` = 工具判不了，必须有人看。

## 1. 第一小时（切换后立刻，15:00 收盘线之前每 15 分钟一遍）

| # | 项 | 类型 | 怎么跑 | 期望 | 红了怎么办 |
|---|---|---|---|---|---|
| 1.1 | **没有双活** | `钱` 自动 | `m3 verify --money-only`（`duplicate_fills`） | 无同一 (标的,方向,价,量,分钟) 多行成交 | **立即回退**（runbook §5.1）；先查 Pi 侧旧 cron 是否还活着（见 1.2） |
| 1.2 | 旧执行线真的停了 | `钱` 人工 | `pgrep -af paper_trading.engine`；NEC `crontab -l` 与 runbook §3 逐条对照；**再 ssh 到 Pi 看它的 crontab** | 规则引擎/开盘执行两条已注释；Pi 上没有残留行 | **立即回退**（双活一秒就可能重复下单——`migration-nec.md:75-76` 原文铁律） |
| 1.3 | 首单落账 | `钱` 人工 | 看当天第一笔 `orders`/`fills` 与 `op_log` 的 action | 成交笔数=计划笔数，费用字段齐（commission/stamp_duty/transfer_fee 都有值） | **立即回退**（G1 的账本写入路径没接通就是这个问题） |
| 1.4 | 行情闸 | 自动 | `m3 verify`（`raw_gate`，T41 逐日台账） | 全部新鲜、`unflipped_days=0`、`audit_complete=true` | 归因：数据不对 ⇒ 不许下单；若持续红按 §5.1 回退 |
| 1.5 | 面板在跑 | 人工 | `systemctl is-active paper-trading-dashboard`（或 `paper-dashboard.service`）、浏览器开 8080 | 面板打开、当日持仓/现金可见 | 归因（unit 名两个都试：`AGENTS.md:38`）；不影响账本 |
| 1.6 | 心跳 | 自动 | `m3 verify`（`heartbeat`） | 应跳时段内 `engine` 跳动 < 300s | 归因（cron 没装/锁被占）；非钱类不回退 |

## 2. 第一天（收盘后 + 次一交易日开盘后）

| # | 项 | 类型 | 怎么跑 | 期望 | 红了怎么办 |
|---|---|---|---|---|---|
| 2.1 | **费用三件套逐笔相符** | `钱` 自动 | `m3 verify`（`fees`，按 `config.yaml trading.*` 重算） | 佣金 `max(额×万2.5, 5)`、印花税只在卖出、过户费双向，逐分相同 | **立即回退**（T38 前就是差在过户费与万八佣金） |
| 2.2 | **T+1 冻结正确** | `钱` 自动 | `m3 verify`（`t_plus_one`）；次日开盘再看一次同一 SQL | 当日建仓 `available_volume=0`；次一交易日 `before_trading` 后放开 | **立即回退**（T+1 错 = 可卖量虚高 = 违规卖） |
| 2.3 | **NAV 收盘记录** | `钱` 自动 | `m3 verify`（`nav`） | 当日恰 1 行、`total=cash+市值`；面板净值曲线不断档 | **立即回退**（G2 没有记录器时这条必红——先别切） |
| 2.4 | 成交⇔审计齐备 | 自动 | `m3 verify`（`audit_trail`） | 每笔成交能追到当天一条执行类流水 | 归因；补 `op_log` 说明（无痕=不可审计） |
| 2.5 | 拒单原因可读 | 人工 | 造/等一笔拒单（如无持仓卖出），看 `evidence.rejects` 与账本侧 | 中文原因（不是堆栈、不是 `see_logger`） | 归因（G-T13-8 要求两侧都有原因） |
| 2.6 | 影子对账留痕 | 自动 | `m3 verify`（`parity_trace`） | 当天有 `parity:diff` 且三个 delta 全零 | 归因（未归因分歧不许继续切）；`ok=0`/delta 非零 = 红 |
| 2.7 | AI 计划链未断 | 自动 | `m3 verify`（`plan_chain`） | 当天有计划行且状态可解释（executed/done） | 归因——**注意 `agent tick` 一条命令同时管做计划与执行**（runbook §3 #2），停错半边就表现为这条红 |
| 2.8 | 条件单链 | 自动 | `m3 verify`（`cond_chain`） | 无"已触发但当天无成交"的悬空单 | 归因（多为撤单/涨停挡下，写清即可容忍） |
| 2.9 | 锁不互相挡 | 人工 | `ls -la var/*.lock`；`paper_trading.lock` / `paper_trading.event.lock` / 新线 `var/rq.lock` | 三把锁各自独立，无残留（`run_lock` 会接管死进程残留锁，`AGENTS.md:72`） | 归因；不要让新线共用旧线锁（一天一进程 vs 每分钟一跳） |

## 3. 第一周（观察期，每天一次）

| # | 项 | 类型 | 怎么跑 | 期望 | 红了怎么办 |
|---|---|---|---|---|---|
| 3.1 | 双轨零未归因分歧 | `钱` 自动 | 逐日 `m3 verify` 汇总（`parity_trace`） | 每交易日 1 行 `parity:diff`，delta 全零或已写归因 | **立即回退**（M2 验收单 §5.4-3 的同一判据） |
| 3.2 | 现金/持仓与归档账无穿越 | `钱` 人工 | 新账 `account.cash` 只应等于新账自己的成交序列；旧账（`chmod 444` 归档）行数不变 | 两本账零交叉 | **立即回退** + 检查是否有人把归档账又拿去跑写命令 |
| 3.3 | 旧模块只读封存 | 人工 | `git log` + 部署确认：`engine/`、`agent/` 执行路径未被新线写复用 | 一个版本周期内不动它们（计划 M3 行原文） | 归因 |
| 3.4 | 复权因子/跨除权日 | `钱` 人工 | `rq day` 的 `evidence` + bundle 日期：月度快照月中"日线不完整"是预期（report-t39 §4） | 出现除权日时因子已更新（`factor_freshness_ok` 判 ok=True） | **立即回退/停当日下单**（因子旧 = 换算系统性错价，外推不是降级） |
| 3.5 | 逐日台账无长期未翻转 | 自动 | `m3 verify`（`raw_gate`）/ `data raw-coverage --gate` | `unflipped_days=0`；`waiting_days` 只在母库真滞后时非零 | 归因（母库滞后容忍；我们没重跑=红） |
| 3.6 | 日志与轮转 | 人工 | `logrotate -d /etc/logrotate.d/paper-trading`；新日志文件名齐全 | 覆盖 `rq_day.log/shadow.log/raw_fetch.log/gate.log`，且**路径是 `/home/nec/…`**（仓库模板 `deploy/logrotate.paper-trading:6` 仍写 `/home/pi`，需先改） | 归因；磁盘写满会连带交易失败，一周内必须解决 |
| 3.7 | 幂等与续跑 | 自动 | 同日重跑 `rq day` 必须被 `SessionGate` 拒（T32 已验）；改费率后指纹漂移会拒续（G-T13-5） | 拒得对、原因可读 | 归因；**别用 `--force` 绕过**（绕过=同日重复成交） |
| 3.8 | 面板与账本一致 | `钱` 人工 | 抽一天：面板现金/持仓/净值 vs `m3 verify` 的 SQL 结果 | 逐位一致 | **立即回退**（展示层与账本分叉说明写入路径有问题） |

## 4. `m3 verify` 退出码约定（给 cron/告警用）

| 码 | 含义 | 动作 |
|---|---|---|
| 0 | 全绿（警不算红） | 记录，继续 |
| 1 | 有非钱类项红 | 当天归因，写进 `op_log`，观察期不自动回退 |
| 3 | **有钱类项红**（`rollback_required=true`） | **立即回退**，按 runbook §5.1 执行，产出事故报告 |
| 2 | 命令本身跑不起来（缺库/参数） | 修工具，不代表账本有问题 |

```bash
# 每天 15:55（影子对账与门禁之后）
55 15 * * 1-5 cd /home/nec/paper-trading && venv/bin/python -m paper_trading.cli \
  --data-db data.db --account-db paper_account.db m3 verify --json >> logs/m3_verify.log 2>&1
```

## 5. 工具不覆盖的（明确写出来，免得被当成"已经验过"）

- 「首单是否真的是新线下的」需要 `m3:switch` 这类流水锚点——**该开关还不存在**
  （runbook §0 G3），现在只能靠 `op_log` 的 action 名与时间线人工判断。
- 涨跌停带的"该拒必拒"在生产账本里验不了（新线不写账本，runbook §0 G1）；
  它的等价性目前只有 T29 的双跑与 T32/T42 的影子对账支撑。
- 除权日的真实资金影响（G-T13-2 另一半）需要因子文件到位才能测。
- `m3 verify` 读的是账本与行情库的**既成事实**，不重跑撮合——它回答
  "钱对不对、账齐不齐、线活着没"，不回答"引擎逻辑是否与旧线一致"（那是影子对账的职责）。
- 账本重置类动作（`ledger archive` 重开、手工改 cash、新开账）**不在 verify 的射程里**：
  它看的是当天事实，看不见"旧账的运行态还挂着新账"。这类动作之后必须跑
  `ledger reset-state`（清单与理由在 runbook §10「重置后必清」，周一的孤儿峰值就是它没跑）。
