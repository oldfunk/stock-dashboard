# T68 · 审计判定复核 + engine/strategy 行为级断言（2026-11-03，Qoder）

派发原文（Batch 16）：

> **T68（Qoder，P1·T66 三切入点）**：2×2 归因缺格重跑；矩阵三路供给判定复核
> （静态读点之外的间接层）；`chat_with_retry` 与旧 `chat` 行为等价性；
> **engine/strategy 行为级断言**（第 5 件拍板）：合成配置 + 跑一轮 tick 的行为测试
> （"接了线但路径不跑"类别，如 legacy 路径在 rqalpha 底座下）；`report-t68.md`

四条：三条做完，一条按"花额度先报备"停在门口（§5）。

---

## 1. 逐条核销

| 派发点 | 状态 | 落在哪 / 证据 |
| --- | --- | --- |
| 矩阵三路供给判定复核（间接层） | ✅ 改成了推导 | `wiring_audit.detect_helpers()`：第 1 层 = 体内写 `cfg.timeout/max_tokens/extra_body` 且调 `chat`；第 2 层起 = 把**自己形参**原样交给已知层的薄壳。原本人工登记的 `LLM_HELPERS` 降级为期望值，推导与期望不一致 ⇒ 门禁红 |
| `chat_with_retry` 与旧 `chat` 行为等价性 | ✅ 6 例 | `tests/test_audit_t68.py`：只重试超时（总次数 = retries+1）、非超时错一次即抛、1210 降 `level:low` 并把预算抬到 ≥4096、`_ask_llm` 位置参数语义不变且返回物仍可 JSON 解析、重试期间预算只升不降 |
| engine/strategy 行为级断言 | ✅ 21 例 | `tests/test_engine_behavior_t68.py`（真跑 tick，看结果与账本） |
| 2×2 归因缺格重跑 | ⏸ 待报备 | 需要 2 次云端请求；脚本 `/home/nec/tmp/t63_grid.py` 就绪，见 §5 |

## 2. 复核把我自己的三个缺陷照出来了（这才是"复核"的价值）

1. **子串碰撞**：`_find_call(body, "_ask")` 用 `body.find("_ask(")` 会命中
   `def llm_ask(` 的尾巴 ⇒ 问答那一行被读成"形参列表"，四个字段全报漏传（假阳）。
   改成 `(?<![\w.])name\(` 的词首匹配。
2. **函数表按裸名建键**：`run` 在 `cli.py` 与 `agent/loop.py` 各有一个，
   `setdefault` 让后出现的那份 body 永远查不到 ⇒ 矩阵**静默少一行**。
   改成 `文件::函数名` 索引。少一行比错一行更糟——没人会去查没出现的东西。
3. **作用域泄漏**：传递层推导里 `fpath` 沿用了上一层循环的残值，
   于是 `_ask_llm` 的行被标成 `provider.py`（错文件）。测试直接断 `{good.py, bad.py}`
   两个文件都在，把它钉住。

还有一个方向性错误值得记：我第一版把"任何调用了层的函数"都算作层，
`run/tick/main` 全变"间接层内部"，**供给点一个都不检查**——矩阵当场从 13 处"断层"
变成什么都不报。薄壳的判据必须是"转传自己的形参"，业务函数传 `self.cfg.llm_*`
是要被检查的对象，不是被豁免的层。

## 3. 行为级断言测到的东西（静态读点给不了的）

`tests/test_engine_behavior_t68.py` 全部走"真跑一轮 `EngineRunner.tick()`/`run_loop`"，
每条都是"改一个键 ⇒ 结果不同"，而不是"这个键有没有被引用"：

| 键 | 断言方式 | 结果 |
| --- | --- | --- |
| `engine.enabled=false` | cron 用的 `--once` 路径 + 会触发的规则 | **不下单**，且仍落 `engine_triggers.status=blocked_disabled` 留痕（拦下的意图要能复盘） |
| `max_orders_per_day` | 同规则连跑两轮 | 第二轮 skipped「日内笔数上限」；把键放大到 2 后第二单成交 ⇒ 确实是这个键在挡 |
| `per_symbol_cooldown_sec` | 0 vs 900 | 900 时 skipped「冷却中」 |
| `min_ticket_cny` | 50000 vs 500 | 大额时 skipped「低于最小下单金额」 |
| `max_turnover_per_day` | 1000 vs 100 万 | 小额时 skipped「日内金额上限」 |
| `rule.enabled` / `rule.once` | 关开关 / once 连跑 | 分别是「不触发」与「当日只一次」 |
| `universe` + `universe_max` | 池 5 只、cap=2 | 只订阅前 2 只；`universe=list` 时不读池 |
| `interval_sec` | `run_loop(max_ticks=1, interval_sec=3600)` | 立刻返回一轮 ⇒ **该键在 cron 的 `--once` 路径上不生效**，写死成测试免得有人改了以为管用 |
| `strategy.short/long_window` | 锯齿末跌序列 | 短窗(2,4)出死叉、长窗(5,10)无信号 ⇒ 窗口真的进计算 |
| `strategy.buy/sell_volume` | 金叉/死叉两个方向 | 300 进买、700 进卖（两个键各写一遍就会有一个不生效） |

**这里我差点造出一个假发现**：读 `__main__` 时我看到 `enabled=false` 只在守护入口查、
而 cron 走 `--once`，就判它是"停机开关按了机器还在转"，并已在 `run_loop` 加了早退守卫。
跑测试前读了 `EngineRunner._execute` 才发现那里早有守卫，注释还写着同一条洞
（"原来只在守护入口查这个开关，cron 走单轮时绕过了它"）。我的守卫是冗余的，而且更糟：
它会把 `quote_log`、持仓峰值刷新、心跳轮次和**「被拦下的意图」留痕**一起吞掉。
已回滚，并把这条假设改写成一条**证明它不成立的测试**。行为级断言的价值正在这里——
它既能证明洞存在，也能证明洞已经补上，而我这次的收获是后者。

## 4. "接了线但那条路径不跑"这一类现在的处理

M3 切换后 `executor=rqalpha`，legacy 撮合分支（`broker.submit_order`）在生产不再执行。
它的键仍在被读（`risk_kwargs`、`strategy.*`），所以静态审计不报——这类洞**只有行为测试
能抓**，但也要先定义"生产该跑哪条路径"。做法：本轮把可判定为"当前底座下不执行"的
一条（`interval_sec`）写成显式测试注释；其余留作清单：
- `strategy.*` 四个键只被旧线 `run`/回测路径消费，M3b 的 `rq tick` 不读它们；
- `risk.*` 仍被消费（`account_guard`/`check_signal` 在判定侧），不算死；
- `engine.interval_sec` 只在守护模式生效（现网是 cron 单轮）。
要不要据此调整这些键的归属（比如给 strategy 段加"仅旧线/回测用"的标注），是文档口径题，留给主 AI。

## 5. 2×2 缺格（待报备）

结论不依赖它（现有三点已能分离"上下文"与"输出上限"两个变量），但补上更硬。
跑它要 2 次云端请求（`timeout 120s` 级别、每次几百 token 输出）。按批复
"今后同类花额度操作先报备再做"——**在此报备，等一句"跑"**：
命令 `ssh nec 'cd /home/nec && venv/bin/python /home/nec/tmp/t63_grid.py'`，只读账本副本、
不写 `op_log`、生产 md5 不变。

## 6. 交付物与验证

`tests/wiring_audit.py`（推导式间接层 + `fk()` 文件限定 + `_find_call` 词首匹配 +
`Scan.params/bodies` + 漂移门禁）、`tests/test_audit_t68.py`（9 例）、
`tests/test_engine_behavior_t68.py`（21 例）。全量 **738 passed / 9 skipped**；
`python tests/wiring_audit.py --quiet` → 断层：无（退出码 0）。生产未动。
