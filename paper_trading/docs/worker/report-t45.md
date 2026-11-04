# report-t45 · M3 切换 runbook（Qoder，P0·Batch 9，产出 `docs/migration-m3.md`）

分支 `worker/t45`（堆叠 `worker/t42`→`t40`→`t41`，**待 review 按序合并**）。
纯文档，未改产品代码；本任务不做实现（切换操作按 §5.6 由主 AI 亲执行）。

## 0. 一句话结论

runbook 写完了，但**它的第一节是"现在还不能切"**：我把 T45 的活读成
"把切换需要的前提写成可核销的清单"，读码结果是 **4 个缺口**，其中两个是 P0 级——
**新线根本不写账本**（G1/G2），而 §5.6 依赖的**"config 开关一键切回"不存在**（G3）。
按现状换 cron，得到的是"每天算完就丢、没有 NAV、面板断供"的空转线。
这不是拒绝执行，而是 runbook 里最该有的那一行字。

## 1. 逐条核销清单（`docs/WORKER.md` Batch 9 · T45 原文）

| 派发原文 | 状态 | 落地点 |
|---|---|---|
| 「参照 `migration-nec.md` 的经验与 checklist 传统，产出于 `docs/migration-m3.md`」 | ✅ | 新建 `docs/migration-m3.md`：沿用它的**引用块开头（背景/目标/最高风险）→ 规格对比表 → 已拍板 → 逐条核销表 → cron 代码块 → 三阶段 → 执行记录表 → checklist 增补**结构；顺序铁律直接引原文行号 |
| 「切换日流程：账本归档（`paper_account.db` 冷备+只读保留）→ 切换日新开账（新引擎账本起点=切换日 NAV 对齐，历史只读可查）→ cron 换线（旧引擎 5 条停、新引擎线启用，顺序铁律同 Pi→NEC：先停旧再启新）→ 验证步（首单/费用/T+1/NAV/面板/留痕）」 | ✅ 全部写成可执行步骤 | runbook §4 Phase 1（①-⑤ 带命令与 `crontab` 备份名）、§3 对象清单 19 条、§6 验证步 7 项 |
| 「**回退方案**：config 开关一键切回旧线的精确步骤 + 回退后账本衔接说明（新开账 vs 归档账的边界）」 | ✅（并上报：开关不存在） | §5.1 现状下的 6 步手工回退（含"禁止跨账合并"的理由：旧线 `avg_cost` 含费、rqalpha `avg_price` 不含费）；§5.2 最小开关规格（含**必须加进 `engine/config.py:27-37` 的 `DEFAULTS` 白名单，否则新键被静默丢弃**——这是 `config.py:59/64` 的实测行为）；§5.3 账本衔接边界 |
| 「逐条 cron/服务/路径清单对照（漏一条=漏一个功能——T21 教训）」 | ✅ | §3 表 19 行 + §8 继承坑位；cron 五条按 `AGENTS.md:53-70` 原文列出 |
| 「`report-t45.md`；**切换操作本身由主 AI 亲执行**」 | ✅ | 本文件；runbook §7 是留给主 AI 填空的执行记录表 |

## 2. 四个缺口（P0 级，逐条带证据，全部是"读码 + 现网核对"得来）

**G1｜新线不写账本。** `rq day` 的产物只有 `--state-dir` 里的 rqalpha persist 与
`evidence_<date>.json`（`rq_engine/runner.py:216-218`）；整个 `rq_engine/` 目录里
唯一碰 `PaperBroker` 的地方是**影子对照**，而且指向 `--shadow-dir` 的库
（`shadow.py:48-54/72`）。⇒ 引擎撮合出来的成交没有任何一条路径写进
`paper_account.db` 的 `orders/fills/positions`。

**G2｜新线没有 NAV 记录器。** `record_nav` 的调用点全在旧线
（`agent/loop.py:524/1171`、`cli.py:371/442`、`main.py:133`）；
`runner.py:90` 那句"指标继续由我们的 nav_history 记录器出"是**意图注释，代码不存在**。
⇒ 切换后面板净值（`nav_history`）当天就断供。

**G3｜"config 开关一键切回"不存在。** 全仓 `use_rqalpha|execution_backend|engine_backend|backend`
零命中；`engine/config.py:27-37` 的 `DEFAULTS` 里没有任何底座选择键；
这个说法只在计划文本里（`refactor-plan-open-source.md:97` M2 回滚列、`:320`、§5.6"回退保障"）。
§5.6 还写「主 AI 亲执行（… **回退开关验证**）」——要验证的东西得先被建出来。

**G4｜归档/新开账工具不存在。** 只有手工冷备先例
（`deploy/incident-20261027-fill-correction.py:23-25` 的 `shutil.copy2`、
`data/db_manager.py:75/105` 的 `conn.backup`）；`nav:revision` 这个 action
只出现在 `ROADMAP.md:120/214`，代码里没有写入点。
⇒ 我在 runbook 里给的是手工步骤（Phase 1 ②③ + §5.1 ③），但"切换日新开账 + NAV 对齐"
这种钱类动作不该靠现场手敲，建议主 AI 派一件小工具（T45 不越界实现）。

**如果主 AI 决定 G1/G2 先不补**：那 M3 的语义应显式改写成"影子期正式化（新线只对照、
旧线继续下单）"，而 `refactor-plan-open-source.md:98` 的 M3 行现在写的是
"cron 切 rqalpha 常驻（一天一进程）"。两处文本必须对齐，别让 cron 与实现各说一套。

## 3. 读码过程中发现的、需要在切换设计里显式决定的两件事

1. **`agent tick` 一条命令同时管两件事**：09:30-10:00 **执行**当天计划、16:30-18:30 **做**下一日计划
   （`agent/loop.py:684-690` 的窗口语义）。⇒ "停旧线"如果整条停掉 `agent tick`，
   会连带**断掉 AI 计划链**（收盘做计划）。runbook §3 #2/#3 把这两半分开写了：
   停执行半边、保留做计划半边，或者给 tick 加"只计划不执行"的开关——**这条要主 AI 选**。
2. **新线"一天一进程"与每分钟 cron 的形状冲突**（G-T13-9 `_SESSION_USED`）。
   新线不能沿用"每分钟一跳"的排程；`SessionGate` 还会拒绝同 state 目录同日重跑。
   §3 #8 给的建议是 09:35 + 15:05 两轮、**每轮独立 state（或按日分目录）**，
   并且单独一把锁（`var/rq.lock`），不与旧线共用（否则互相挡）。

## 4. 我没有做的（边界）

- 没写实现（G1/G2/G3/G4 的代码）、没装 cron、没打 tag、没碰现网任何文件；
  本任务只读码 + 写文档，NEC 一次也没连。
- 没改 `config.yaml` / `AGENTS.md` / `docs/ROADMAP.md`：计划文本的口径修正留给主 AI。

## 5. 交接给 T46（切换后检查体系）

runbook §6 只列了 7 项"当天必须看"的验证步，T46 要把它扩成
第一小时/第一天/第一周三段 + `m3 verify` 命令（当前仓库里**没有** `m3` 子命令，
`WORKER.md` 里是待办），并且每项标"红了怎么办"。
我在 runbook 里已经钉死一条边界：**任何钱类项红 ⇒ 立即回退**，回退动作以 §5.1 为准。
