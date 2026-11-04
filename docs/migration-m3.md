# M3 切换 runbook：旧自研执行线 → rqalpha 常驻线（2026-10-29）

> **背景**：M2 双轨影子对照已经在现网跑通（T39 首跑 + T42 验证日，
> `cash/fee/fill_delta` 全零，首个现网 `op_log[parity:diff]` 已落库）。
> §5.6 把节奏改成"压缩影子期、直取 M3"，切换方案由员工产出、**切换操作由主 AI 亲执行**。
> **切换目标**：让 rqalpha 成为下单/撮合/T+1/涨跌停的真相源（L1），
> 旧自研执行线整体封存只读，AI 链/门禁/面板/数据供给（L2）继续由我们拥有。
> **最高风险**：**新线目前根本不写账本**（§0 G1/G2）——按现状态"换 cron"会得到一个
> 每天算完就丢、没有 NAV、没有成交记录的空转线；以及**回退开关不存在**（§0 G3），
> 而 §5.6 把"config 开关一键切回"写成了回退保障。这两条必须在切换前关闭或明确改计划。

## 0. 现在还不能切：四个缺口（先读这节，全部有 file:line 证据）

| # | 缺口 | 证据 | 关闭判据（验收） | 建议归属 |
|---|---|---|---|---|
| **G1** | **新线没有账本写入路径**：`rq day` 只写 `--state-dir` 的 rqalpha persist + `evidence_<date>.json`，**不写 `paper_account.db` 的 orders/fills/positions/nav_history** | `paper_trading/integration/rq_engine/runner.py:216-218`（只写 evidence 路径，没有 broker/账本调用）；整个 `rq_engine/` 目录里唯一的 `PaperBroker` 用法是影子对照，且指向 `--shadow-dir` 的库（`shadow.py:48-54`、`shadow.py:199`） | 新线执行日：一笔计划订单从 rqalpha 成交 → 落到现网账本三张表（orders/fills/positions），字段与旧线口径可逐位对照；失败路径（拒单/资金不足）也落 `op_log` 且原因可读 | 需主 AI 派新任务（T45 不覆盖实现） |
| **G2** | **新线没有 NAV 记录器**：`record_nav` 的调用点全在旧线 | `paper_trading/agent/loop.py:524/1171`、`cli.py:371/442`、`main.py:133`；`runner.py:90` 的注释"指标继续由我们的 nav_history 记录器出"**只是意图，代码不存在** | 收盘后 `nav_history` 每天恰好 1 行（幂等，重跑不重复），且市值口径与面板一致 | 同 G1，建议并入同一件 |
| **G3** | **"config 开关一键切回"不存在** | `engine/config.py:27-37` 的 `DEFAULTS` 白名单里没有任何底座/后端选择键；全仓 grep `use_rqalpha|execution_backend|engine_backend|backend` 零命中；该说法只出现在计划文本（`refactor-plan-open-source.md:97/320`、§5.6"回退保障"） | 有开关：单一配置键 + 启动时校验 + 开关状态进会话指纹**并落 `op_log`**（否则事后无法证明那天是哪条线跑的）；且**回退方向要有回归测试**（开着旧线跑一天，账本与切换前一致） | 需要主 AI 决定：建开关，或接受 §5.1 的手工回退 |
| **G4** | 账本**归档/新开账工具不存在**（只有手工冷备先例） | `deploy/incident-20261027-fill-correction.py:23-25`（`shutil.copy2` 手抄 .bak）、`data/db_manager.py:75/105`（`conn.backup`）；`nav:revision` 这个 action 只在文档里（`ROADMAP.md:120/214`），代码无写入点 | 一个命令：把旧 `paper_account.db` 打成只读归档 + 新建账本并把首日 NAV 对齐归档的最后一天；产出一份"衔接说明"（哪几天归谁） | 建议主 AI 派 T47 之类；T45 先给手工步骤 |

**如果主 AI 决定 G1/G2 暂不补**，那 M3 的语义就必须改写成
"影子期正式化（新线只对照、旧线继续下单）"——这与 `refactor-plan-open-source.md:98`
M3 行写的"cron 切 rqalpha 常驻（一天一进程）"**不一致**，请显式改计划文本，
别让 cron 和实现各说一套。本文余下章节按"要真切换"来写。

### 0.1 T47 实现落点（`worker/t47` 待 review ⇒ **缺口尚未关闭**，这里只给"工具长什么样"）

| 缺口 | 现在的命令/入口 | 默认 | 关闭判据还差什么 |
|---|---|---|---|
| G1 | `rq day … --ledger`（成交/拒单/费用三分量落 `orders/fills/t1_freeze/op_log[rq:fill|rq:reject]`）；`--ledger-dry` 只报告不写库 | **不落账**（`build_config(ledger=False)`） | review + 一次现网受控落账（前后账本指纹核对，照 T42 做法） |
| G2 | 同上路径自动接 `record_close_nav` ⇒ `nav_history` + `op_log[rq-nav]`；旧线当天已记 `nav:close` 则让位 | 只在 `--ledger` 生效 | 现网连续一天 `nav_history` 恰 1 行 |
| G3 | `config.yaml`/`engine.local.yaml` 的 `engine.executor: legacy|rqalpha`；`cli ledger executor`（只读看当前底座 + 最近切换留痕）；守护进程启动即校验，未知值/装了旧线没装 rqalpha ⇒ **exit 2 不降级** | `legacy` | 现网切换/回退各一次演练（`op_log[m3:switch]` 两条） |
| G4 | `cli ledger archive --archive-dir DIR --yes [--allow-existing] [--stamp S]`：sqlite 冷备 → `chmod 444` → 新开账（cash/持仓/**未解冻 T+1**）→ `op_log[ledger:archive]` | 无 `--yes` 直接拒绝；账本非空且无 `--allow-existing` 拒绝 | 切换日实操一次，产出 §4 ③ 要的 `M3-BRIDGE-<date>.md` |

⚠️ **一处仍未闭合的双活风险**（report-t47 §7-Q1）：`engine.executor=rqalpha` 时
`paper_trading.engine` 守护进程**本身还是旧线撮合**——T47 只给了"新线成交怎么落账"，
没有擅自让守护进程停单。§4 的顺序铁律（先停旧再启新）目前仍靠人工执行，
它归 T48 执行模型拍板后一起解。

## 1. 规格对比（切换前后到底变不变）

| 维度 | 旧自研线 | rqalpha 线 | 结论 |
|---|---|---|---|
| 撮合 | `PaperBroker.submit_order` 自研判定 | `sys_simulation` + `matcher`（`price_limit=True`/`volume_limit=True`/`matching_type=current_bar`） | 换真相源 |
| T+1 可卖 | `positions.available_volume` + `unfreeze_t1` | rqalpha `closable`，解冻在 `before_trading`（`position.py:244-248`） | **切换日必须双看**（§6 验证步 3） |
| 涨跌停带 | 自研 `bands` 判定 | 框架 `utils/price_limits.py` + 我们的 band 供数（ST 不降档，2026-07-06 起） | 已由 T29 双跑证明等价 |
| 费用 | 佣金万 2.5/地板 5 元、印花税千 0.5 卖出、过户费 0.001% 双向 | T38 起与旧线**同源**（`sys_transaction_cost` 倍率 + 过户费 overlay） | 现网实测两侧逐分相等 |
| NAV/账本 | `paper_account.db` 全表 | **当前不写**（G1/G2） | 缺口 |
| 幂等/续跑 | `run_lock` + `op_log` 同日判重 | `SessionGate` + `session_fingerprint`（**含费率**，T38） | 新线更严，副作用见 §5.3 |
| 失败面 | 拒单原因只进日志（`paper_broker.py:291-293`，`_reject` 只有 `logger.warning`） | 拒单原因进 `evidence.rejects`（G-T13-8） | 新线更好，但旧线侧仍是 `see_logger` |
| 运行形态 | 每分钟 cron tick（`agent tick`/`engine --once`） | **一天一进程**（G-T13-9，`_SESSION_USED` 拒进程内重入） | cron 形状要重写（§3） |

## 2. 已拍板（原文引用，避免我替主 AI 解释）

- `refactor-plan-open-source.md` §5.6：「**不跳的最低线**：① T41 管道三缺陷修复；
  ② T40 生产 raw 回填 + `--gate` 全绿；③ 切换前对账 ≥1 个交易日 `cash/fee/fill_delta` 全零；
  ④ T44 审查无 P0」——①②③已达成（T41/T40/T42），④由 Hermes 交。
- §5.6：「M3 切换执行：主 AI 亲执行（账本归档+切换日新开账、cron 换线、
  **回退开关验证**），方案与切换后检查清单由员工产出（T45/T46）」
  ⇒ 回退开关是主 AI 自己要验证的项，但**它现在不存在**（G3），故列 §0。
- 顺序铁律沿用 `docs/migration-nec.md:75-76`：
  「**顺序铁律：① 在 ⑤ 之前。宁可空窗几分钟……不可双活一秒（重复下单）**」，
  回退同律（:83-87）「先停新线再恢复旧线」。

## 3. 切换对象清单（逐条核销；漏一条 = 漏一个功能，T21 教训）

| # | 对象 | 旧 | 新 | 备注 |
|---|---|---|---|---|
| 1 | cron 规则引擎/条件单 | `* 9-15 * * 1-5 … -m paper_trading.engine --once` | **停** | 停前先把 `engine.local.yaml` 的 `enabled` 置 false 落一次 `blocked_disabled`，确认没有在途单 |
| 2 | cron 开盘执行 | `30,35,40,45,50,55 9 * * 1-5 … agent tick` | **停或改**（见 ⚠️） | ⚠️ `agent tick` **一个命令同时做**"开盘执行计划"（`agent/loop.py:684-728` 窗口 09:30-10:00）和"收盘做计划"（16:30-18:30）——整条停掉会**连带断掉 AI 计划链**；要么给它加"只计划不执行"的开关，要么把执行半边交给新线。这是切换设计里必须显式决定的一条 |
| 3 | cron 收盘计划 | `*/5 16-18 * * 1-5 … agent tick` | **保留**（做计划半边） | 与 #2 同一命令，靠时段区分，别误停 |
| 4 | cron 事件复盘 | `1-59 9-11,13-14 * * 1-5 … agent event` | **保留** | 独立锁 `paper_trading.event.lock`（AGENTS.md:74），不与新线抢锁 |
| 5 | cron 交易日历 | `0 10 * * 6 … calendar sync` | **保留** | 新线的 `raw_freshness`/`is_trading_day` 也依赖 `var/trading_calendar.json` |
| 6 | cron raw 采集 | 无（手工/影子三步） | **新增 15:40 `data fetch-raw`** | 草稿见 `report-t42.md §5`；≥15:35 是批复 ③ 的硬时刻 |
| 7 | cron 影子对账 | 无 | 15:45 `rq shadow-day`（引擎 venv） | 需要 `--state-dir`/`--shadow-dir`；切换后是否继续跑双轨 = 主 AI 决定（建议第一周继续，作为 T46 的"影子对账连续"项） |
| 8 | cron **新线执行**（G1 关闭后） | 无 | **执行模型待定，见 `docs/worker/report-t48.md`**（"09:35 + 15:05 两轮"已被主 AI 否决：盘中每分钟止损语义不可丢） | 现状实测：同日复用 state 会被 `SessionGate` 以 exit 4 拒（"同日重跑会重复下单"）；单会话 2.4 s / 171 MB（NEC 实测）。切换前必须先定 ①/②/③，别照本行旧建议装 cron |
| 9 | 面板 | `paper-trading-dashboard`（8080，systemd unit） | **保留** | 面板读 `nav_history`/`op_log`：G2 不关闭则切换后面板净值断供 |
| 10 | 账本 | `paper_account.db` | 归档 + 新开账（G4） | §4 Phase 1 ③ |
| 11 | 行情库 | `data.db`（`daily_bars`=qfq、`daily_bars_raw`=28,339 行、`minute_bars`） | **不改** | T40 已回填；AI 展示层继续吃 qfq 列（口径标签由 T31 钉住） |
| 12 | venv | `~/paper-trading/venv`（akshare，无 rqalpha） | **+** `/home/nec/venvs/rq-engine`（rqalpha 6.4.2 + numpy 2.3.5） | **绝不合并两份 venv**（`requirements-rqalpha.lock.txt` 头部）；跨机/跨架构拷贝 venv 禁止（migration-nec.md:28） |
| 13 | bundle | 无 | `~/.rqalpha/bundle`（202610，sha256 记录在 report-t39 §3） | 月度快照月中"日线不完整"是预期；复权因子同源 ⇒ 跨除权日跑历史前先更新 |
| 14 | state 目录 | 无 | `/home/nec/rq_state/<date>/` | 指纹含费率与 db/bundle 绝对路径 ⇒ 换机/换路径会被判漂移（§5.3） |
| 15 | 锁 | `paper_trading.lock`（下单类）/`paper_trading.event.lock` | 新线单独一把（`--lock-file var/rq.lock`） | 别和旧线共用：旧线一天多轮、新线一天一进程，共用会互相挡 |
| 16 | 本地覆盖文件 | `engine.local.yaml`、`strategy.local.yaml`、`secrets.local.json` | **carry-over** | 与 migration-nec.md:118-119 同规则：一并带上、`.bak*` 不迁 |
| 17 | 方案（scheme） | `strategy.local.yaml` 的 `active_name`（`strategy/schemes.py:118-123`）；`--scheme` 只作覆盖 | **保留原方案** | mother 方案依赖母项目可读性（schemes.py:132-165），切换日别顺手换方案 |
| 18 | logrotate | `/etc/logrotate.d/paper-trading` | **改路径/加新日志** | 仓库模板 `deploy/logrotate.paper-trading:6` 仍写 `/home/pi/paper-trading/*.log`（**已知过时**），要加 `rq_day.log`/`shadow.log`/`raw_fetch.log`/`gate.log` |
| 19 | 版本/tag | 生产 `main@aaedea4`（T41-T42 未合） | 切换前必须合入 T41/T40/T42 并打 tag | tag 由主 AI；`worker/t4x` 堆叠顺序 t41→t40→t42 |

## 4. 三阶段执行

### Phase 0 · 切换前（1-2 个交易日）

- [ ] G1/G2/G3/G4 四个缺口：要么关闭（附测试），要么在计划文本里显式改写 M3 语义（§0）
- [ ] 影子三步（采集→shadow-day→gate）连续 ≥1 个交易日零差且 `--gate` 绿（已由 T42 达成 1 天）
- [ ] T44（Hermes）钱类复核无 P0
- [ ] **T42 §4 的 P1（当日跨源对照恒不成立）有结论**：切换后引擎吃的是"当日收盘 bar"，
      没有当日独立参照的链路不该正式接管下单
- [ ] 干跑：用现网 `data.db`/`paper_account.db` 的**副本**在 scratch 走一遍新线全流程
      （`rq day` + 状态续跑 + 幂等闸 + NAV 写入路径若已存在）
- [ ] 回退演练：按 §5.1 手工回退走一遍（副本上），确认步骤里没有任何一步需要"现场想"

### Phase 1 · 切换日（主 AI 亲执行，逐步填时间戳进 §7）

```
① 停旧 cron（#1、#2 的执行半边）
   crontab -l > /home/nec/crontab.pre-m3-$(date +%Y%m%d-%H%M%S).bak
   crontab -e   # 注释掉规则引擎行与开盘执行行，保留收盘计划/事件复盘/日历
   确认：pgrep -af paper_trading.engine 为空；engine.log 末行是本轮结束而非中断
② 账本冷备 + 归档（G4 有工具用工具，没有就手工）
   sqlite3 paper_account.db ".backup /home/nec/archive/paper_account.pre-m3-<date>.db"
   chmod 444 /home/nec/archive/paper_account.pre-m3-<date>.db
   ⚠️ 归档后这份库**只读保留一个版本周期**（§7-M3 行原文），别再拿它跑写命令
③ 切换日新开账
   新账起点 = 归档账 `nav_history` 最后一天的 cash/positions/NAV（人工核对一遍，
   或 G4 工具自动对齐）；账本衔接说明写入 /home/nec/archive/M3-BRIDGE-<date>.md
④ 启新 cron（#8、#6、#7）——必须在 ① 之后（顺序铁律，不可双活一秒）
⑤ 验证步（§6 逐项，绿了才算切换完成；任一项红 ⇒ §5 回退）
```

### Phase 2 · 观察期

- 第一小时 / 第一天 / 第一周逐项跑 `docs/m3-checklist.md`（T46 产出），
  **任何钱类项红 ⇒ 立即回退并归因**（§5.6 原文）。
- 影子对账建议第一周继续跑：它是"新线是否漂移"唯一的外部对照（G-T13 家族）。

## 5. 回退方案

### 5.1 现状（无开关）下的手工回退——精确 6 步

```
① 停新 cron（#8/#6/#7）：crontab -l > /home/nec/crontab.pre-rollback-<ts>.bak && 编辑注释
② 确认新线无在途：pgrep -af rqalpha / paper_trading.cli 为空；读当天 evidence_*.json 的 exit_code
③ 旧账本处置：新账（切换日开的）立刻 .backup 一份并 chmod 444 归档；
   把 pre-m3 归档账本复制回 /home/nec/paper-trading/paper_account.db
   —— 禁止把新账的 fills 增量"合并"进旧账（两本账的 avg_cost/entry_day/peak_price
      口径不同：旧线把费用摊进 avg_cost，rqalpha 的 avg_price 不含费 ⇒ 合并必错）
④ 恢复旧 cron：crontab /home/nec/crontab.pre-m3-<ts>.bak（回到切换前的那一版）
⑤ 验证：status/面板/`nav_history` 当日 1 行/下一日 T+1 解冻正确/`op_log` 落 rollback 说明
   bridge.broker.log_operation(action="m3:rollback", …) —— 这条流水是事后归因的唯一锚点
⑥ 写事故报告到 docs/worker/（触发项、证据、复现步骤、下次怎么防）
```

### 5.2 最小开关规格（若主 AI 选择建它，G3 的关闭判据）

- 配置：`engine.local.yaml` 或 `config.yaml` 新增**一个**键（例如 `runtime.executor: legacy|rqalpha`）；
  必须同时加进 `engine/config.py:27-37` 的 `DEFAULTS` 白名单——
  **不加就会被静默丢弃**（这是本仓已知的"配置键白名单吞新键"行为，`config.py:59/64`）。
- 启动时校验并 `logger.warning` 打印当前底座；`rqalpha` 而引擎 venv 缺失 ⇒ 直接 exit 2，不许降级。
- 开关状态进 `session_fingerprint`（`rq_engine/runner.py:123-140`）**并落 `op_log`**
  （`m3:switch`），否则事后没人能证明那天是哪条线下的单。
- 回退方向的回归测试：开着 `legacy` 跑一天 ⇒ 账本与不带该键时逐位一致。

### 5.3 账本衔接边界 + 一个必须知道的副作用

- **边界**：切换日 00:00 前的所有事实归归档账；切换日起归新账；
  回退时新账整段封存只读，**不做跨账合并**（§5.1 ③）。
- **副作用（G-T13-5 的真实含义）**：`session_fingerprint` 含 `db`/`bundle` 绝对路径与费率
  （T38 加入），所以**换机、换路径、改 `config.yaml` 的 `trading.*` 都会让同一天拒续跑**，
  需要 `--force`。跨机对照时"指纹不同"是设计如此（`report-t39.md §5`），别当漂移；
  真正的可比对象是现金/费用/持仓数字。

## 6. 切换日验证步（详版在 `docs/m3-checklist.md`，T46）

| # | 项 | 怎么验 | 期望 |
|---|---|---|---|
| 1 | 首单 | 下一交易日看 `orders`/`fills` 是否由新线写入且 `op_log` 有归因 | 与计划逐笔一致 |
| 2 | 费用三件套 | 逐笔手算：佣金 `max(amount×0.00025, 5)`、卖出印花税 `amount×0.0005`、双向过户费 `amount×0.00001` | 与账本逐分相同（T38/T40 已双侧实测） |
| 3 | T+1 | 同日买入的 `closable/available_volume` 必须为 0，次一交易日 `before_trading` 后放开 | 与 `report-t32` 的两侧对照一致 |
| 4 | 涨跌停 | 一字板标的下单必须被拒且原因可读 | `evidence.rejects` 有中文原因 |
| 5 | NAV | 收盘 `nav_history` 恰 1 行，面板净值连续 | 依赖 G2 关闭 |
| 6 | 留痕 | `op_log` 出现当天 `parity:diff`（若影子继续跑）与 `m3:switch` | 缺失即"无痕"，按红线归因 |
| 7 | 门禁 | `data raw-coverage --gate` 退出码 0；`flip_watch` 无 `unflipped` | 非 0 ⇒ 不许下单 |

## 7. 执行记录（主 AI 亲执行时填写）

| 步骤 | 时刻（北京） | 执行者 | 结果/证据 |
|---|---|---|---|
| ① 停旧 cron | | | |
| ② 账本冷备+归档 | | | 归档文件名： |
| ③ 新开账+NAV 对齐 | | | 衔接说明文件： |
| ④ 启新 cron | | | `crontab -l` 差量： |
| ⑤ 验证步 1-7 | | | |
| （回退则另开一表） | | | |

## 8. 从 migration-nec.md 继承的坑位提醒

- **禁止跨机/跨架构拷 venv**（migration-nec.md:28）：NEC 是 x86-64-v1，numpy 必须 <2.4
  （AGENTS.md:235-238；T37 三次现场复现 `X86_V2` RuntimeError）。
- 本地覆盖配置一并带上、`.bak*` 不迁（:118-119）。
- 面板 `admin_token` 存在浏览器 localStorage，换机/换账本后要重存（:120-121）。
- `ufw` 放行 8080；"本机 curl 200 ≠ 局域网可达"（:98-102）。
- cron 路径必须显式 `/home/nec/...`（Pi→NEC 迁移后 `/home/pi` 是旧真相，:71/:95）。

---

## 执行记录（2026-10-30 15:45-19:50，用户 24 小时迁移令，主 AI 亲执行）

| 步 | 动作 | 验证 |
|---|---|---|
| 1 | 5 分钟线回填（新浪 106 天历史深度实测后批量拉取） | 191,862 行 / 38 只（minute_bars 总 194,166） |
| 2 | R1 修复：overlay 安装点 → mod start_up（priority 200） | 续跑跳实测 other_fees 0.02167/0.07228 非零 |
| 3 | 分钟 store + get_bar 覆盖（开源版拒 1m 查询） | 分钟会话 240 跳/session |
| 4 | 意图队列 + 执行分叉 + rq tick | 端到端：入队→成交@44.94（盘中价）→fills 落账→rq:fill/rq-nav→m3:switch 留痕 |
| 5 | 切换：engine.local.yaml `executor: rqalpha` + cron `2-59/5 9-15 * * 1-5` rq tick | engine --once 以 rqalpha 底座正常运行；m3 verify 钱类 4 项绿 |
| 回退 | `executor: legacy`（一行）+ 删 tick cron 行；rq_intents 残留 queued 意图人工核对 | 旧线 PaperBroker 路径零改动 |

**已知保守语义**（记录在案）：同日同标的同量（intent_key 级）二次触发被幂等拦截——追加仓位需不同量或次日；tick 按 symbol 批量标 executed 的粒度待精化（按 biz_id 匹配）。

---

## 9. ⚠️ 上面那份"执行记录"由 T57 接管更正（2026-10-30 晚，`docs/worker/report-t57-m3-takeover.md`）

切换确实发生了，但**执行记录里三条验收说法与现网证据不符**，逐条更正：

| 执行记录原文 | 现网实测 | 更正 |
|---|---|---|
| 步 4「成交@44.94（**盘中价**）」 | `evidence_2026-10-30.json` 里这笔成交的 `datetime=2026-10-30 09:31:00`、`price=44.94`，而意图 `created_at=16:49:24` 是**收盘后**造的 | 成交价不是决策时点的价，而是**每跳重放当天全部 bar** 后早盘那根 bar 的价（P0-2）。接管后用 14:30 的意图实测成交在 `14:30:00 / 45.04`（= 当天 14:30 那根 5 分钟 bar 的 close，早盘 09:35 是 45.08） |
| 步 5「m3 verify 钱类 4 项绿」 | 当时的 verify **没有** `intent_queue` 这一项；现网 `rq_intents` 里 3 条 `executed` 只对应 1 笔 fills | "4 项绿"是真的但**不完整**——缺的那一项正是会把这 3 条假账点出来的。T57 补了 `intent_queue`（钱类红）后，现网跑同一命令立即红并逐条点名 |
| 尾注「tick 按 symbol 批量标 executed 的粒度**待精化**」 | 同跳/同日同款意图被顺带标 executed，其中一条是**卖出**机会（买单把它吞了） | 这不是可延后的打磨项，是丢单的 P0：T57 已改为逐笔认领（方向+量）+ 未成交 rejected |

另外两条"切换完成"没覆盖的运维事实（也是 T57 补的）：

- **现网一条行情采集 cron 都没有**：`daily_bars_raw` 当日 0 行（周一开盘 T50 盘后闸会
  全线挡单）、分钟线只有收盘后补的那一份 ⇒ 新线盘中没有 bar 可撮合。
  T57 装了 `40 15 fetch-raw` + `50 15 minute sync` 两行，并当场补齐 10-30 的 raw（38/38 checked）。
- **无消费跳/无队列/无供给的报警缺失**：`rq tick` 的 cron 是 16:52 装的、当日没跳过，
  `rq_tick.log` 与心跳里的 `rq_tick` 键都不存在——这类"没跑"当时无人可见。

更正后的现网状态：NEC 工作树 `56ae3b1 → b5e4378`（`worker/m3-takeover`，**未合并 main**），
`venv/bin/python -m pytest -q tests/` 647 passed；`paper_account.db` 未被动过（md5 不变）。
回退：`git checkout 56ae3b1` + `crontab /home/nec/crontab.pre-m3-takeover-20261030-183811.bak`。


### 切换后第一日核验补充（2026-11-03，主 AI）

- **周一 11-02 零成交 = 熔断正确拦截**（回撤 35.23% > 20%）；根因：
  `peak_total_value=156,005` 为 10-27 旧账户遗留（账户重置后成孤儿峰值，
  永久锁死熔断——这正是"自研时代账户重置没清状态"的残留）。
- **用户拍板重置**：`peak_total_value → 100,189.96`（2026-11-03，冷备份
  `peak-reset-backup-20261103.db`）→ 熔断解除，engine 恢复（armed=9/quotes 在）。
- 数据供给缺口：① 分钟线**盘中**采集缺失（已装 15 分钟 cron +
  `deploy/m3_minute_intraday.sh`）② raw_gate 5 只未翻转（T60 归因）
  ③ heartbeat 语义（T61 审查）。
- 结论：**新引擎（rqalpha 分钟会话）工作正常**，当日无成交是风控行为而非故障；
  状态清理工具（T62）防止同类"重置遗留"再发生。

## 10. 重置后必清（T62 状态清理工具，2026-11-03）

账户重置 / 账本迁移 / 新开账之后，**只重开账本不够**：库外的运行态不会跟着开。
2026-11-02 周一就是这么炸的——`ledger archive` 式重开把 cash 改成新起点，
而 `risk_state.peak_total_value` 还是旧账的 156,005.23，新账净值 100,189.96
⇒ 回撤 35.7% > 20% ⇒ 全天零成交，次日被读成“迁移失败”（见 §9 之后的 11-03 补充）。

清单本体在代码里（`paper_trading/ledger_state.py::TARGETS`，纪律 7）；
下面这张表由它渲染，`tests/test_reset_state_t62.py` 逐条比对，文档与代码不许各说各话。

| id | 名称 | 位置 | 类别 | 默认 | 留着会怎样 | 动作 |
| --- | --- | --- | --- | --- | --- | --- |
| `risk_peak` | 风控峰值净值 | risk_state.peak_total_value | halt | 必清 | 峰值是旧账挣出来的，新账第一小时就被回撤熔断挡在门外（周一事故本体） | 删除该键，让峰值从新账的第一次观测重新锚定 |
| `risk_daily_baseline` | 当日亏损基线 | risk_state.daily_baseline:<日> | halt | 必清 | 基线是旧账的总资产，日亏比例按旧尺子算 ⇒ 每轮都判熔断；现网 10-27 那行至今写着 156,005.23 | 删除全部 daily_baseline:* 键 |
| `rq_intent_pending` | 未决意图队列 | rq_intents(status=pending/queued) | ghost | 必清 | 意图是旧账在某分钟做出的决定，重置后照样被 `rq tick` 消费 ⇒ 新账执行了它没做过的决定 | 逐条标 rejected（写原因、保留行——删掉就没法解释那天为什么没成交） |
| `cond_armed` | 已挂条件单 | conditional_orders(status=armed) | ghost | 必清 | 条件单的价位与金额是按旧账的仓位/现金设的，重置后触发就是拿旧决定下单 | 逐条标 cancelled（想留的价挂单请在重置后重挂） |
| `rq_session_state` | 引擎会话状态目录 | var/rq_state/<日> | ghost | 必清 | 同日续跑会恢复旧账的 portfolio/universe/挂单，新账直接接上旧持仓继续撮合 | 整目录改名 <日>.pre-reset-<时刻> 挪开（不删：钱类件的可恢复性优先） |
| `engine_fired` | 规则当日已触发标记 | engine_state.fired:<日> | throttle | 必清 | 规则被记成「今天已经动过」，新账同一分钟再触发会被静默吞掉 | 删除 fired:* 键 |
| `engine_budget` | 日内下单预算 | engine_state.budget:<日> | throttle | 必清 | 当日订单数/成交额预算已被旧账用掉 ⇒ 新账被判「超出预算」而不下单 | 删除 budget:* 键 |
| `engine_cooldown` | 每标的冷却 | engine_state.cooldown:<标的> | throttle | 必清 | 冷却时间戳来自旧账的成交，新账白白等一段冷却（漏机会，不伤钱） | 删除 cooldown:* 键 |
| `engine_trigger_once` | 拦截意图的单日一条留痕 | engine_state.trigger-once:* | throttle | 必清 | 开关拦截的流水标记留着 ⇒ 重置后同类拦截不再上面板，看着像「没发生」 | 删除 trigger-once:* 键（engine_triggers 流水表本身是历史，不动） |
| `engine_event_throttle` | AI 事件评审节流 | engine_state.event:* | throttle | 必清 | event:last / event:sym:<标的> / event:budget:<日> 是上一本账的评审配额，留着会让新账少评审一轮（代价：重置后可能多花一次 LLM 调用） | 删除 event:* 键 |
| `t1_unfreeze_overdue` | 逾期未解冻的 T+1 | t1_freeze | report | 只报不清 | 解冻只发生在 `run` / `agent tick` 的结算段；结算路径整日没跑，冻结股就一直 available=0（卖不掉）。重置工具无权替结算路径写持仓 | 报出来，跑一次结算路径或人工裁决 |
| `intent_ledger_conflict` | 意图与账本口径冲突 | rq_intents × orders | report | 只报不清 | 标 rejected/expired 的意图在同源同日仍有成交，而原因又不是幂等/去重 ⇒ 队列与账本讲的是两个故事，先裁决哪边是真的，再谈清理 | 报出来（现网 10-30 切换日那三行就是这个形状） |
| `heartbeat_file` | 心跳文件 | var/heartbeat.json | report | 只报不清 | 心跳记的是数据供给，不是账本状态；删掉反而让「装过才追问」的分钟采集项假绿 | 保留（只报当前键与最后时刻） |
| `unknown_state_keys` | 清单没见过的状态键 | risk_state / engine_state | report | 只报不清 | 新代码往里写了新键而清单没跟上 ⇒ 旧账的运行态被静默留下，正是这次事故的形状；未知键必须冒出来，不许默默算「清干净了」 | 报出来，按纪律 7 把键补进本清单 |

用法（默认 dry-run，看不见的不写）：

```bash
# 1) 先看计划：什么都不写，把每一项现状 + 钱类对照打出来
python -m paper_trading.cli ledger reset-state

# 2) 确认后动手：--reason 必填，会连同清理前的原值一起进留痕
python -m paper_trading.cli ledger reset-state --yes \
  --reason “11-03 账户重置：旧账峰值/基线是孤儿尺子”

# 3) 想留一项就显式点名（会写进 op_log，事后可审计“谁决定留的”）
python -m paper_trading.cli ledger reset-state --yes --reason … --keep cond_armed
```

三条硬规矩（细节在模块 docstring）：

1. **只清运行态，绝不动钱类**：`account/positions/orders/fills/nav_history/t1_freeze/op_log` 一行不碰，前后快照必须逐字相同，越界即回滚并报错（`test_money_guard_rolls_back_when_a_target_oversteps` 钉着）；
2. **清之前抄现场**：`op_log[ledger:reset-state]` 的 `result.cleared[*].before`
   存清理前的键与值——切换日那次无留痕的改判已经吃过教训，不留痕的清理等于毁证；
3. **只报不清的四项**（`t1_unfreeze_overdue` / `intent_ledger_conflict` /
   `heartbeat_file` / `unknown_state_keys`）要人裁决：逾期 T+1 的解冻要写持仓，
   是结算路径的活；意图与账本口径冲突要先定“哪边是真的”；心跳删了会让
   “装过才追问”的分钟采集项假绿；清单没见过的键必须冒出来，不许默默算“清干净了”。
   有红报时命令退出码 1，脚本不许把 0 当成“状态已一致”的凭据。

`ledger archive`（G4 重开账本）现在也会报同一份清单里**它自己清不掉**的部分：
库内 KV 态随文件一起没了，但 `var/rq_state/<日>` 会话目录不在库里——旧账的
portfolio 快照留着，下一次 `rq tick` 的 `intraday_resume` 就会把它接进新账
（钱类残留）。archive 的返回与留痕里多了 `reset_state_pending`，看到它就跑上面第 2 步。

现网首次体检（2026-11-03，`/home/nec/paper-trading`，只读）真实照出两类红报：
`t1_unfreeze_overdue` 1 条（002192 100 股，应于 11-02 解冻，结算路径没跑到 ⇒
available 仍是 0），`intent_ledger_conflict` 2 条（10-30 切换日的两条 002192
BUY_100 意图标 rejected 写着“no ledger fill”，而订单 #13 / 成交 #12 就躺在账里）。
这两类都是“先裁决、后清理”的形状，`reset-state` 不会替人按下删除键。
