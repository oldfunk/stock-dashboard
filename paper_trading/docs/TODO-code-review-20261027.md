# 一周代码重审清单（2026-10-20 ~ 2026-10-27，事故驱动）

> 背景：2026-10-27 价格尺度事故（v0.7.1/v0.7.2 执行侧取母库后复权价，
> 以 175.97 买入真实价 22.24 的票，虚付 ~15,372；已冲正 + v0.7.5 门禁）。
> 事故根因不是单点 bug，是三类**系统性缺口**，本清单按类排查全周 40 提交/~2400 行。
> 进度回写本文件；里程碑级结论同步 `docs/ROADMAP.md`（唯一进度真相源）。
>
> 纪律：每项必须有**证据**（对抗性测试 / 脚本输出 / Pi 实测），不许“读了一遍觉得没问题”。
> 发现问题 → 域内直接修（小步提交），接缝 → 母库 Issue，跨阶段结论 → ROADMAP 勾选。

## 事故复盘（为什么测试没拦住）— R0

- [x] R0.1 写事故复盘文档 `docs/incident-2026-10-27.md`：时间线、五道防线为什么全漏
  （计划价对的、执行价污染、涨跌停带同源自洽、金额上限被抬过地板、无跨源校验）
- [x] R0.2 提炼"事故类"检查模式，写进 AGENTS.md（独立基准原则/量级 sanity/
  接线三件套/数据源切换=高危变更——已写入 incident 文档 §5，AGENTS 待随 P1 收尾合入）

## P0 — 同类根因全量排查（价格/数据尺度，事故直接同源）

- [x] R1.1 **价格消费点全量清单**：摸排完成（见附录 A）
- [x] R1.2 **所有乘价格的运算**：NAV/单笔上限/费用地板/涨跌停带/条件值/止损价
  已逐点走查；根因结论=同源校验挡不住尺度错误，修复走 R1.3 统一闸门
- [x] R1.3 **跨源尺度闸（v0.7.6）**：`integration/mother.daily_bars` 出口加
  `_scale_guard`——母库与本地**同日收盘**比对，偏离 >10%（`SCALE_TOLERANCE`）
  拒用母库回退本地 + 告警 + `scale_check` 落 provenance；3 组单测
  （污染拒/小差放/无重叠不误杀）
- [x] R1.4 **AI 上下文同口径**：`recent_closes` 走 `_mother.daily_bars` →
  已被 R1.3 闸门覆盖（同入口）；执行层 `max_price_deviation` 门禁已有
- [x] R1.5 **回放对照口径检查**：`_replay_decision` 加 `caliber_warning`
 （决策价 vs 回放 bar 收盘偏离 >30% 显式告警）+ `caliber_warnings` 汇总字段；
  篡改价触发/正常不触发两组单测；Pi 实测事故日回放无告警（冲正生效）✓
- [x] R1.6 Pi 实测：38 只（持仓+池）双源量级核对——25 只同日可比**全部在
  [0.9,1.1]、0 异常**；7 只无同日重叠（本地滞后）；BarSource 生产路径
  38 只无量级可疑 ✓

## P0 — 配置接线断层（require_stop 教训的全量排查）

- [x] R2.1 **AgentConfig 字段接线审计**：`tests/wiring_audit.py` 反射脚本——
  首跑即抓到 4 个未接线（news_enabled/news_limit/news_timeout_sec/context_budget），
  且发现**双层白名单结构**（load_config 归一化层 + cli 构造层），v0.7.5 只修了
  cli 层、漏了归一化层（config 写了仍失效）→ 两层全补，27/27/27 打通
- [x] R2.2 **接线回归测试** `tests/test_config_wiring.py`（6 项）：
  双层字段 ⊆ 互检 + 反向（拼写错误）+ 安全三件套存在性 + yaml roundtrip
  + TradingBridge 实例穿透验证
- [x] R2.3 其他配置类同查：wiring_audit 输出的 27 键清单即对照表
  （engine/strategy 配置键对照随 P2 收尾）

## P1 — 执行链/下单链重审（loop.py +821 行）

- [x] R3.1 `_execute_actions` 全分支走查：10+ 拒单分支顺序合理（基准价提取在
  判定链前、门禁在风控前），无绕过路径；dry-run 与实盘共用同一判定链
- [x] R3.2 `_execute_plan` vs `run()` 两路径对照：**唯一生产入口** loop.py:822
  位于 `run()` 第 4.5 步（回撤/日亏熔断**之后**），`runtime_tick` 也走
  `run(execute_only=True)` 同链——熔断/风控无绕过路径；两路径共用
  `_execute_actions` 同一判定链
- [x] R3.3 计划生命周期：已有 10 个专项用例（跨日消费/stale bars/缺价
  fail-closed/过期顺延/同日幂等/条件挂载/NAV 补记），无新增缺口
- [x] R3.4 条件单四条 AI 路径统一性：计划/现决/事件复盘（`events.py`→
  `review_conditions`）/条件设计（`design_conditions`）全部传 holdings +
  prices，`parse_rejected` 全进 `ai:plan`/`ai:decide`/`ai:review` 流水
- [x] R3.5 **条件单尺度门禁（v0.7.6）**：`parse_conditions_verbose` 加 `prices` 参数，
  price_* 条件值必须在现价 [0.5×,2×] 带内（挂几天容许翻倍止盈/腰斩止损预期，
  8× 口径污染当场拒），pct_* 不适用；**四处 AI 路径全接**（计划用定稿收盘价、
  现决用 price_map、事件复盘/条件设计用实时价）；事故场景单测锁定

## P1 — 母库集成层重审（mother.py +739 行，全新增）

- [x] R4.1 逐接口降级矩阵：daily_bars 四态全覆盖（母库好✓/表缺失✓新增/
  该票缺✓/库整体坏✓）+ news 空库态、screening 无母库态；其余接口共享同一
  `_has→unavailable` 骨架，不逐个做 4 态全组合（收益递减，代表性覆盖足够）
- [x] R4.2 TTL 缓存与缓存键正确性：**抓到真 bug**——12 处缓存键含 `_seq(conn)`
  单调连接序号，`_conn` 每次新建连接 → 键永远不同 → **TTL 缓存跨调用永不命中**
  （文档声称 60s/300s，实际形同虚设）；`_COLUMNS` 同类问题（每次连接全清）。
  修复：键改**库路径**（`_dbkey`，跨连接稳定/多库不串），列缓存同改；
  回归测试 `test_ttl_cache_hits_across_connections`（改库吐旧值+TTL=0 回源）
- [x] R4.3 只读保证审计：integration 层零写操作/零 `executescript`、连接全部
  `mode=ro`+`query_only`+`timeout=2`+`busy_timeout=2000`；唯一 `SELECT *`
  （financial_summary）违反 §6 承诺 → 改 PRAGMA 动态列清单（母库加列自动跟随）
- [x] R4.4 失败模式：锁竞争实测（合成库 BEGIN EXCLUSIVE → 2s 硬超时内回退
  本地，`test_locked_mother_falls_back_quickly`）；真母库不做加锁实验
  （母项目回填进程在写，红线不碰）；磁盘满=连接失败同一 `_conn except` 路径
- [x] R4.5 真库契约扩展：R1.6 量级比值检查固化进
  `test_real_mother_contract_if_present`（近 7 日重叠抽样 [0.9,1.1] 断言）

## P1 — 引擎/回测重审（runner +224 / backtest +172 / ai_conditions +146）

- [x] R5.1 回测 vs 实盘口径：回测读 `data_db` 已存日线快照（**有意保留**——
  可复现性要求快照源，写入附录 A）；v0.7.3 qfq 对齐后与 bar_source 同口径
  （`data compare` corr≈1.0）；缺票诚实 skipped 不造假
- [x] R5.2 `evaluate()` 纯函数审计：`rules.py` 零状态依赖（无 self/state 全局），
  回测与 `runner` 调同一份 `_EVAL` ✓
- [x] R5.3 无未来函数：`test_runner_today_minutes_drops_future` 锁定（ts>now
  丢弃）+ 回测"已走完分钟"重放注释与实现一致 ✓
- [x] R5.4 条件单触发链：`test_engine_switch.py`（enabled=false 只 blocked_disabled
  不成交）+ `test_engine_cond.py` 离场优先/触发/过期 ✓；触发价用实时报价（独立源）
- [x] R5.5 资金守恒不变量：`test_backtest_cash.py` 3 项（卖出回款/权益=现金+市值/
  日内卖出回款）✓；引擎 dry-run 实测见收尾项

## P2 — 账本/审计完整性（broker +265）

- [x] R6.1 `agent_decisions` 新表：写入全 try/except（失败不阻断决策链）、
  读回放 JSON 容错（坏 JSON 回空默认）；cron 写入由全局 run_lock 串行；
  roundtrip + 落库回放单测 ✓（旧库升级=CREATE IF NOT EXISTS 幂等）
- [x] R6.2 NAV 幂等判重（业务日）：3 个专项测试（每日一条/补记历史日判重/
  计划已存在也补记）✓
- [x] R6.3 `op_log` 无任何 DELETE/DROP 逻辑，`data:incident` 永久留痕 ✓
- [x] R6.4 事故冲正可重复性：**冲正脚本存档** `deploy/incident-20261027-fill-correction.py`
  （幂等断言防重跑+改前备份+逐步复核），作为未来冲正的模板与审计记录

## P2 — 并发/锁/超时（run_lock +151 / market_time +120 / timeout +33）

- [x] R7.1 三把锁竞态：`test_lock_isolation.py` 3 项 + `test_run_lock.py` 3 项
  （创建释放/活锁不偷/残留接管）✓；event 独立锁不占全局（复盘不会挡引擎）
- [x] R7.2 残留锁 PID 复用误判：`test_pid_reuse_mismatched_token_is_stale`
  ——PID+启动标识双校验已有专项测试 ✓
- [x] R7.3 akshare 硬超时清点：三个出口全过——`akshare_fetcher`（fetch_daily/
  stock_info）、`news`（_call_with_timeout+不挂死测试）、`trading_calendar.sync`；
  main/cli 仅 import 无直调 ✓
- [x] R7.4 cron 双跑：event 独立锁、engine 单轮 `--once` 5s timeout 由 R7.1
  测试覆盖语义；两锁不同文件，同分钟并发不互挡不死锁

## P2 — 面板/CLI/安全（dashboard +355 / cli +552）

- [x] R8.1 POST 端点鉴权全量核（脚本枚举 do_POST 顶层分支）：8 个写端点
  ——7 个过 `_admin_ok`（同源 CSRF 在入口统一拦）、`agent/schedule` 410 停用；
  GET 全只读 ✓
- [x] R8.2 `--json` stdout 纯净性：CLI 无裸 print（唯一 print 是 `emit` 本体）、
  日志走 stderr（`test_json_stdout.py` 锁定）✓
- [x] R8.3 `rowsC` 折叠渲染的 XSS：provenance 表 4 列全走 `esc()`（含母库
  返回的 source/asof 字段）✓；`st` 列为纯代码常量无注入面
- [x] R8.4 错误信息泄漏：500 响应仅 `类型: 消息`（无栈/Key——Key 从不进异常链
  且有单测锁定不落日志）；ValueError→400 不含请求体内容；局域网威胁模型内
  可接受，记录不改

## P3 — 文档一致性 + 收尾

- [x] R9.1 AGENTS.md 对照：规则 3.2 重写（三道尺度防线/独立基准原则/一个价源）、
  硬约束新增第 7 条配置三件套；`docs/automation-audit` 版本记录同步
- [x] R9.2 附录 A 价格消费点表已产出（R2.3 配置对照=`wiring_audit.py` 输出 27 键）
- [x] R9.3 汇总报告见下方「审查汇总」
- [x] R9.4 复盘文档 `docs/incident-2026-10-27.md` 定稿；本轮审查未发现新的
  母库接缝问题（缓存键/接线/绕过点全在子项目侧），无需新提母库 Issue

## 审查汇总（2026-10-27，R0–R9 全部完成）

**发现的真问题 6 个，全部已修（含回归测试）**：

| # | 问题 | 级别 | 修复 |
|---|------|------|------|
| 1 | 配置接线**双层白名单**断层：`require_stop`/`require_basis`/`max_price_deviation` 归一化层漏、`news_*`/`context_budget` 构造层漏（config 写了也静默失效） | P0 | 两层补齐 + `test_config_wiring.py`(6) + `wiring_audit.py` 脚本 |
| 2 | 母库 TTL 缓存**永不命中**：12 处键含单调连接序号、`_COLUMNS` 每连接全清——文档声称 60s/300s 实际形同虚设 | P0 | 键改库路径 `_dbkey` + 回归测试（改库吐旧值/TTL=0 回源） |
| 3 | **6 处价源绕过** `bar_source`：get_status 面板计价/手动单/preview/dry-run 直读本地——同账户两套价 | P0 | 统一 `bar_source`（回测快照源有意保留并记录） |
| 4 | 条件单**无尺度门禁**（事故 #29 场景：现价 22 挂 180 挂了 26 分钟） | P0 | `parse_conditions_verbose` 加 `prices` 带闸，四路径全接 |
| 5 | 回放**无口径检查**（污染决策对照时静默出错误结论） | P1 | `caliber_warning` + 单测 |
| 6 | `financial_summary SELECT *` 违反 §6 只读性能承诺 | P2 | PRAGMA 动态列清单 |

**新增防线 1 道（根因级）**：R1.3 跨源尺度闸——`daily_bars` 出口母库 vs
本地同日比值 [0.9,1.1]，一处保护执行/NAV/引擎/AI 上下文全部下游。

**审查通过（证据在各节）**：R0 事故复盘、R3 执行链（分支/两路径/生命周期/
四条件路径）、R5 引擎回测（纯函数/未来函数/资金守恒/总开关）、R6 账本
（幂等/容错/审计无删除/冲正存档）、R7 锁与超时（PID 复用/锁隔离/三出口超时）、
R8 安全（POST 8/8 鉴权/JSON 纯净/XSS/错误泄漏）。

**测试**：294 → **296**（接线 6 + 尺度闸/条件闸/回放/缓存/表缺失/锁竞争等
新增用例，另有既有用例随断言收紧），本地/Pi 全绿，CI 通过。
**遗留 0 项**；依赖母库的 P5.5/母库日历未来部分仍按 `TODO-p5-p7.md` 挂起。

## 附录 A：价格消费点清单（R1.1，2026-10-27 摸排）

| 消费点 | 取数源 | 用途 | 尺度防护 |
|---|---|---|---|
| `cli.TradingBridge.price_map` | `bar_source.get_bars`（母库优先） | AI 执行价、收盘 NAV、run 结算 | R1.3 闸 + v0.7.5 执行门禁 |
| `engine.runner.price_map` | 实时报价优先，缺回退 `bar_source` | 引擎计价、熔断、总仓位 | 实时源独立（天然跨源） |
| `loop._execute_plan/_execute_actions` | `latest`(price_map) + `all_bars` | 下单价、涨跌停带 prev_close/ref | v0.7.5 `max_price_deviation`（跨时点） |
| `paper_broker.submit_order` 涨跌停带 | `order.prev_close`（调用方传入） | ±10/20/30% 带宽 | **同源自洽**——靠上游传干净价 |
| `broker.get_nav` | 入参 `current_prices` | NAV/回撤/日亏 | 依赖传价方；R1.3 保证 bars 源 |
| `loop` 计划基准价回填 | `all_bars[-1].close` | 计划 price | 同 bars 源（R1.3 保护） |
| `llm_context.recent_closes` | `_mother.daily_bars`（直连） | AI 上下文收盘 | **R1.3 闸覆盖**（同函数出口） |
| `ConditionRunner` 触发比价 | 实时报价 `quote["price"]` | 条件单触发 | 实时源独立 |
| 条件单 `cond_value` 挂单校验 | AI 输出（无基准） | 触发价 | **R3.5 尺度闸**（vs 现价带） |
| 手动 `buy/sell`/`preview` 缺价回填 | ~~`data_db`~~ → **`bar_source`（v0.7.7 统一）** | CLI 手动单缺价 | 统一后走 R1.3 闸 |
| `get_status` 持仓计价 | ~~`data_db`~~ → **`bar_source`（v0.7.7 统一）** | 面板/状态显示 | 与 NAV 记录同源 |
| `run` dry-run 信号 | ~~`data_db`~~ → **`bar_source`（v0.7.7 统一）** | 试运行预览 | 与实盘 run 同源 |
| `_detect_revisions` | `data_db`（有意保留） | 本地新旧版本对比（除权检测） | 必须比本地自身两版 |
| `data compare/coverage` 本地侧 | `data_db`（本职） | 双源对照的 local 臂 | 本职 |
| 回测/optimize | `data_db`（有意保留） | 已存日线快照 | 可复现性要求快照源；qfq 对齐后与 bar_source 同口径（corr≈1.0） |
| 面板现价展示 | `data/realtime.py` 腾讯实时 | 仅展示 | 不进账本 |

**R1.2/R3.1 走查发现（v0.7.7 已修）**：`get_status`、手动单/preview 风控计价、
dry-run 信号共 6 处直读 `data_db` 绕过 `bar_source`——同一账户两套价源
（NAV 记录用 bar_source、面板/手动单用本地），母库与本地一旦分叉即口径分裂。
已全部统一到 `bar_source`（本地回退行为不变）；回测的快照源有意保留并记录理由。

结论：**所有账本/风控价格最终只有两个源**——① `bar_source`（R1.3 跨源闸把门）、
② 实时报价（与 bars 天然跨源，引擎触发侧）。事故时的第三源（直连母库 hfq）
已被 v0.7.3 关闭、R1.3 闸兜底。

## 执行纪律

1. 顺序：R0 → P0（R1/R2）→ P1 → P2 → P3；P0 未完成不并行开 P1。
2. 每项完成 = 改动 + 测试 + 勾选；只审不改的要写“为什么不改”。
3. 小步提交打 tag（v0.7.6 起），每个 P 级收尾跑全量 + Pi 部署验证。
4. 发现新事故级问题（账本错/安全）：立即停下按事故流程处理，不继续清单。
