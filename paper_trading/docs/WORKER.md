# WORKER — 协作协议与任务箱（主 AI ↔ 员工 AI）

> 你是本仓库的员工 AI（打下手 + 找问题）。主 AI（我）负责交易代码的最终把关、
> 部署与发布。本文件通过 git 同步——**开工先 `git pull --ff-only`，完工推回去**。

## 协作协议

> **审查分工（2026-10-27 用户定）**：两位同事与我并行工作，**主要职责是
> 审核我的工作**，防止我走歪或丢重要细节——
> 同事 A（Qoder）：选型/spike/方案反驳审查（T5'、T6 报告作者）；
> 同事 B（Hermes）：现网 correctness 独立审查（H-1..H-8 报告作者）。
> **主 AI 的代码与决策推送后，须经至少一位同事复核并在本仓库留意见**
> （报告或 `questions.md`）；有异议先改再合，不许静默通过。

| 事项 | 约定 |
|---|---|
| 同步 | 开工前 `git pull --ff-only`；分阶段提交，不要攒大包 |
| 报告 | 写 `docs/worker/report-<任务号>.md`（如 `report-t1.md`），按 P0/P1/P2 分级，每条带 **文件:行号 + 复现/论证思路** |
| 状态 | 完成一项就把本文件对应复选框勾掉（`- [x]`），提交信息含任务号 |
| 文档/测试改动 | 可直接提交 main，前缀 `docs(worker):` / `test(worker):`；**禁止 force push、禁止删历史** |
| 产品代码改动 | **一律开分支** `worker/<任务号>`，push 分支后在报告里写明"待 review"，由我 review 合并；不要直接改 main 上的 `paper_trading/**` |
| 交付前自检（2026-10-29 新增，Qoder 裁决 4 漏落地教训） | 交付 checklist 必须含**「对照 questions.md 批复 + refactor-plan 裁决逐条核销」**——每条裁决标注"已落地于 XX 文件/测试"或"不适用+理由"；**禁止拿自己的实现反解拍板**（实现与裁决冲突时以裁决为准，冲突本身要上报） |
| 疑问/阻塞 | 写 `docs/worker/questions.md`（追加式），不要自己猜着改设计 |

## 红线（违反即事故）

1. **不跑任何写账本的命令**：`agent run/tick/fire`、`buy/sell`、`corporate apply`、
   面板 POST——只读 CLI（`status/nav/history/data coverage/data compare`）可以。
2. **不碰 Pi、不碰 secrets.local.json、不部署、不打 tag、不改 cron**。
3. 产品代码只提案不直改（见上表分支流程）；`config.yaml` 的默认值不许动。
4. 不接券商实盘、不做 tick/HFT/期权/做空（用户决策，勿扩展）。
5. 发现**账本错误/安全漏洞**级问题：立即写进报告标 P0 并 `questions.md` 通知，
   不要自己修，等我处理。

## 背景速读（开工前必读，按序）

0. `docs/founding-mandate.md` —— **立项约束偏差记录（必读第 1 位）**：本项目
   立项要求"用成熟开源量化项目搭建"，当前为自研，结论待用户拍板
1. `docs/incident-2026-10-27.md` —— 刚发生的交易事故复盘（尺度污染，已冲正）
2. `docs/TODO-code-review-20261027.md` —— R0–R9 全量审查清单与结论（**已知问题都在这**，你的价值是找它没覆盖的）
3. `AGENTS.md` —— 项目硬规则（数据不许编造、fail-closed、可审计……）
4. `docs/worker/report-defect-attribution.md` —— **代码问题的量化归因**（L1 重造面积 44.6%／钱类缺陷 83% 可被框架消灭／61% 缺陷在 L2 框架管不到）＋ §6 M1 验收靶子；L3 双审的分工按 §7

---

## 任务箱 · Batch 1（2026-10-27）

### T1 对抗性重审：执行链与账本（找我没找到的）

- [ ] 范围：`paper_trading/agent/loop.py`、`broker/paper_broker.py`、
  `risk/risk_manager.py`、`engine/ai_conditions.py`、`engine/conditional.py`
- [ ] 视角：带着事故复盘的"尺度/同源自洽/接线断层"三类模式，**再找别的类别的**
  （状态机漏洞、幂等缺口、竞态、异常路径吞错、边界数值）
- [ ] 产出 `docs/worker/report-t1.md`；**不改代码**
- 验收：每条发现可论证（贴代码链路），且确认不在 R0–R9 清单里

### T2 文档一致性清扫（可直接修文档）

- [ ] `README.md`、`docs/*.md` 里的命令示例/配置键/默认值 vs 代码实况逐条对照
- [ ] 过期内容直接改（前缀 `docs(worker):`），拿不准的进 `questions.md`
- 验收：抽查 3 条修改均与代码一致；报告列改动清单

### T3 测试覆盖盘点（可补纯测试）

- [ ] 盘点本周新增/改动大的模块的测试缺口（尤其 `integration/mother.py`、
  `cli.py` 新子命令、`agent/events.py`）
- [ ] 产出 `report-t3.md`（缺口表：模块/函数/风险等级）；能补的补
  （前缀 `test(worker):`，只加测试不改产品代码）
- 验收：`python -m pytest -q` 全绿（当前基线 **296 passed, 1 skipped**）

### T4 路线图待办可执行性评估（纯调研）

- [ ] 读 `docs/ROADMAP.md` §2–§4 未勾项，挑出"**不依赖母库、现在就能做**"的，
  写成带验收标准的任务草案（`report-t4.md`），标注预估改动面
- 验收：每条草案含"改哪些文件/怎么验收/依赖什么"

### T5' 迁移方案反驳审查 + 桌面 spike（P0，替代原 T5）

- [ ] 背景变化：用户已拍板"量化核心必须用成熟开源框架、零自研"；主 AI 已产出
  迁移方案 `docs/refactor-plan-open-source.md`（vnpy + rqalpha 双框架）
- [x] **反驳审查**：通读方案，专门找漏洞——技术假设错误、遗漏的能力点、
  风险低估、更该入选的框架；产出 `docs/worker/report-t5.md`，
  每条反驳要有依据（文档/代码/检索），**不许和稀泥**（→ `report-t5.md`，
  已促成 §7 拍板作废与 rqalpha 单引擎定案）
- [ ] **桌面 spike**（用户电脑，隔离 venv，不污染现有环境）：
  建 Python 3.13 venv（如 `C:\Users\yanzh\venvs\refactor-spike`），
  `pip install vnpy rqalpha`，跑两个 hello：① vnpy EventEngine+撮合引擎
  下单→成交→账户查询（无 GUI）；② rqalpha `run_app` 空策略回测
  （数据可先用其自带 fixture/示例）；记录版本号、坑、安装体积
- [ ] 红线：不碰 `~/paper-trading` 生产环境、不碰现有 venv、不改产品代码；
  spike 代码放 `spikes/`（新建目录，gitignore 可选）或报告内嵌
- 验收：反驳报告 + spike 实录（含 vnpy 4.5 在 3.14 vs 3.13 的安装差异确认）

---

### T6 akquant 挑战者 spike（P0，半天，决定终选翻不翻盘）

- [ ] 背景：选型主线已定 **rqalpha 单引擎**（`docs/refactor-plan-open-source.md` §7），
  但 akquant（MIT + AKFamily 同门 + Rust）是唯一可能翻盘的挑战者——
  用**两个否决性问题**验证，不是全量打分
- [x] 问题①**aarch64 可装性**：PyPI/官方是否提供 manylinux aarch64 轮子
  （Rust 内核没有轮子就要交叉编译 = 否决）；在 Pi 隔离 venv
  （`/home/pi/refactor-spike`，与你之前 spike 同一环境）实测 `pip install akquant`
- [x] 问题②**模拟撮合/T+1 能力**：源码取证——有没有常驻/模拟盘撮合（不是纯
  回测）、T+1 冻结语义（`tplusone`/`Position.available`）、涨跌停拒单、
  卖出印花税、100 股整手；每条给 文件:行 证据
- [x] 产出 `docs/worker/report-t6.md`：两问各有明确是/否 + 证据；
  结论格式「akquant 反超 / rqalpha 定案」二选一，不许含糊
- 验收：aarch64 实测结果 + 撮合能力源码取证；**不改产品代码、不碰生产环境**

**状态**：Batch 1 T1–T4 + T5' 已交付；**T6 已交付 → akquant 不反超（缺涨跌停带），rqalpha 定案**；主 AI 已完成 H 系修复（`caa7371`）与 M-1/M-2/M-3 三缺陷修复，全量 **316 passed**。
  注：T6 的两处未做项见 `report-t6.md` §3（Pi 真机安装由主 AI 一枪收口；akquant 策略执行验证约半天，不在否决路径上）。

### T7 rqalpha 数据源适配器取证与口径归口设计（Qoder，P0，M0b 关键路径）

- [ ] 背景：T6 判决 **rqalpha 定案**；M1 第一步 = `AbstractDataSource` 接我们的数据
- [ ] 在 spike venv（`C:\Users\yanzh\venvs\refactor-spike`，rqalpha 6.4.2 已装、
  bundle 已下）读源码，枚举 `AbstractDataSource` 必须实现的方法/属性（**文件:行**）
- [ ] 逐方法映射到我们的数据（SQLite `daily_bars` / bundle 元数据 / 交易日历）
- [ ] **口径归口设计（refactor-plan §7.3 红线，本任务核心）**：论证两条路线的
  除权日行为——路线 A「我们的 qfq 序列入 + 因子置中性 + bundle 只供停牌/ST/
  日历/分红元数据」 vs 路线 B「raw 价 + bundle 复权因子」；必须回答
  "**除权日净值/持仓会不会错**"，举一个真实分红案例逐步推演
- [ ] 产出 `docs/worker/report-t7.md`：接口表（文件:行）+ 数据流 + 难点清单
  （哪些方法我们给不了、退路是什么）+ 口径结论
- 验收：**不含产品代码**；口径结论有除权日推演

### T8 H 系修复复核（Hermes，P0，L3 审查）

- [ ] 复核 commit `caa7371`（H-1/H-2/H-4/H-6/H-7 修复 + H-3/H-5 锁定测试）：
  修复是否正确、测试是否真能红（mutation 视角）、**有无过度拦截**
  （正常下单/挂条件路径是否被误伤）
- [ ] 产出 `docs/worker/report-t8.md`：逐条 verdict（修复正确 / 需改 / 误伤）
- 验收：只审查不改码；发现 P0 立即 `questions.md` 喊停

**缺陷量化基线**（主 AI 已采纳）：`report-defect-attribution.md`——
L1 44.6% / 钱类 83% 框架可灭 / **L2 61% 是迁移后的主战场**（1.53 缺陷/KLOC）；
其 §6 的 M1 七条验收门禁已并入 `refactor-plan` §5.1。

---

## 当前状态与任务分配（主 AI 更新 2026-10-27 深夜 · 用户指示：主 AI 减负、多派活）

- [x] T8 已交付（`report-hermes-t8-20261027.md`：H-1/2/4/6/7 修复通过、
  M 系通过、H-5 仍开 → 主 AI 已裁决 **consume-once** 并修复
  （`test_plan_consumed_even_if_execution_crashes`），分歧记录见 `questions.md` 批复节）
- [x] **T7 已由主 AI 接管并完成**：`docs/m1-data-adapter.md`——store 接口取证
  （adjust.py 读时复权 / position_model 分红入账）+ 口径归口定案（回测用 bundle
  自洽三件套、实时线用我们两源+尺度闸）+ **M1-A 端到端跑通**
  （`spikes/rqalpha_ma_backtest.py`：trades=108、NaN 门禁实弹验证、EXIT=0）
- 全量测试 **317 passed**（本地+Pi 双绿，v0.7.10 已部署）

### 已派发任务（认领即勾选；主 AI 只做最终裁决/门禁签核，不亲自实施）

- [x] **T9（Qoder，P0，M1-B parity 实施级）已交付并合并**（`report-t9.md` +
  `spikes/parity_t9.py`）：三层归因实验，判定原容差结构性不可达 → 主 AI 已裁决
  **D1–D4 + 两级门禁**（`refactor-plan` §5.2/§5.3）
- [x] **T10（Qoder，P1）已交付并合并**（`report-t10.md` + `spikes/exdate_t10.py`）：
  route B 除权日验证通过；rqalpha 过户费缺失/分红无红利税实测证据
- [x] **T11（Hermes，P1）**：H-5 consume-once 裁决再复核（`questions.md` 批复节）；
  无异议，见 `report-t11.md`
- [x] **T12（Hermes，P0，与 T9 交叉）T15b1/T16 已交付**（见 Batch 3 勾选）；
  **T12 本体仍开且扩权**：
  - [ ] **收口 T9 坦白的 26 笔未归因缺口**（`report-t9.md` §3.6：候选=once 每日
    语义/涨跌停一字板拒单/T+1 冻结/多次触发——用 `spikes/parity_t9.py` 复跑归因）
  - [ ] **陈旧引擎审查移交的 11 条重定位**（Qoder 移交：符号/行号已失效，
    原始清单在 `C:\Users\yanzh\AppData\Local\Temp\qoder_engine_notes.md`，
    按当前 HEAD 逐条判"仍在/已修"，不许按旧行号改码）
  - 产出 `report-t12.md`

**主 AI 保留职责（不再亲自写实施）**：L1 交易代码最终把关（缺陷归因 §7）、
T9/T10 结果裁决与 M1 门禁签核（§5.1 不绿不合）、P0 事故响应、M2 方案拍板。

---

## Batch 3 · 挑战性长线任务（主 AI 派发 2026-10-27 深夜）

> 验收备注：T11 ✅ 已交付合并；**T9/T10/T12 无交付物，继续挂起**（口头完成不算，
> 推送到仓库才算）。Batch 3 与挂起项并行，认领即勾选。

### T13（Qoder，P0·M2 关键路径·长线，预计 2-3 天）rqalpha 模拟盘运行模式取证 + 原型

- 挑战点：回测的 `simulation_event_source` 是**窗口内回放**，而我们要的是
  **常驻模拟线**（Pi cron 世界：每日新 bar 灌入、T+1 跨日结算、断点续跑）。
  rqalpha 自述有"实盘模拟"——它到底怎么喂新鲜数据？事件源可否替换？
- 任务：
  - [x] 取证 rqalpha 的 live/sim 运行路径（源码 `文件:行`：事件源注册、
    数据源热更新、日终结算触发），回答"持续运行一天模拟需要什么"
  - [x] 原型：用我们 SQLite 的**历史某日**数据当"今日"，跑通一天模拟
    （下单→成交→日终结算→T+1 冻结），数据不缺任何字段
  - [x] 明确断点续跑方案（Pi 进程每天重启，状态怎么恢复）
  - [x] 产出 `docs/worker/report-t13.md`：可行性结论 + 原型代码（放 `spikes/`）
- 验收：原型可复现（命令+输出）；**不能跑通就写清卡点**，不许绕过
- **交付（Qoder 2026-10-28，`worker/t13` 分支，待 review 不合并）**：
  用**生产 data.db 真实日线**跑通模拟日 + 跨进程续跑，**21/21 门禁全绿**
  （`spikes/live_sim_t13.py selftest`）。**M2 三个 P0 卡点**：① 我们的
  `daily_bars` 是**前复权**，rqalpha 契约要原始价（84.3% 标量-日不符，最坏
  低估 67.6%；再乘 bundle 因子＝双重计入，D3 事故家族）；② **6.4.2 core 里没有
  persist provider 实现**（`interface.py:643` 只有抽象类，CLI 无 `-p`），断点续跑
  必须我们自带；③ bundle 之后的新鲜日**原始价无法自证**（7/38 标的因子外推，
  最大 1.0459；且 2026-10-28 只有 29/38 有 bar）。另有 5 条常驻线特有的 P1
  （跨进程重复结算打爆 sys_analyser、同日重跑重复成交、band 缺数据 fail-open、
  前端拒单不落 Order、进程内重入被日志掩盖真因）。详见 report-t13 §4/§5/§6。

### T14（Qoder，P1·长线）研究流水线移植设计：grid/walkforward → rqalpha

- 挑战点：现 `engine/optimize.py`/`walkforward` 的纪律（网格只是筛选器、
  必须样本外、0 笔成交不参与排名、基准超额）要在 rqalpha 上原样复活，
  且叠加 **NaN 门禁 + 我们的 provenance 指纹** → 可复现实验注册表
- 任务：
  - [x] 对照现实现逐语义映射（每条纪律 → rqalpha 对应物或缺口清单）——
    `report-t14.md` §2 八条纪律逐行给现代码 `文件:行` + rqalpha `文件:行`
  - [x] 设计实验注册表格式：bundle 版本 + 参数哈希 + 数据指纹 + 指标有限性证明
    —— §4 `experiments` 表 + 状态机（`ok/excluded_zero_trade/invalid_nan/failed`）
    + `exp_id = hash(bars|params|days|engine_ver)` 的可复现性判据
  - [x] 产出 `docs/worker/report-t14.md`：映射表 + 缺口 + 实施步骤（先设计不写码）
- 验收：映射表每行有现代码 `文件:行` 出处 ✅（§7 出处自检）
- **交付要点（Qoder 2026-10-28，`worker/t14` 纯文档）**：
  ① **rqalpha 6.4.2 core 没有任何寻优设施**（全库 grep `optimize` 零命中，
  只有 `utils/concurrent.py:40` 一个带进度的进程池）⇒ 网格/排名/0 笔剔除/样本外切分/
  基准超额**五件纪律全是我们 L2 的活**，移植只把"评估一个变体"换成一次 rqalpha 会话；
  ② 基准建议**自算等权买入持有**（用同一段 rqalpha 会话跑 buy-and-hold），
  因为 analyser 的 benchmark 是指数口径且在 bundle 覆盖不足时**直接抛错**
  （`mod.py:203`，T13 §3 S1 现场撞过）；
  ③ **一 trial 一进程**（G-T13-9），否则被 `Environment` 单例清空后的日志掩盖真因；
  ④ 顺带发现 **P1 缺陷**：`walk_forward()` 承诺的过拟合量度其实没算
  （`optimize.py:251-252` 的 `oracle` 与 `picked` 是同一行、`picked` 全文未被使用），
  修法见 §6，建议与实施步骤 S3 合并做。

### T15（Hermes，P0·长线，预计 3-5 天）L2 对抗性测试战役——狩猎新缺陷

- 背景：缺陷归因 §7 说 L2 是主战场（**1.53 缺陷/KLOC**，`agent/loop.py`+
  `cli.py`+`integration/mother.py` 三文件占 L2 的 42%）。你的 H 系列已经证明
  能在全绿测试下找到真 bug——这次是**系统性狩猎**，不是抽查
- 任务（每周节奏，按批交付）：
  - [ ] 批次一：`agent/loop.py` 状态机对抗——`_plan`/`_execute_plan`/
    `_execute_actions` 的部分失败、动作交错（条件单与直接单同标的）、
    cap 边界（正好 n、n+1）、计划过期与消费竞态、dry-run 与实盘路径分叉点
  - [ ] 批次二：`integration/mother.py` 降级矩阵对抗——缓存与 schema 变更交错、
    尺度闸与绝对闸同时触发、local/mother 模式切换中途的 provenance 一致性
  - [ ] 批次三：`cli.py` 编排对抗——锁被抢/超时半途、`--json` 错误路径、
    子命令参数非法组合
  - [ ] 每批：新发现 → `report-t15-batchN.md`（H 系列格式：文件:行+复现+风险级）
    + 能补的回归测试直接 `test(worker):` 提交
- 验收：每批至少给出"查过什么、为什么这里没有 bug"的覆盖清单（阴性结果也算交付）

### T16（Hermes，P1）合规能力回归套件 `tests/test_compliance_regression.py`

- 背景：`refactor-plan` §5.1-6 要求"6 条钱类缺陷逐条写成回归测试，迁移前后
  各跑一次，同一份测试证明 L1 替换没把合规能力换丢"——这份套件就是**迁移的
  合同**，M1-B 验收要引用它
- 任务：
  - [ ] 把 6 条钱类（事故本体/价源绕过/条件无尺度闸/H-1 fail-open/H-4 退化/
    H-7 旁路）+ 10-27 事故日场景，各写成独立命名的用例
  - [ ] 每条标注溯源（对应 incident/报告章节），并设计"rqalpha 版对照位"
    （同一场景在新引擎上怎么验，留 TODO 占位）
  - [ ] 产出提交 `test(worker):` + 在报告里说明每条的通过语义
- 验收：全绿；迁移 M1-B 时此套件必须**双跑**（旧引擎+新引擎）

### Batch 3 追加（主 AI 裁决 D1–D4 后派发，2026-10-28）

- [x] **T15 批次一已交付**（`report-t15-batch1.md`：阴性结果+3 测试）→ 批次二/三继续
- [x] **T16 已交付**（`tests/test_compliance_regression.py` 166 行，全绿）
- [ ] **T17（Qoder，P0·小）**：修「金额不足一手 → 静默 skipped」（T9 §3.2，
  14 笔+连带 15 笔，违反纪律 4）——`engine/backtest.py` 的 skipped 已有归因，
  缺的是**报告化**：`metrics` 显式输出 skip 分类计数 + 面板/CLI 可见；
  补一条"必须非静默"的回归测试；`fix(worker):` 提交
- [ ] **T18（Qoder，P0·D1 关键实验）**：分钟频率可行性——bundle 是否含 1m
  数据（`stocks.h5` 外的 minute 文件/配置）；rqalpha 用分钟频率能否实现
  **T+1 开盘撮合**（信号 T 收盘 → T+1 首分钟成交）；出结论：
  可行（回测线口径统一路径）/不可行（M2 模拟线为唯一 T+1 路径）→ `report-t18.md`
- [ ] **T19（Qoder，P0·S 级门禁实施）**：按 `refactor-plan` §5.3 实现 S 级
  信号集合对照——基于 `spikes/parity_t9.py` 改造：同价源同锚（route B）、
  股数语义、±1 交易日匹配、匹配率统计 + 未匹配逐笔归因表；
  输出 `report-t19.md`（M1 签字必备件）

### 生产迁移 NEC（主 AI 派发 2026-10-28，Pi 过劳实锤：load 4.4 / free 59MB / 连续自重启）

- [x] **T20（Qoder，P0·迁移 Phase 0 环境搭建）已实证验收**（主 AI 直测）：
  NEC venv Py3.12.3、**326 passed, 1 skipped** 与基线一致、repo 就绪；
  **报告已补交**（`report-t20.md`）：3.12 免装 3.13、零 cron、面板 200；
  **P0 发现：NEC 是 x86-64-v1 CPU（Core2 Duo T9900）**——numpy≥2.4 wheel
  导入失败、未 pin 版本的 fresh install 会坏（现生产环境因已装好不受影响，
  但新机部署必须 pin；rqalpha 实验环境若上 NEC 也受此约束）→ 已转 T30 处置
- [x] **T21（Hermes，P0·迁移 runbook 审查）已交付并采纳**（`report-t21.md`）：
  P0 回退顺序错误已修入 runbook；P1（sha256/扩展表/local 配置/凭证提醒）全部落地
- [x] **生产迁移已完成（2026-10-28 14:47，主 AI 亲执行，用户指示提前切换）**：
  Pi 静默（0 进程 0 cron）、NEC 生产运行（engine cron 分钟级正常触发、
  风控熔断态正确传递、dashboard systemd 200）；数据 sha256 逐字节一致；
  执行记录见 `migration-nec.md`；**Pi 冻结为回退副本，观察期 2-3 交易日**

---

## Batch 4 验收（主 AI 2026-10-28 晚）

- [x] **T24（Hermes）已交付并采纳**（`report-t24.md`）：**P0 成立**——SIGKILL/
  SIGTERM 不执行 finally，消费滞留 finally 时计划残留 pending → 下 tick 重跑
  已成交动作。**主 AI 已修**（commit `fb6cfcc`）：消费前移到 `_execute_plan`
  第一句（顺带堵掉解析段在 try 外抛异常同样残留的漏洞）；`finally` 删除；
  mutation 验证——消费挪回旧位后**两个测试真红**。
  **对 Hermes 建议的裁决**：其"running 状态 + SIGTERM handler"两方案均**不需要**
  （消费前移后被杀时计划早已消费，状态机与信号处理皆无必要——更简单且覆盖全信号）。
- [x] **T23（Hermes）实现验收通过，测试欠账**：`data coverage --strict` +
  白名单 + diff 脚本 + 非 strict 行为不变；**但 0 新增测试**（326→326 未动）。
  - [ ] **T23b（Hermes，P0·补测试）**：strict 降级→exit 1 / 白名单放行→exit 0 /
    非 strict 不受影响 三条用例；白名单文件当前为空是正确状态（母库迁移期
    strict 必红=符合红线），测试需以 tmp 白名单文件构造放行场景
- [ ] **T12 扩权 / T15b2/b3（Hermes）继续**（M1 四条件之"26 笔缺口"在其内）
- [ ] **T13/T14/T17/T18/T19/T22/T25（Qoder）继续**（M1 三条件 T17/T18/T19 打头）

### Batch 4 分支验收（主 AI 2026-10-28 晚 · Qoder 四分支已全部合入 `9f88f08`，340 passed）

- [x] **T17 ✅**：skip_counts/order_attempts/signal_drop_pct 进 metrics + 面板
  跳过分类展示 + 3 回归测试（变异可杀）——纪律 4「不静默」落地
- [x] **T18 ✅（D1 判决）**：机制可行（`next_bar` 仅 1d 降级）数据不可行
  （bundle 零分钟）⇒ **D1 终局裁决已落 `refactor-plan` §5.3**：回测线接受
  日线 current_bar + F 级书面容差；T+1 正式路径 = M2 模拟线；新增 T26 积累线
- [x] **T19 ✅（M1 关键件）**：S 级达标 **97.3%(raw)/100%(同锚)**、判定层
  **111/111 全等**、26 笔缺口收口为 **29 笔归因**（14 一手+15 连带）；
  自曝 harness numpy bug（诚实加分）；M1 四条件**三达成一待**（T16 双跑）
- [x] **T22 ✅**：heartbeat.py + 看门狗 cron + 面板红条（恢复自动消失）+
  `/api/health`；代码审查过（心跳 try/except 不拖垮决策链）
- [x] **T25 ✅**：AGENTS cron 路径 → NEC + 3 条实测环境坑（x86-64-v1/numpy、
  XDG_RUNTIME_DIR、ufw）

**新派发**：

### Batch 6 · M1 收尾冲刺（主 AI 派发 2026-10-29 · 目标：凑齐签字条件后 M1 关闭）

> M1 签字四条件现状：S 级 ✅（T19）/ 26 笔缺口 ✅ 基本（剩 000333 对）/
> D1 ✅（T18+终局裁决）/ **T16 双跑 ⏳（旧引擎绿，新引擎侧未做 = 本批核心）**。
> 签字文档框架已由主 AI 落档 `docs/m1-signoff.md`，两件交付回来即填终评。

- [ ] **T29（Qoder，P0·M1 签字最后拼图）T16 双跑——新引擎侧**：
  读 `tests/test_compliance_regression.py` 中 6 条钱类 + 事故日场景的
  **"rqalpha 版对照位 TODO"**，在 spike harness（`refactor-spike` venv +
  bundle，复用 `spikes/parity_t9.py` 骨架）实现新引擎侧对照：
  - [ ] **适用场景逐条实现**：如事故日尺度回归（10-27 场景在 rqalpha 数据上
    重放 → 期望同样拒单/无成交）、费用口径对照（佣金地板/印花税/过户费——
    过户费需 D4 decider，未实现前差值要量化）、涨跌停带拒单（rqalpha
    bar_matcher 内置行为实测）
  - [ ] **不适用场景逐条论证 N/A**：L2 门禁类（H-1 无基准拒单、H-4 条件
    缺现价）住 L2 不随 L1 替换——标 N/A **必须写清"L2 测试覆盖 + L2 在
    M2/M3 不动"的依据**，不许一句带过
  - [ ] 产出 `report-t29.md`：双跑结果表（场景 × 旧引擎结果 × 新引擎结果 ×
    等价判定 × 证据），**全表没有"未测"格**
  - [ ] 验收：跑得起来的都跑了；harness 必须带 `handler_invocations>0` 类
    空转断言（T19 踩过的 numpy 静默坑）

### Batch 5 · 项目内部梳理（用户要求 2026-10-29：验证接入 + 梳理结构 + 查文档漏洞）

- [ ] **T27（Hermes，P0·结构文档漏洞审查）**：文档之间与"文档 vs 代码"的
  一致性系统审查——产出 `report-t27.md`（H 系风格逐条：位置 + 现状 + 应为
  + 风险级）。范围：
  - [ ] **文档互查**：`AGENTS.md` / `refactor-plan-open-source.md` / `WORKER.md` /
    `mother-interface.md` / `incident-2026-10-27.md` / `founding-mandate.md` /
    `migration-nec.md` / `m1-data-adapter.md` ——同一事实两处陈述是否矛盾、
    是否有过时断言（例：D1-D4 裁决后仍写"等待分钟数据"的旧句子；T25 改 AGENTS
    后与 migration-nec 是否重复维护）、复选框状态与真实交付是否相符
  - [ ] **代码 vs 文档**：AGENTS 硬规则（0-6）/ 纪律 3.2/5/8 各条引用的
    `文件:行` 或行为描述当前是否仍成立；合规表 16 条"已建模/简化"状态抽查
    （每类至少 1 条实证）；Risk Parameters 表 vs `config.yaml` 实际默认值
  - [ ] **分层纪律落实度**：硬规则 0 的 L1/L2 划分——列出哪些模块的代码注释/
    文档声称属 L2 但实际在算 L1 的活（影响成交价/持仓/盈亏/拒单的数值来源
    逐个点名：现在 L1=自研 Backtester，rqalpha 未接生产——**如实记录过渡态**）
  - [ ] P0 矛盾 → `questions.md` 喊停；P1/P2 进报告清单
- [ ] **T28（Qoder，P0·结构地图 + 分层现状）**：产出 `docs/architecture-map.md`
  ——项目当前的"内部梳理"权威地图：
  - [ ] **模块地图**：`paper_trading/` 全模块清单（包/文件/行数/一句话职责），
    按 `engine / agent / data / integration / dashboard / cli / utils` 分组
  - [ ] **依赖方向图**：核心链路（cron → cli → engine/agent → broker → db）
    与数据链路（akshare/母库 → source → 价源消费者）的文字版数据流，
    标出**单向依赖违规**（谁反向 import 了不该 import 的层）
  - [ ] **L1/L2 现状 vs 目标**：对照 `refactor-plan` §1 三层——当前每模块的
    实际归属、M2/M3 后的目标归属、**迁移状态一览**（哪些已换/待换/永不换）
  - [ ] **重复与死区**：明显重复实现（如多处各自的取价/格式化）、
    从未被引用的模块（import 图验证）、测试盲区（无测试的生产模块清单）
  - [ ] 验收：行数/清单用工具输出（cloc 或脚本统计）不许估；依赖图从
    import 语句实测不凭记忆

- [x] **T26（Qoder，P1·长线·D1 闭合）每日 5 分钟线积累**（`worker/t26`，**待 review**；
  分支链 `t31 → t32 → t26`）——先设计后实施：真机实测 48 根/标的/日幂等落库、
  过期日只报缺口不假装、周期分道、保留窗口可关；新增 10 测，全套 397 passed 4 skipped。
  **顺带挖出一条真实数据损坏风险**：`minute_bars` 主键是 `(symbol, ts)` **不含 period**
  ⇒ 1 分钟与 5 分钟在同一时刻会互相覆盖（`ON CONFLICT` 静默改写）。根治要重建表，
  属钱级数据层改动，我**只报告不动手**，等你拍板（`report-t26.md` §5-①）。
- [ ] （原文留档）每日 5 分钟线积累：从现在起每日收盘后
  把池内（或全市场按需）5 分钟线落 `data.db minute_bars`（现仅 2304 根/约
  22 天）——设计：数据源与频率确认、每日 cron 或并入既有 tick、增量 upsert
  幂等、量级估算（保留窗口策略防 SD/磁盘涨满）、缺口补拉；攒够回测窗口后
  回测线可升分钟频率（D1 彻底闭合）→ `report-t26.md`（先设计后实施）
- [ ] **T12 收尾（Hermes）**：000333 一对（rq 07-03 / 旧 07-09）用 Qoder 存盘的
  `C:\Users\yanzh\AppData\Local\Temp\t19_capture.json` 逐日快照比对归因
  （111 条事件 × 21 根收盘，无需重跑）→ 并入 `report-t12.md` 增补节

### M1 关闭后追加（主 AI 2026-10-29 · M1 签字关闭，见 `docs/m1-signoff.md`）

### M2 拍板后实施批（主 AI 2026-10-29 · `refactor-plan` §5.4 已拍板，M2 启动）

- [x] **T13 ✅ 已验收合并**（`report-t13.md` + 原型两件）：21/21 门禁真重启实测、
  3 P0/5 P1 清单、断点续跑方案、9 条门禁建议——**M2 拍板全部采纳**
  （§5.4：close_raw 供给 / provider 钱类件 / 双保险新鲜度 / vwap→只用 current_bar /
  band 供给责任=我方）
- [x] **T31（Qoder，P0·M2 数据侧）close_raw 供给管道**（`worker/t31` 分支，**待 review**）：
  - [x] 读取链：~~母库 `close_raw` 优先~~ → **实测修正**：母库只有
    `close_raw/volume/amount` 是不复权，`open/high/low/close` 四列本身就是**前复权**
    （600519 2016-01-04：母库 close 166.544=close_qfq，close_raw 210.02=bundle 原始价）
    ⇒ 改为 **akshare `adjust=""` 供 raw OHLC+成交额**，母库 `close_raw` 当**独立同日对照源**
    （喂料≠验料）；落库走**独立表 `daily_bars_raw`**，既有 qfq 数值一字未动
    （`daily_bars` 只加 `adjust='qfq'` 标签列，迁移用例断言过）
  - [x] **口径标签机制**：`Bar.adjust`；`upsert_raw_bars` 收到非 raw 直接拒写；
    `BarSource.engine_raw_bars()` = 引擎唯一取数口，口径不符/缺表/缺数/陈旧一律返回 `[]`
    并把原因留在 `last_engine_reject`（G-T13-1 fail-closed）
  - [x] G-T13-2 新鲜度：本地半边 `raw_freshness()`（按交易日历算滞后）+ 回测线决策函数
    `factor_freshness_ok(today, factor_last)`（bundle 因子末日读取由 T32 适配器做，产品不引 h5py）
  - [x] T26 同步：`minute_bars` 加 `adjust='raw'` 标签，实测印证最后一根 5m 收盘
    = 当日 raw 日收（000792 22.10 / 600519 1330.75）
  - [x] 测试 + `report-t31.md`：**新增 17 条**；全套 **357 passed, 1 skipped**
    （基线 `e28def3` 同环境实测 340 passed ⇒ 零回归）；CLI `data fetch-raw` /
    `data raw-coverage --gate`；拒收写 `op_log[data:raw_reject]`
  - ⚠️ 顺带发现：`mother.py:406-429` 把 `close` 当"不复权"兜底列是**误判**（它等于 qfq）——
    我没顺手改，已进 `questions.md`
- [x] **T32（Qoder，P0·M2 适配器产品化）T13 原型 → 产品件**（`worker/t32` 分支，**待 review**；
  依赖同为未 review 的 `worker/t31`，两条一起看）：
  - [x] 三件套进产品 —— 包名定为 **`paper_trading/integration/rq_engine/`**
    （**不能叫 `rqalpha/`**：曾把该目录注入 `sys.path`，结果遮蔽真框架包，见 report-t32 §5-④）：
    `RawDayBarStore`（raw + prev_close + band 三字段必供，缺带/OHLC 不自洽即
    `BandMissingError` 拒供）、`PaperDataSource`（bundle 元数据 + 热替换 CS store +
    `available_data_range` 覆盖 + `bundle_price_last/bundle_factor_last`）、
    **`FilePersistProvider` 按钱类件对待**（原子换名 + **fsync** + sha256 sidecar，
    校验和不符即拒绝恢复并把错误留在 provider——rqalpha 会吞这个异常）；
    **重启 cash/qty/closable/frozen 逐字段相等已在真实数据上实测**（E2→E3：
    昨日 100 股 `closable=0` → 今日 `closable=100`，现金 97,774.00 续上）
  - [x] G-T13-3/5/6/7/8/9 全部落地并逐项实测：band 缺失拒跑（`store`）、
    幂等会话闸 + 配置指纹 fail-closed（`provider.SessionGate`，E4 `rc=4`）、
    禁 `sys_analyser`（`build_config` 写死 + SETTLEMENT 重放降级为警告，E3）、
    空转断言（`check_session_gates`，就是它抓出 §5-①/④ 的真因）、
    `ORDER_CREATION_REJECT` 原因采集（E2 中文文案进 `rejects`）、
    一天一进程（`_SESSION_USED` 拒绝进程内重入）
  - [x] **双轨影子 runner**：`shadow.shadow_day()` 同日跑 rqalpha + `PaperBroker`
    （两侧**显式对齐** `initial_cash`/零滑点/同一 6 位代码键，否则比的是参数），
    `parity_diff` 逐字段对账：**持仓/T+1 可卖/成交笔数完全一致，
    `cash_delta=0.0222` = 过户费 0.001%×2221**（与 T29 同源）；
    写 `op_log[parity:diff]` 需 `--allow-op-log` 显式开启（默认不写生产账本），
    影子路径与生产 `paper_account.db` 同路径时直接 `RuntimeError`
  - [x] 测试 + `report-t32.md`：新增 **33 条**（30 条不需要 rqalpha，NEC 也能跑）；
    端到端 **16/16 PASS**（`spikes/e2e_t32.py`，生产真实原始价 + 母库验料，每项一个子进程）；
    全套 **387 passed, 4 skipped**（基线 340+1 ⇒ 零回归）；T13 的 spikes 原型保留为对照件
  - ⚠️ 顺带交付：CLI `rq day / rq shadow-day / rq state`；未装 rqalpha 时明确 exit 2 说明原因；
    `describe_error()` 剥 rqalpha 的 `ExceptionGroup`（mod 装载失败原本只露 `KeyError('ptpaper')`）
- [ ] **T33（Hermes，P0·M2 审查）拍板与原型双重审查**：
  - [ ] 审 §5.4 拍板：9 条 G-T13 门禁判据**是否可测**（每条给出测试方案草案
    或指出不可测）；close_raw 方案有无第三条 P0（如母库 close_raw 新鲜度/
    与 qfq 的一致性校验缺位）
  - [ ] 审 T13 原型代码（钱类视角）：FilePersistProvider 的原子写/指纹/
    恢复语义找漏洞；SqliteDayBarStore 的 band 计算与 AGENTS #6/#7 合规表
    逐档核对（10/20/30%、ST、四舍五入）
  - [ ] 产出 `report-t33.md`；P0 → `questions.md` 喊停（M2 动工前必须清零）

- [x] **T30（Qoder，P0·部署安全）requirements 版本 pin**（`worker/t30` 分支，**待 review**）
  - ① 现役盘点：NEC `venv/bin/pip list` → Python 3.12.3 / numpy **2.2.6** / pandas 3.1.0 /
    akshare 1.20.1 / pytest 9.2.0（现网"看起来没事"只因包早已装好，上限缺失只在重装/新机暴露）
  - ② 上限：`numpy>=1.26,<2.4`（**唯一有实测失败证据的一条**；下限同步抬到 1.26 以匹配
    pandas 3.x 实际要求）+ `akshare<2.0`、`pandas<4.0` 两条**护栏**（可驳）；
    `pyyaml/pytest` **不设上限**（无失败证据，加天花板只会添解冲突成本）
  - ③ **NEC fresh venv 实测**（临时 venv + 临时 clone，用完即删，不碰生产 venv）：
    解析到 `numpy 2.3.5 / pandas 3.1.0 / akshare 1.20.2 / pytest 9.2.0`，
    `pip check` 干净，**340 passed, 1 skipped** 与基线逐条一致；
    **反例**：同一台机 `pip install numpy pandas`（不 pin）→
    `RuntimeError: NumPy was built with baseline optimizations: (X86_V2)`
    ——装得上、跑不了，本轮现场复现。
    本地这一腿给的是**较轻证据**（如实标注）：`pip --dry-run` 解析出同一组版本
    （`numpy-2.3.5/akshare-1.20.2/pytest-9.2.0`）+ 现役 Py3.14 环境 **340 passed, 1 skipped**；
    本地完整 fresh venv 因 `lxml/mini-racer` 安装卡十分钟被我终止——
    架构相关的决定证据是 NEC 那台 v1 机器（见 report-t30 §3）
  - ④ 兼容矩阵 + 上限的代价（长期停 2.3.x / 换 v2+ 机器 / 源码编译三选一）
    + M2 装 rqalpha 时 h5py 等二进制依赖需重测 v1 —— 见 `report-t30.md` §4/§5

### Batch 4 任务定义（历史，主 AI 派发 2026-10-28 下午）

> 背景：生产已迁 NEC；母库对照发现 **40 键降级**——**用户裁决（2026-10-28）：
> 母项目（stock-dashboard）正在自行迁移，子项目一律不介入母库**（不回填、
> 不同步、不诊断母库内容）。子项目侧事实：`daily_source=auto` 已自动回退
> local:akshare，交易链无中断；母库迁移完成后富化接口自动恢复。

- [ ] **T22（Qoder，P0·运维 fail-loud）NEC 生产心跳监控**：
  Pi 连续自重启无告警、靠用户肉眼发现——这类"静默故障"必须消灭。设计并实现：
  - [ ] 心跳件：定期记录 engine/agent 最后成功轮次（如写 `var/heartbeat.json`）
  - [ ] 看门狗 cron：每 10 分钟检查心跳年龄 + dashboard systemd 活性，
    超阈值（engine >5min 未跳动）→ 面板显著告警 + `op_log[data:health]`；
    **不引入外网依赖**（纯本地/局域网）
  - [ ] 恢复检测：故障恢复后告警自动消除，不留陈旧红条
  - [ ] 测试 + `report-t22.md`：注入停滞的手工验证步骤
- [ ] **T23（Hermes，P0·数据完整性门禁）coverage 严格模式**：
  主 AI 手工对照 Pi vs NEC `data coverage` 71 键发现 **40 键降级**（母项目
  迁移期状态，用户裁决不介入）。子项目要做的是把"数据不缺"变成**可重复
  验证的门禁**：① 对照脚本（两机/两时点 coverage 输出逐键 diff，**降级白
  必须显式列举并注明归属母库**）② `report-t23.md` 列 40 键与降级原因归属
  ③ `data coverage --strict` 模式或独立测试：白名单外的任何降级=失败
  （用户红线：不能缺任何数据）；**只做子项目 CLI 与测试，不碰母库、
  不催母库修复**（母项目自行迁移中）
- [ ] **T24（Hermes，P1·挑战）L2 崩溃恢复对抗——信号语义专项**：
  NEC 是 systemd/cron 世界，进程随时可能被 SIGKILL（升级/断电/OOM）。
  对抗审查点（H 系列风格：文件:行 + 复现 + 风险级）：
  - [ ] `agent/loop.py` 计划执行在 **SIGKILL/SIGTERM/断电**下的状态一致性
    （注意：`finally` 在 SIGKILL 下**不会执行**——与 H-5 consume-once 裁决
    交叉验证：哪些信号下计划残留 pending？下 tick 重跑的实际后果？）
  - [ ] `engine --once` 中途被杀：锁残留接管语义（AGENTS 称 PID 标识可自动
    接管——在真 SIGKILL 下验证）、账本写一半（SQLite 事务边界）
  - [ ] cron 轮次重叠（上轮未完下轮启动）时 `run_lock` 实际行为
  - [ ] 产出 `report-t24.md`；P0 立即 `questions.md` 喊停
- [ ] **T25（Qoder，P2·小活）部署文档同步 NEC**：AGENTS.md「Pi Deployment
  Sync」一节 + Cron 示例路径 `/home/pi` → `/home/nec`（生产真相已变）；
  以 `docs/migration-nec.md` 为新真相源，AGENTS 只改指引不复制内容

---

## 主 AI 终审（2026-10-29）：五分支按序合并完毕，"待 review"状态全部关闭

- **合并顺序**：`t31 → t32 → t26`（堆叠分支按序），`t30`、`t14` 独立——全部干净合入，
  全量 **397 passed, 4 skipped**（基线一致零回归）；T33 报告 + ST band P0 回执随 t32 进主线。
- **T14 ✅ / T26 ✅ / T30 ✅ / T31 ✅ / T32 ✅ / T33 ✅ 全部验收**。
- 全部拍板见 **`refactor-plan` §5.5** + `questions.md` 批复节（母库 OHLC 事实修正、
  A+B 当日防线、影子 op_log 默认写、过户费显式差+decider 排期、minute 主键重建、
  walk_forward 缺陷修、numpy 上限 (a)+lock 采纳）。
- **M2 动工 P0 已清零**（T33 确认）；生产 NEC 同步最新主线。

### Batch 7 · M2 收尾与拍板落地（Qoder 4 件 + Hermes 继续在途）

- [x] **T34（Qoder，P0·数据侧修正批次）已验收**（`worker/t34`，§5.5 裁决 1+2）：
  - [x] ① `mother.py` 注释如实 + 标签改 `"qfq"` + 参数化测试锁死
    （**事实校正**：生产母库 `close_qfq` 的 NULL 行数是 **0**——NULL 的是
    `close_raw/close_adj` 各 21 行 ⇒ 原 `adj="raw"` 分支当前不可达，属"母库
    回填一变就错"的潜在缺陷；按裁决仍改了，并新增 `mother.raw_close_asof()`）
  - [x] ② 当日跨源对照：`raw_feed.intraday_reference()`（腾讯实时，与 akshare
    不同源）+ `check_raw_bars(..., intraday, day, enforce_intraday)`：收盘后才
    强制、同日才比、偏差 >0.5% **整只拒收**；顺带修**时区坑**（`session_closed()`
    原用机器本地钟，生产机非 +08 会静默不对照，现统一折算北京时间）
  - [x] ③ G-T13-1 增补判据落成 `raw_flip_watch()` 三态：`waiting_reference`
    （只告警）/ `checked_not_flipped`（**停线**）/ `raw_stale|no_raw_data`（停线）；
    `data raw-coverage --gate` 并入
  - [x] ④ NEC 真机实测：`intraday_no_today_bar=3`——**15:09 新浪当天日 K 未出**
    对照空跑 ⇒ 新增显式计数，**采集时刻建议 ≥15:30（cron 由主 AI 定）**
  - +21 测，全套 **418 passed**；`report-t34.md`
- [x] **T35（Qoder，P0·钱类数据层）已验收**（`worker/t35`，§5.5 裁决 6）：
  - [x] `(symbol, ts)` → **`(symbol, ts, period)`** 在线迁移：判需要 → 备份
    `<db>.pre-t35.bak` → 建新表搬运 → 行数核对不等即回滚 → drop/rename/索引；幂等
  - [x] **生产真实副本演练**：2,304→2,304 行、备份可回滚、生产库未触碰；
    现网分钟仅 3 只（000858/002192/600036 各 768 行）
  - [x] 读写侧同步：upsert 冲突含 period；`runner._today_minutes` 与
    `backtest.load_minutes` 显式 `period="5"`
  - [x] 覆盖风险回归（同刻两周期并存互不覆盖）+ 6 配套 = +7 测，**404 passed**；
    `report-t35.md`；⚠️ 迁移触发方式待批（见 §5.6 批复）
- [x] **T36（Qoder，P1·缺陷修复）walk_forward oracle/picked**（`worker/t36`，**待 review**）
  - 修法：重排**之前**按样本内顺序取 `in_best=oos_rows[0]`、`oracle=max(oos_rows, key=oos)`；
    新增 `in_best_oos_excess_pct/_symbol/_params`、`oracle_oos_params`、
    **`overfit_gap_pct = oracle − in_best`**；死变量 `picked` 删除；
    旧键（`sample_in_best_excess/oracle_oos_excess/…`）**全留**保证面板不破；面板加一行
    "样本内第 1 名到样本外还剩 X%／事后最优 Y%／过拟合差距 Z%"（差距>0 标红）
  - 6 条回归 + **变异验证**：把代码临时改回旧形状 ⇒ 3 条必红；换回修复 ⇒ 6 passed
  - ⚠️ 一个我踩到并写进注释的要点：**不能用 `max(rows, key=in_excess)` 找"样本内第 1 名"**
    ——同分候选会挑到不同行（实测两组 `in_excess=-27.85` 并列）；权威定义是
    `in_sample_top[0]`，测试据此反查
  - 全套 **403 passed, 4 skipped**（基线 397+1，零回归）；产出 `report-t36.md`
  - 顺带记两条 P2（未动）：同分候选排序不保证可复现；样本外只检验 top-K 的取舍未量化

- [x] **T37（Qoder，P1·部署）lock 文件 + rqalpha 二进制 spike**（`worker/t37`，**待 review**）
  - ① `requirements.lock.txt`（NEC Py3.12.3 生产 venv `pip freeze`，35 包，含生成机/日期/用途
    与"不要放开 numpy、不要与引擎 venv 合并"两条警告）；`requirements.txt` 只加两行指针注释
  - ② spike（NEC `/tmp` 临时 venv，跑完 `rm -rf` + 清 pip cache，**生产 venv 零改动**）：
    **直接 `pip install rqalpha==6.4.2` 会解析到 numpy 2.5.4 → import 当场
    `RuntimeError ... (X86_V2)`**（第三次现场复现）；正确姿势是**先 `pip install 'numpy<2.4'`
    再装 rqalpha**，之后 numpy 2.3.5 + h5py 3.16.0 / scipy 1.18.1 / matplotlib 3.11.2(Agg)
    / statsmodels 0.15.0 / jsonpickle / logbook / pandas 3.1.0 / rqalpha 6.4.2 **全部 import 通过**，
    `run_func / BaseDataSource / SimulationMod / AbstractPersistProvider` 四入口可取，
    h5py 写读往返 OK，`rqalpha --help` 正常；venv 697 MB、安装 2m10s
  - 结论：引擎必须走**独立 venv**（`requirements-rqalpha.lock.txt` 已给），
    不与交易/面板进程混装；bundle 3.3 GB 下载未做（属部署决定，等主 AI 定放置位置）
- [ ] **Hermes 在途继续**：T15b2/b3、T27 ✅（已交付验收：P0 无阻塞）、T28

### Batch 8 · 影子期启动件（主 AI 派发 2026-10-29，批复见 questions.md）

- [x] **T38（Qoder，P0·裁决 4+5 联动）影子留痕 + 过户费 decider**（`worker/t38`，**待 review**）
  - [x] `--allow-op-log` → **默认写生产账本**：`cli.py:1362` 换成 `--no-op-log` 关闭旗，
    `cli.py:1846-1872` 默认调 `record_to`；`shadow.py:35` `OP_LOG_ACTIONS={parity:diff}`
    + `shadow.py:191-198` 白名单断言（6 个非法 action 实测全部 ValueError 且 broker 未被触达）
  - [x] **D4 过户费 decider**：新模块 `rq_engine/costs.py` —— `TransferFeeOverlay`
    **包住**框架已注册的股票 decider，把 0.001% 双向加进 `TransactionCost.other_fees`
    （框架现金扣减与资金校验都读 `.total`，`interface.py:745-752`）；只作用于 CS，
    装载挂 `POST_BEFORE_TRADING`（`ptpaper` priority=50 比 `sys_transaction_cost` 先
    start_up，`mod/__init__.py:68`，挂 start_up 根本看不到框架 decider）；
    后置门禁 `TransferFeeNotInstalled` 兜"没装上"（`runner.py:162-166`）。
    **验收信号达成**：22 万元单子两侧费用 **57.2 == 57.2、`fee_delta=0.0`、现金一致**
  - [x] "预期差逻辑收敛"：删 `FEE_TOLERANCE_RATIO`（相对 1%）→ `FEE_ABS_TOLERANCE=0.01`
    绝对容差；T32 旧用例前提被推翻，改写 + 新增（`tests/test_rqalpha_adapter.py:247-272`）
  - [x] T35 迁移留痕：`db_manager.py:20-29` 登记三类迁移 + `cli.py:1153-1166/1642`
    写 `op_log[data:migrate]`（取走即清空，重复调用不重复留痕）；
    顺带修 `daily_bars` 建表缺 `adjust` 列导致**每个新库都误报一次迁移**（`db_manager.py:120`）
  - [x] 测试 + `report-t38.md`：新增 23 条（18 条不需 rqalpha）+ 迁移留痕 3 条；
    e2e **16/16 PASS**（`spikes/e2e_t38.py`，含 S3 变异检查：overlay 装不上 →
    门禁停线 + `fee_delta=-2.2` + `cash_delta 2.2` 同时报）；
    全套 **461 passed/1 skipped**（有 rqalpha）与 **453 passed/9 skipped**（生产口径），
    基线 431/4 ⇒ 零回归
  - ⚠️ **交付时撞破我自己的 P1（T32 缺陷）**：产品化 `build_config` 从没带
    `sys_transaction_cost` ⇒ 引擎一直在用框架默认**佣金万八**（现网万 2.5）。
    T29/T32 没暴露是因为单子太小、两侧都撞 5 元最低佣金把差额盖住了
    （22 万元单差 121 元）。费率对齐留在 spikes 里没搬进产品件 = 我的疏漏，
    现按"费率进会话指纹"修复；副作用见 report-t38 §5.5（指纹变了，旧 state_dir 会判漂移）
- [x] **T39（Qoder，P0·M2 影子期启动）NEC 引擎环境落位**（`worker/t39`，**待 review**）
  - [x] NEC 独立引擎 venv `/home/nec/venvs/rq-engine`：按 lock **先 `numpy<2.4`（20s）
    再 rqalpha==6.4.2（182s）**，lock 收敛 2s ⇒ 41 包 / 700 MB / `pip check` 干净 /
    numpy 2.3.5 + pandas 3.1.0，x86-64-v1 上 h5py·scipy·matplotlib(Agg)·statsmodels·
    jsonpickle·logbook 全链 import 通过；现网交易 venv 仍无 rqalpha（未混装，实测 import 报
    ModuleNotFoundError）
  - [x] bundle 落位 `/home/nec/.rqalpha/bundle`：`rqalpha download-bundle`（202610 快照，
    16:59→17:02 约 3 min，3,537,975,667 B / 14 文件），**sha256 全表已记**
    （report-t39 §3，NEC 另存 `t39_bundle_sha256.txt`）；开发机 bundle 是同一份快照
    ⇒ 跨机对照无"两份数据不同"混淆因子。`check-bundle` 会提示"日线不完整"= 月度快照
    月中必然落后，行情由我们 `daily_bars_raw` 供，但 `ex_cum_factor` 同源 ⇒ 复权因子
    新鲜度仍是真约束（§4）
  - [x] `rq shadow-day` NEC 首跑（真实 2026-10-29 收盘原始价，5 s）：两侧现金
    **827,339.1198 == 827,339.1198**、费用 **44.8802 == 44.8802**、
    `cash_delta/fee_delta/fill_delta = 0`、`violations=[]`、2 成交 + 1 拒单（中文原因可读）、
    `evidence.cost.wrapped=["CS"] applied=8 added_total=6.90464`、gates ok；
    手算复核费用构成一致（佣金 21.47+21.68、过户费 0.8588+0.8674）。
    开发机同数据同场景复跑**逐字段相同**；`--no-op-log` 在 NEC 生效（op_log 0 行）
  - [x] `report-t39.md`（耗时/磁盘/首跑对账 + 逐条核销清单）；cron 只给草稿（§7），
    按派发"跑顺再提请"
  - ⚠️ **留痕没写现网账本**：`--account-db` 指到 scratch 库。理由是 `worker/t38` 未 review
    ＋红线不写账本，现网 `op_log` 至今 0 条 `parity:diff`；切换时机请拍板（§6-C）
  - 🔴 **两件阻塞影子期的发现（只报告不自修）**：
    ① 生产 `data.db.daily_bars_raw` = **0 行** ⇒ 派发说的"生产真数据"现在不成立，
    需另派"现网 raw 回填 + 每日采集"；
    ② raw 管道**参照窗口比采集窗口短**（采集 `days*2+10` 自然日 vs 母库 `LIMIT days` 交易日，
    `raw_feed.py:239-241` vs `mother.py:533-540`）⇒ scratch 实测 590 行只有 400 行验了料，
    其中 17 个未验料日是**母库明明有数据**的 8 月；而 `raw_flip_watch` 只看最后一次
    checked 距今几个交易日（`raw_feed.py:192-218`），`data raw-coverage --gate` 对此
    报 `violations=[]`（盲区）。细节与修法见 report-t39 §6-B
  - 另：现网母库其实可达（`/home/nec/stock-dashboard/data/db/stock_dashboard.db`），

---

## Batch 9 · 影子期正式运行（主 AI 派发 2026-10-29 深夜 · 批复见 questions.md）

> T38/T39 已验收合并（453+9skip 生产口径；NEC 引擎 venv 461+1）。三件拍板：
> 授权现网 raw 回填 / T39 §6-B 三缺陷派修 / 留痕现在切现网。
> **Qoder 四件连发：T41→T40→T42 有依赖顺序（先修管道再回填再挂 cron），T43 独立。**

- [x] **T41（Qoder，P0·先行）raw 管道加固**（`worker/t41`，**待 review**；T39 §6-B 三缺陷）
  - [x] 参照窗口对齐：新增 `raw_feed.fetch_window(days, today)` 作为采集/参照/审计
    **唯一的窗口定义**（顺带修掉默认 `fetch` 写死 `date.today()` 不看 `today` 参数的坑）；
    `mother.raw_close_map(..., since=)` 按日期下界取参照，老调用方 `days` 语义不变。
    **真机：同一库同一窗口 `checked 400 → 557`（590 行，拒收 0）**
  - [x] `raw_flip_watch` 逐日检查：`db.raw_date_flags()` 逐日台账 + 六态分类
    （`verified/today_pending/unflipped/waiting_reference/no_reference_available/audit_incomplete`，
    另有 `raw_stale/no_raw_data/reference_source_unavailable` 停线态）；
    CLI `raw-coverage` 新增 `audit` 块并进 `--gate`。
    **真机：T39 那份报 `violations=[]` 的库现在报 `unflipped_days=157`、10 只全停线**；
    新采的那份 `unflipped_days=0`、gate 绿（母库滞后的 23 天归 waiting 告警）
  - [x] `--no-mother-ref` 语义修正（选"真不读"而不是改名）：`raw_feed.reference_for()`
    是旗子→参照提供者的唯一映射，CLI 不再留 `reference=None`。
    **真机：带旗 `single_source=true`、`symbols_with_ref=0`、590 行全 `checked=0`，
    且闸口归因 `reference_source_unavailable` 停线**（不装没事）
  - [x] 回归测试 + `report-t41.md`：`tests/test_raw_pipeline_t41.py` 17 条，
    全套 **470 passed / 9 skipped**（基线 453/9 零回归）；
    `spikes/mutation_t41.py` 四条源码级变异（内层→外层/丢 since/旗失效/停线不生效）
    分别 1/1/2/3 条必红，还原后 55 条全绿
  - ⚠️ 真机跑出的**我自己引入的缺陷已当场修**：`flip_watch_with_mother` 误用
    `raw_close_map` 的外层 `{code:{date:row}}` ⇒ `ref_asof` 变成标的代码、
    `ds in have` 恒假，双向误判（漏报 unflipped + 7 只母库滞后标的被误停线）；
    已加形状回归用例锁死
  - ⚠️ 语义收紧是有意的：`--gate` 失败面变大（旧数据今天就该不合格）；
    母库不可达且窗口内一行都没验过 ⇒ 停线；T34 已验收的判据一条没放松（12 条全绿）
  - ➡️ 给 T40 的输入：现网 `daily_bars` = 27,951 行 / 38 只 / 2023-10-09…2026-10-29（每只 742 行），
    对齐深度约需 `--days 550`；`--days` 参数含义含糊（自然日换算），要不要加 `--start/--end`
    请主 AI 定。跨仓事实：同一轮里母库深市到 10-27、沪市只到 10-23（影子期会持续
    `waiting_reference` 告警，不停线）——若判定为母库同步问题需转 stock-dashboard 那条对话
- [x] **T40（Qoder，P0·依赖 T41）生产 raw 回填**（`worker/t40`，堆叠在 `worker/t41` 之上，
    **待 review 按序合并**；已授权行情库）
  - [x] 全池 38 只回填 `daily_bars_raw`：**0 行 → 28,339 行 / 2023-09-25…2026-10-29**，
    `checked=28,264`（**99.74%**）、**拒收 0**、口径标签全 `raw`；
    顺序是先探母库深度（10,548,954 行/5,193 只/2016-01-04…）→ **现网库副本干跑**
    （35 s 同参数）→ `data.db.pre-t40-20261029-181759.bak` 冷备 → 现网跑（44 s）
  - [x] **`data raw-coverage --gate` 全绿（退出码 0）**，`audit={audited:28339,
    verified:28264, unflipped_days:0, waiting_days:37, no_reference_days:0,
    incomplete_days:0, complete:true}`、`violations=[]`；
    `raw_flip_watch` 三态核对：37 行未验料**全部**是 `waiting_reference`
    （母库自己只更到 10-23/10-27，18 只在等），母库补上后必须由下一次 `fetch-raw` 翻转，
    否则逐日闸按 `checked_not_flipped` 停线（T41 设计）
  - [x] 回填后重跑 `rq shadow-day`（现网真数据，2 s）：买 000651/300750/600519 三笔
    （含创业板 20% 带）+ 卖 601318 无持仓被拒且原因可读 ⇒
    **两侧现金 743,559.3428 == 743,559.3428、费用 66.6572 == 66.6572、
    cash/fee/fill_delta 全 0、violations=[]**，`wrapped=["CS"]`；逐笔手算复核佣金+过户费一致
  - [x] 缺口与红线如实报告（report-t40 §2/§5/§6）：`daily_bars`(27,951、SUM(volume)/SUM(close)
    逐位不变) 与 `minute_bars`(2,304) **未被动过**；现网 `paper_account.db` 的
    `parity:diff`/`data:migrate` **仍是 0 行**（影子留痕写在 scratch，首个现网留痕归 T42）
  - ⚠️ 两点要你知道：①本次用的是 `worker/t41` 的管道（未 review）——main 的窗口缺陷
    会把"1/3 无从验料"固化进现网，所以按 T41→T40 顺序走；review 后若语义变动，
    重跑 `fetch-raw` 即可（幂等 upsert）。②`--days 560` 只是"覆盖 742 交易日"的倒推值，
    参数含义含糊，要不要加 `--start/--end` 等你定
  - 顺带发现（不在 T40 范围，未动）：`DataDBManager._connect()` 没设 `busy_timeout`，
    与 cron 并发写会直接 `SQLITE_BUSY` 报错而不是等
- [x] **T42（Qoder，P0·依赖 T40）~~影子期一周~~ → 切换前对账验证**（`worker/t42`，
    堆叠在 `worker/t40`→`worker/t41` 之上，**待 review 按序合并**；§5.6 节奏变更）
  - [x] **首个现网 `op_log[parity:diff]` 已落库**：id=327，
    `result={"cash_delta":0.0,"fee_delta":0.0,"fill_delta":0,
    "rqalpha_cash":743559.3428,"legacy_cash":743559.3428}`（写之前该库 parity 行数=0）；
    写前冷备 `paper_account.db.pre-t42-20261029-182221.bak`
  - [x] 全链三步 2026-10-29 实跑（采集 18:22 >15:35 → shadow-day 现网真数据 → `--gate`）：
    `cash/fee/fill_delta` 全 0、`violations=[]`、gate 退出码 0
    （`audit={audited:28339, verified:28264, unflipped:0, waiting:37, complete:true}`）；
    场景含创业板 20% 带（300750）与"无持仓卖出被拒且原因可读"；费用逐笔手算与两侧一致
  - [x] 账本红线核对（写现网账本必须留这一栏）：`account.cash` 71,922.004、
    持仓 5 只、`orders/fills/nav_history/agent_plans/conditional_orders` = 12/11/15/17/37
    **前后逐位不变**；`op_log` 325→327（只多 `data:raw_no_intraday_ref` + `parity:diff` 两行审计）；
    影子成交落在 `--shadow-dir`，与生产同路径直接 RuntimeError 的拒绝逻辑再次生效
  - [x] cron 草稿提请（report-t42 §5，15:40 三步，由主 AI 装）
  - ⚠️ **"切换就绪"我给成有条件**：这一跑照出一件 P1——**当日跨源对照（裁决 2 的 A 半边）
    在 ≥15:35 的采集时点上恒不成立**：`realtime.py:149-150` 非交易时段直接 return {}，
    `realtime.py:19/54` 的 180 秒新鲜度又丢掉腾讯收盘最后一笔（实测时间戳 16:14:45），
    而同刻 `fetch_batch` 能正常拿到 000651=37.58（与新浪收盘一致）⇒ 是我们自己的闸把防线关了；
    叠加 `source.py:76-104` 引擎取数口不查 `checked=1` ⇒ 当日 bar 无独立参照就被吃下。
    三个修法 (a) 盘后专用新鲜度 / (b) 采集挪盘中（不建议，新浪日K 15:35 才发）/
    (c) 当日那根必须 checked 才喂引擎（选它就得先做 (a)，否则每天该时段停摆）——
    **已进 questions.md 等拍板，我不自选**
  - ➡️ 待办：第二个验证日（10-30 15:40 后同一条链）由我跑或 §5 cron 装好后自动产出；
    另请一并定"影子期 cron 用 `stock_pool` 表 38 只还是 `config.yaml` 的 10 只"
- [x] **T45（Qoder，P0·M3 切换方案）切换 runbook**（`worker/t45`，堆叠在 t42/t40/t41 之上，
    **待 review 按序合并**；产出 `docs/migration-m3.md`，沿用 `migration-nec.md` 传统）
  - [x] 切换日流程四步全部写成可执行动作：§4 Phase 1 ①-⑤（含 `crontab` 先备份、
    归档后 `chmod 444` 只读保留一个版本周期、新开账 NAV 对齐归档最后一天、
    **④ 必须在 ① 之后**——顺序铁律引 `migration-nec.md:75-76` 原文"不可双活一秒"）
  - [x] 回退方案：§5.1 现状下的手工 6 步（明确**禁止跨账合并**：旧线 `avg_cost` 含费、
    rqalpha `avg_price` 不含费，合并必错）；§5.2 最小开关规格（关键坑：新键**必须**加进
    `engine/config.py:27-37` 的 `DEFAULTS` 白名单，否则 `config.py:59/64` 会静默丢弃）；
    §5.3 账本衔接边界 + 指纹副作用（换机/换路径/改 `trading.*` 都会让同日拒续跑）
  - [x] 逐条 cron/服务/路径清单：§3 共 19 行（cron 五条按 `AGENTS.md:53-70` 原文逐条标注
    停/保留/新增，另有面板 unit、两个 venv、bundle+sha、state 目录、三把锁、
    `engine.local.yaml`/`strategy.local.yaml`/`secrets.local.json` carry-over、
    logrotate 里**仍是 `/home/pi` 的过时路径**、tag 与合入顺序）
  - [x] `report-t45.md`（含逐条核销清单）；切换操作留给主 AI 亲执行（§7 是待填执行记录表）
  - 🔴 **runbook 第一节写的是"现在还不能切"——4 个缺口都有 file:line 证据**：
    G1 新线不写账本（`rq_engine/runner.py:216-218` 只出 evidence；`rq_engine/` 里唯一
    的 PaperBroker 用法是 `--shadow-dir` 对照，`shadow.py:48-54/72`）；
    G2 新线没有 NAV 记录器（`record_nav` 调用点全在旧线：`agent/loop.py:524/1171`、
    `cli.py:371/442`、`main.py:133`；`runner.py:90` 那句只是意图注释）⇒ 切换后面板净值当天断供；
    G3 **"config 开关一键切回"不存在**（全仓 `use_rqalpha|execution_backend|engine_backend|backend`
    零命中，`engine/config.py:27-37` 无该键；说法只在 `refactor-plan-open-source.md:97/320` 与 §5.6）；
    G4 归档/新开账工具不存在（只有 `deploy/incident-*.py:23-25`、`db_manager.py:75/105` 手工冷备，
    `nav:revision` 只在 `ROADMAP.md:120/214` 出现，代码无写入点）。
    ⇒ 需要主 AI 派实现任务，或把 M3 语义显式改成"影子期正式化"（与 `:98` 的
    "cron 切 rqalpha 常驻"文本对齐）
  - ⚠️ 另外两条要主 AI 在切换设计里显式决定：① `agent tick` **一条命令同时管**
    "开盘执行计划"(09:30-10:00) 与"收盘做计划"(16:30-18:30)（`agent/loop.py:684-690`）
    ⇒ 整条停掉会连带断掉 AI 计划链；② 新线"一天一进程"+`SessionGate` 与"每分钟一跳"
    排程冲突 ⇒ 建议 09:35/15:05 两轮、每轮独立 state、单独一把锁（`var/rq.lock`）
- [x] **T46（Qoder，P0·切换后检查体系）检查清单 + 验证工具**（`worker/t46`，
    堆叠在 t45/t42/t40/t41 之上，**待 review 按序合并**）
  - [x] `docs/m3-checklist.md`：第一小时 6 项 / 第一天 9 项 / 第一周 8 项，
    派发点名的八项全覆盖（T+1、费用三件套=佣金地板/印花税单边/过户费、NAV 收盘、
    条件单触发链、AI 计划执行链、审计留痕、面板数据一致、影子对账连续），
    另加切换日更要命的**双活检测**两项（含"ssh 回 Pi 查残留 cron"）
  - [x] 可自动化项做成命令：新模块 `paper_trading/utils/m3_verify.py`（10 项检查 +
    汇总 + 渲染）+ CLI `m3 verify`（人读 ✅❌⚠️⚪ / `--json` / `--money-only` /
    `--days` / `--today` / `--symbols` / `--heartbeat`）；人工项每条写"怎么跑/期望/红了怎么办"
  - [x] 每项标"红了怎么办"，**钱类项红⇒立即回退**：`money`/`on_red` 字段 +
    `rollback_required`；退出码 **0 绿 / 1 非钱类红 / 3 钱类红(=立即回退) / 2 工具自身失败**，
    cron 可分别告警；人读输出末行直接印「❗钱类项红 ⇒ 按 §5.6 底线立即回退
    （步骤见 docs/migration-m3.md §5.1）」
  - [x] 真实跑的证据（`spikes/smoke_m3_verify.py` 可重跑）：正常态 7 绿/2 警/0 红 exit 0；
    把佣金 18.79 改成 99.0 ⇒ `fees` 红 `{"commission":80.21}` **exit 3**；
    `--money-only` 正确只列 4 项钱类
  - [x] 测试 `tests/test_m3_verify_t46.py` **29 条**；`spikes/mutation_t46.py` 六个源码级变异
    实测分别 2/1/1/1/1/1 条必红，还原后 29 全绿；全套 **499 passed / 9 skipped**
    （main 基线 453/9 + T41 的 17 + 本任务 29，零回归）
  - ⚠️ **按派发"工具给 Hermes 审一遍再交付"**：我没有自审通过。给 Hermes 标了三个最该盯的点：
    ① `fees` 依赖 `fills.direction==-1` 判卖出；② `t_plus_one` 依赖 `positions.entry_day`
    （旧线才有这列 ⇒ 新线若不写这列，T+1 永远查不出问题，是 T45 §0 G1 的衍生缺口）；
    ③ `raw_gate` 母库不可用时给 warn 而非红，这个松紧是否合适请审
  - ⚠️ 诚实边界：工具**只读**（全 SELECT，无 INSERT/UPDATE/DDL），且它只回答
    "钱对不对/账齐不齐/线活着没"，**不重跑撮合**；清单 §5 专列了"工具不覆盖的"
    （首单归属锚点 `m3:switch` 还不存在、涨跌停无法在生产账本里验、除权日影响待因子到位）。
    另外 T45 §0 的 G1/G2 不关闭时，本工具上线第一天就会稳定红 `nav`/`audit_trail`——
    那不是工具的错，是它把缺口照出来了
- [ ] **T44（Hermes，P0·审查）T38/T39 钱类件复核**：TransferFeeOverlay
  （H 系视角：套层重复挂载/基金类遗漏/费率变更时会话指纹漂移的恢复语义）+
  T39 三发现的严重性复核（漏验窗口/盲区/--no-mother-ref 是否还有第四件）+
  T38 自撞破的佣金 P1 修复复核（万 2.5 是否真的进了会话指纹与撮合两侧）；
  产出 `report-t44.md`，P0 → questions.md

---

## Batch 10 · M3 直达硬前提（主 AI 派发 2026-10-29 深夜 · 批复见 questions.md）

> T40-T46 六件已验收合并（499 passed 9 skipped）。T45 亮出的四缺口 G1-G4
> 是"现在还不能切"的全部原因——**T47 补齐它们 = M3 硬前提**；T48 解执行模型。
> Qoder 三件 + 顺手一件 + T51；Hermes 审查件。

- [x] **T47（Qoder，P0·M3 硬前提）四缺口补齐**：`worker/t47`（堆叠在 `worker/t48`），
    runbook `migration-m3.md` G1-G4 ⇒ **待 review 不合 main**；全套 **568 passed / 9 skipped**
    （基线 515/9 ⇒ 新增 53 条钱类测试，零回归）
  - [x] **G1 新线写账本**：`PaperBroker.apply_external_fill()`（G1 原语：**不重判价/不加滑点/
    不重算费**，只做一致性守卫 + 复用旧线同一批落账原语 ⇒ `avg_cost` 含费、T+1 解冻日、
    `peak_price` 口径逐位一致）；`ledger.post_session()` 把成交/拒单/费用三分量落
    `orders/fills/t1_freeze/op_log[rq:fill|rq:reject]`；`mod_entry` 补出
    `commission/tax/other_fees` 三分量 ⇒ **缺任一分量整批拒绝落账**（不拿 `cost` 去猜拆分）；
    守卫失败**停批**（后面的笔不写）；biz_id 幂等判定用 `has_op_with()` 全表按 action 查，
    **不用最近 N 条窗口**（本仓已证面板问答会把目标行挤出窗口）。`shadow.py` 一字未改（对照用法保留）
  - [x] **G2 NAV 记录器**：`ledger.record_close_nav()` 业务日判重（复用 `has_operation()`，
    `date` 写进 **result** 以支持跨日补记）、**零成交也记**、幂等；金额调 `broker.get_nav()`
    不另写公式（测试断言与旧线逐位相同，含 `pnl` 对 `initial_cash`）；双轨期旧线已记
    `nav:close` 则新线**让位**（同业务日两行净值会让面板与 verify 双双失真）；持仓缺价 ⇒
    拒记 `price_missing` 而不是按 0 市值写
  - [x] **G3 回退开关**：`engine/config.py:33` `DEFAULTS["executor"]="legacy"`（**进了白名单**，
    测试直接验"yaml 写了不被吞"；变异⑧删这行 ⇒ 3 条必红）+ `session_fingerprint` 含
    `ledger`（变异⑩）+ `op_log[m3:switch]` 同日同值只记一行 + **守护进程启动校验**
    （`startup_guard`：未知值/`rqalpha` 却没装 ⇒ **exit 2 不降级**，真子进程退出码有测试）。
    演练=离线两向：`m3:switch` 双向留痕 + **键不存在 vs 键=legacy 六表逐位一致**（§5.2 回退回归）；
    **现网真跑一天的切换/回退需授权** → questions.md Q2
  - [x] **G4 归档/新开账工具**：`cli ledger archive --archive-dir DIR --yes [--allow-existing]`
    = sqlite `backup()` 冷备（不是 `cp`）→ `chmod 444` → **账本非空且无 `--allow-existing` 直接拒**
    （拒绝时源文件 SHA 不变，冷备已先落盘）→ `.pre-reopen-<stamp>` 现场副本 → 新开账对齐
    归档 `cash + 持仓 +`**未解冻 T+1**（不搬 `t1_freeze` 会让切换日买的票永远卖不掉，测试盯着
    解冻入口）→ `op_log[ledger:archive]`（含归档行数清点）。幂等可重入：同 `--stamp` 重跑
    `archive_reused=True` 且归档 mtime/字节不变；`-journal/-wal` 存在 ⇒ 有人在写，拒绝动手。
    另有 `cli ledger executor`（只读看当前底座 + 最近切换留痕）
  - [x] 顺手：`fetch-raw --start/--end`（裁决 3）：`sync_raw(start=, end=)` +
    `flip_watch_with_mother(start=, end=)`，采集/母库参照/逐日闸**三处仍同源**；
    新语义=区间终点早于真实今天时**不做盘中跨源**（历史日没有当天实时价可比），
    默认路径强制跨源那条**一字未松**（有测试对照）。⚠️ 我这次改名漏了两处引用 + 签名，
    既有回归网 **17 条立刻红**（report-t47 §2.3）
  - [x] `report-t47.md` + 裁决核销清单（§1 逐条对照派发原文与 §5.2 开关规格）；
    `spikes/mutation_t47.py` 14 个源码级变异 2/1/2/3/1/2/1/3/3/1/1/1/1/1 条必红、还原 88 全绿；
    `docs/migration-m3.md` 加 §0.1「T47 实现落点」（只标工具长什么样，**不宣布缺口已关闭**——
    关闭判据要 review + 现网演练）；三个决定点进 questions.md（Q1 双活防护归谁 /
    Q2 现网演练授权 / Q3 `m3 verify` 要不要长出新留痕项）
- [x] **T48（Qoder，P0·M3 执行模型方案）**：`worker/t48`（堆叠在 `worker/t50`），
    **只出方案不改码**，`docs/worker/report-t48.md` → 等主 AI 拍板后实施
  - [x] 三方案各自给全四项（止损延迟上界 / state·账本一致性风险 / 实施工作量 / 回退难度）：
    **①每分钟短窗口进程** 延迟≈1 分钟，但一致性风险**高**——同日再跑必然重复下单
    （T13 §4.7 实测：resume 只跳 `before_trading`，`handle_bar` 再执行一次；
    现网闸文案即照此写），要做成需新建日内共享状态 + 计划增量语义 + 幂等闸改写，
    且把 §4.4 的重复结算每天放大 240 倍；**②常驻长窗口（分钟线驱动）** 延迟同样≈1 分钟，
    状态只有一份、幂等天然成立，风险中等，**方向正确但前置是分钟线**
    （`minute_bars` 现在只有 5 分钟周期 / 2,304 行 / 无每日采集与新鲜度闸 ⇒ 今天跑不起来）；
    **③分层混合** 延迟不变、工作量最小、回退最易，但rqalpha 不当成交真相源
    ⇒ **不满足 M3 定义**，只适合当兜底/M2 延长线
  - [x] **开销用 NEC 实测而非估算**：单会话（38 只 × 3 年 raw，1 笔成交，费用 9.7708 与手算一致）
    **2.42 s / 峰值 RSS 171 MB**；其中 `import rqalpha` 固定 **0.83 s / 95 MB**；
    同日复用 state 被闸拒 **0.93 s / exit 4**；无共享状态连跑 5 次 ⇒ **每次成交 1000 股
    = 净买 5000 股**（重复成交风险的直接证据）；①每日机时外推 240×2.4 s ≈ 9.7 分钟
  - [x] **带 T13/T45 约束原文**：§0 逐字引 T13 §4.4（重复结算 + 指标致命）与 §4.7
    （一天一进程、`should_resume/should_run_init` 核心无实现）、
    G-T13-5/9 现网闸文案、以及主 AI "两轮否决"的原文理由
  - [x] 给出推荐顺序（拍板参考，不越权）：先做 **G1+G2（T47）**——它是 ①②③ 共同地基；
    ②的分钟线供数列为前置；①不推荐；③只作兜底
  - ✏️ 顺手修正一处**会伤人的过时建议**：`docs/migration-m3.md` §3 #8 原写
    "建议 09:35 一轮 + 15:05 一轮"——该方案已被裁决否决（丢盘中止损语义），
    照 runbook 执行的人会照着装；现改为指向 report-t48 并附实测约束
- [x] **T50（Qoder，P0·小）盘后防线修复**（`worker/t50`，**待 review**；裁决 1）
  - [x] (a) 盘后专用新鲜度：`realtime.py` 新增 `is_closing_snapshot()`
    （当天 + ts≥15:00 + 时龄 ≤`QUOTE_CLOSING_MAX_AGE=9h`，**跨日自动作废**）、
    `quote_usable(allow_closing=)`、`get_quotes(allow_closing=, now=)`；
    撮合/风控仍走 `quote_fresh` 180 秒，一字未改；`intraday_reference` 默认入口带上
    `allow_closing`。**真机（NEC 19:32 真实快照）：`intraday_checked 0→38`、
    `intraday_missing 38→0`，今日 38 行 checked=1 从 0→38，全库 28,264→28,302**
  - [x] (c) 当日无参照 bar 不喂引擎：`raw_feed.today_reference_state()` 三态 +
    **两道闸同装**——`source.engine_raw_bars`（文档承诺的口）与
    `RawDayBarStore.from_data_db`（**真正喂 rqalpha 的那条**）；被挡标的记
    `gate_rejected` 并进 evidence。**真机：抹掉 600036 当日参照 ⇒ store 只供 000651、
    框架以"没有市场数据"拒单、`gate_rejected={"600036":"today_bar_unchecked"}`、
    旧线照买 ⇒ `position_delta=-1000` 把"挡"显式照出来**
  - [x] 顺序 (a)→(c) 未反（先 (c) 会让每天 15:35–母库更新之间全线停摆）；(b) 未做（按裁决否决）
  - [x] 回归测试 + `report-t50.md`：新增 **15 条**（含 (a)→(c) 因果链、四种错形状快照、
    跨日作废、双路径 get_quotes 对照）；既有 `test_engine_raw_bars_returns_fresh_raw`
    按新语义补 `checked_dates`（它原验的正是"无参照也放行"）；
    `spikes/mutation_t50.py` 四个源码级变异 2/1/1/1 条必红、还原 54 全绿；
    全套 **515 passed / 9 skipped**（基线 499/9 零回归）
  - 🔴 **顺手撞破：`BarSource.engine_raw_bars` 这个"引擎唯一取数口"是纸面的**——
    全仓生产代码没人调它，真正的喂料是 `store.py:95 db.get_raw_bars` 直连
    ⇒ 只按派发文改 `source.py` 的话 (c) **完全不生效**。已两道闸都装并写明哪道才是真的
  - ⚠️ 行为变更（影子期/切换须知）：当日采集失败 ⇒ 那只标的**当天不下单**（以前会用未验证的价）；
    `require_today_reference` 已进会话指纹（换档需新 state，别用 `--force`）；
    现网 `data.db` 今日 38 行仍是 `checked=0`（我只写 scratch），
    **下一次授权采集或 15:40 cron 首跑前，新线对"今天"不下单**（by design）
  - ➡️ 两个待你定的小点：① 母库沪市近端滞后（18 只停在 10-23/10-27）是否转母项目；
    ② `m3 verify` 要不要新增一项读 `evidence.gate_rejected`（我不在 T50 里顺手扩已验收的 T46 面）
- [x] **T51（Qoder，P1·口径唯一）费率三真相源收敛**：`worker/t51`（堆叠在 `worker/t47`），
    **待 review 不合 main**；全套 **586 passed / 9 skipped / 1 failed**（基线 `worker/t47` 同时刻 567/9/1 ⇒ 净增 19 条零回归）
    ⚠️ 那 1 条红与本任务无关且在干净 t47 上同样复现：`test_mother_contract` 夹具写绝对日期
    `2026-10-26` + 判定用墙钟 ⇒ 机器过 10-30 午夜即翻红；机制与影响面见 questions.md 2026-10-30 条
  - [x] 三处费率数字 + 三处公式 + 三处"各自 new"收敛：**数值唯一**
    (`models/types.py:TradingConfig`)、**公式唯一**（新 `utils/fees.py:buy_cost/sell_cost`，
    broker/`FeeModel`/`m3_verify` 三路都调它）、**构造唯一**
    (`utils/config.py:trading_config_from_dicts`，**里面一个数字都不写**，未知键 warning 不静默)；
    `costs.py` 删掉四个现网费率常量（只留 `RQ_*` 框架基准率当除数），
    `stock_cost_mod_config/install_transfer_fee/transfer_fee_amount` 费率**必填**；
    `runner.build_config`+`shadow_day` 经 `as_fee_dict` ⇒ 不传/缺项 `ValueError`（旧行为是吃兜底数字）
  - [x] **撞破更严重的事实（派发说"可能"，实测是"一定"）**：`cli.py` 两处与
    `dashboard.py:906` 读 `load_config(...)["trading"]`——**该键不存在**（真键名 `trading_config`）
    ⇒ 回测/网格/面板一直在吃代码抄的兜底费率，改 `config.yaml` 完全不动且不报错。
    `spikes/e2e_t51.py` 端到端：同一笔成交只换费率档 ⇒ `total_fees` **5.08 → 50.23（×9.89）**，
    面板 `_fee_of` 同样跟随。⚠️ 今天 config 与旧兜底**数值相同** ⇒ 既往回测结论仍可比
    （`test_repo_config_yaml_agrees_with_code_default` 钉住该前提），但机制已变 → questions.md Q3
  - [x] **反射测试锁死**（`tests/test_fee_single_source_t51.py` 17 条）：AST 扫全树抓四种写法
    （赋值/形参默认/关键字调用/`.get(k, 数字)`，**大小写都抓**），公式扫描抓
    `max(...commission_rate...commission_min...)` 出现在 `utils/fees.py` 之外；
    两条扫描都带**正控制**（临时植入必须报 3+1 条）——没有正控制的反射测试是永远绿的空断言
  - [x] `spikes/mutation_t51.py`：十个"回退成旧写法"的变异 1/2/1/1/1/3/3/2/1/1 条必红、还原 68 全绿
  - [x] 契约变更与既有测试的理由逐条写进 `report-t51.md` §4 表（改了 4 个文件的 10 条断言：
    `test_fees_and_oplog_t38`/`test_postclose_defense_t50`/`test_rqalpha_adapter`/`test_ledger_gaps_t47`，
    两条旧测试验的正是"不传也有默认"这条被废除的行为，改为验"不传必报错"+ 新增 2 条）
  - 🔴 **纪律冲突请拍板**（questions.md Q1，非我的疏漏）：`m3 verify` 的 `fees` 项原本**独立重写**
    一遍公式，收敛后与 broker 共用 ⇒ 仍能查"库里费用与成交价量不符"，但查不出"公式本身错"；
    实测目前全仓只有 `test_fees_and_oplog_t38.py` 两处零散手算期望值，
    `test_compliance_regression.py` **一条费用断言都没有**。请选 (A) 补硬期望值锚点表 /
    (B) 保留独立实现 + 加一致性测试 / (C) 到此为止
- [ ] **T49（Hermes，P0·审查）**：审 T45 runbook（G1-G4 修法方向、回退 §5
  的账本衔接边界）+ T46 工具（它自点三盯点：`direction==-1` 判卖出对不对、
  `entry_day` 只有旧线写 ⇒ T+1 检查可能永远查不出的盲区、母库 warn 松紧）+
  T47 交付后的钱类件复核；P0 → questions.md

---

## Batch 11 · M3 执行模型终局（主 AI 派发 2026-10-30 · 批复见 questions.md）

- [x] **T52（Qoder，P0·执行模型终局）方案④取证：rqalpha 撮合器逐笔调用**：
    `worker/t52`（基于 main@901bee6，**待 review 不合 main**）；
    证据全在 `spikes/matcher_call_t52.py`（spike venv + bundle + 本地 raw fixture，
    **未改产品码、未写账本**）
  - [x] 接口分析（文件:行，**AST 机器导出**不手抄）：撮合一笔要 8 类在场状态
    （`data_proxy`/`price_board`/`trading_dt`/`calendar_dt`/`config`/`portfolio·account`/
    `event_bus`/**当前 bar**——`bar_matcher.py:30 env.get_bar` 由 BAR 事件注入）+
    38 处 `Environment.get_instance()`/`ExecutionContext.phase()` 硬依赖；
    `event_bus` 不可选：`portfolio/__init__.py:60` 的 `EVENT.TRADE` listener 就是
    持仓/现金更新的唯一入口 ⇒ "逐笔调用"与"起一个会话"的差只是跳几块 bootstrap
  - [x] **语义分工已定位**：涨跌停带在 matcher 内（`base.py:207-218`，市价单 REJECTED /
    限价单留单）、费用三分量在 matcher 内（`base.py:108-114` + 我们的 overlay）、
    **T+1 不在 matcher 内而在提交校验链**（`environment.py:207 can_submit_order` →
    sys_accounts validator）⇒ 绕过 api 就丢 T+1
  - [x] spike 实证四项全测（同一会话同一时点三路径对照）：涨停 ⇒ `REJECTED：已触及涨跌停价
    limit_up`；T+1 ⇒ api 路径 `ORDER_CREATION_REJECT：可平仓量为 0`，
    **裸调 matcher 却 FILLED 并把框架自己的 `closable` 写成 −100**（④否决的硬证据）；
    费用三分量逐笔手算相符（100 股@22.10 佣金 5.00 撞地板 / 过户 0.0221；卖出加印花 1.105）；
    撮合价 = bar 口径 ⇒ 三跳全部成交在 22.10 = **当日 close**（该日 OHLC 22.50/22.51/22.05/22.10）
  - [x] 判定：**不可行**（跨进程 fresh process：`Environment.get_instance()` 与
    `Order.__from_create__` 均 `EnvironmentNotInitialized`，假 env 喂 `match()` 第一步
    读 `order.position_effect` 就 `NotImplementedError`）⇒ 与主 AI 否决一致，本报告补量化理由；
    与派发不同处：**我用了产品码构造会话**（才测得到切换日真跑的那条路，含 T38 overlay/
    T50 闸/T47 落账），只读未改
  - [x] "若可行给架构草图"条件不成立 ⇒ 改交付**①改良在产品路径上的实测与前提清单**
    （`part_d` 三连跳）：意图幂等实测有效（不过滤 ⇒ 同日重复成交 100 股、持仓翻倍；
    过滤 ⇒ 零新增）；🔴 **R1 钱类**：resume 跳过 `POST_BEFORE_TRADING` ⇒ 续跑跳**不装过户费
    overlay**（`other_fees` 0.0221→0.0），`TransferFeeNotInstalled` 每跳红不会静默，
    切换日前必修（questions.md Q1）；🔴 **R2 口径**：1d 频率下成交价=当日收盘价 ⇒
    "每分钟一跳"实为**日 bar 重放**（10:00 的止损按 15:00 才知道的价格成交），与旧线盘中
    实时报价不同口径、与您否决"两轮"的理由相冲，需拍板 (a)/(b)/(c)（questions.md Q2）；
    R3 跨日第一跳 T+1 解冻待取证；R4 幂等键归一坑（plan 6 位码 vs `000792.XSHE`，
    我第一版就因此漏过滤）
  - [x] `report-t52.md`（§1 派发逐条核销、§2 依赖清单、§3 三路径表、§4 四风险、
    §5 跨进程证据、§6 ①改良最小架构草图、§7 未做清单）
- [x] **T53（Qoder，P1·小）测试日期敏感性根治**：`worker/t53`（基于 main@901bee6，
    **待 review 不合 main**）；全套 **594 passed / 9 skipped**（净增 7 条守卫，零回归）
  - [x] 病根定位：**基准日没作为参数传进去**——产品读墙钟、测试写死日期，
    真实日历推进必然分叉。monkeypatch 拦不住：`sources.block()` 是**函数内**
    `from datetime import date`（`sources.py:36`），`mother._stale()` 同理
    ⇒ 按 T41/T47/T50 已有约定补两个注入点：`sources.block(..., today=)`、
    `mother.daily_bars(..., today=)`，**默认行为不变**
    （`test_default_today_is_unchanged` 逐字段比对，剔除定义上依赖基准日的 `stale`）
  - [x] `test_mother_contract` fixture 动态化：`_weekday(offset)`（偏移后回退到最近
    **工作日**）给出 `D_LATEST/D_PREV/D_NEXT/D_SEQ`，母库 8 处日期簇 + 本地三日行情
    + 两处 `UPDATE ... trade_date=?` 参数绑定全部改相对；新鲜度断言改为
    显式 `today=D_LATEST`（可再断 `stale is False`，前提由测试钉住）
  - [x] 两把扫描工具：`spikes/date_flaky_scan_t53.py`（**AST 取代码字符串字面量**，
    注释/docstring 里的历史叙述不算——第一版逐行正则误报的教训；RISK-A/A′/B 分级 +
    `--json` 清单）与 `spikes/clock_shift_t53.py`（**把墙钟拨快 N 天真跑 pytest**，
    红名单才是清单；grep 只给候选）
  - [x] 拨钟实验：平拨 0 天有 3 条 harness 自身副作用（假 `date` 破坏 isinstance）；
    治前 +7 天 21 红（含 mother 那条）；治后 mother 那条**从红名单消失**，
    真实日历上（今天 2026-10-30 跑）全套 594 绿
  - [x] 清单 + 归因（report-t53 §4）：剩下的红分两类——**类 A 仪器误差**
    （fixture 用真钟、产品被拨假钟，真实推进不会坏：本件的 default-unchanged、
    T47 entry_day、T51 m3_verify）与**类 B 真会老化** 15 条
    （`test_agent` 7 / `test_engine_cond` 3 / `test_m1_correctness` 3 /
    `test_engine_switch` 1 / `test_runtime` 1，涉及 agent 计划日、条件单 expiry、
    持仓元数据/日预算、sync 窗口 4 个产品时钟读取点）
  - ⚠️ **类 B 我没在这件里一起改**：那 3 个文件是 Hermes/OpenCode 在途任务
    （T15b2/b3）正在动的范围，且切换日在本周末——不在切换前 48 小时改别人在途的
    测试与产品入口。建议派 T56 或本件续做，验收命令现成：
    `python spikes/clock_shift_t53.py 7` 的红名单应只剩类 A
  - [x] 守卫测试 7 条（`tests/test_date_hygiene_t53.py`）：注入语义、默认不变、
    **周末规则**（today+10 落周六那一类）、**stale 不许写死布尔**、
    扫描器**正控制**（植入"今天+3"必须被抓、故意历史日期不许误报）、
    "谁再写死近今天日期就立刻红"（±45 天白名单外零容忍）
  - [x] 三条请主 AI 定的（questions.md）：类 B 何时做／"新鲜度判定必须收
    today/now 参数，不许函数内 import 后直接读钟"要不要写进 AGENTS／
    拨钟 harness 的 3 条噪声要不要改成注入式插件
- [x] **T54（Qoder，P1·小）m3 verify 生产预跑**：`worker/t54`（基于 main@901bee6），
    **待 review 不合 main**；本地 **599 passed / 9 skipped**（基线 587 ⇒ 净增 12 条零回归）
  - [x] 跑法守住红线：bundle+scp 到 `/home/nec/tmp`，`clone --no-hardlinks` 出 scratch 树
    （**不碰生产 .git**）；现网两库用 sqlite backup 只读复制成副本，工具只吃副本；
    生产 venv（未装 rqalpha，工具不需要）；全套 **734 ms**
  - [x] 预跑基线就是问题：`绿 5 / 警 3 / 红 2 / ❗钱类项红 ⇒ 立即回退`、exit 3——
    而线根本没坏（engine 心跳 6 秒前刚跳）。**切换日第一小时清单正好在盘中跑**，
    不修就会在周六上午指挥操作者回退
  - [x] 照出并修掉 4 个工具缺陷（每个都配"过点必须仍红"的反向断言）：
    ① `check_nav` 盘中把"今天没 NAV 行"判钱类红 ⇒ 收盘窗口（crontab `*/5 16-18`）
    未过判 warn，`now` 可注入，过点/历史日照旧红；
    ② `check_parity_trace` 未到 15:40 影子时点就指控"cron 没跑" ⇒ 文案改"时点未到"，
    过点与 delta 非零仍按原判；
    ③ `check_heartbeat` 读不到文件报**假红**（`heartbeat.default_path()` 是相对路径，
    操作者从 `~`/scratch 跑就读不到；实测加 `--heartbeat` 立刻绿 engine=6s）⇒
    新增 `resolve_heartbeat_path()`：显式 ＞ cwd/var/ ＞ 仓库根/var/，都没有判 **skip**
    并列出试过的路径（"没证据"≠"线挂了"），detail 写出用的哪个文件；
    ④ 人读输出不写"池从哪来" ⇒ 头部加 `｜池 <pool_source>`（cwd 无 config.yaml 时
    "股票池为空"会被误读成"没什么要查的"）
  - [x] 修后现网复跑原文贴 `report-t54.md` §3：仓库根等价环境 **红 0 / exit 0**；
    上一交易日 2026-10-29 **8 绿 / 2 警 / 0 红**（NAV 恰 1 行自洽、parity delta 全零）；
    从 `$HOME` 跑也 **红 0**（两条 skip 讲清试过哪些路径）；`--money-only`/`--json` 均可用
  - [x] 工具只读性双证：`m3_verify.py` 409 行零写 SQL（grep INSERT/UPDATE/DELETE/DROP/CREATE 无命中）
    + 新增断言"跑完全套账户库字节不变"；报告里同时**如实说明** sha1 前后差不能当证据（现网 cron 每分钟在写）
  - 🔧 **顺带照出两件运维事实（不改，交主 AI）**：
    ① 影子 cron 未装（`crontab -l | grep -c shadow-day` = **0**），而 runbook §3 第 3 条
    要求影子期每天跑——10-29 那条 parity:diff 是手工跑的 ⇒ 切换日第一周"新线是否漂移"
    的唯一外部对照会断档；② 现网 engine cron 是 `* 9-15 * * 1-5`、`AGENT_DUE` 20:30 起才追问
    ⇒ 看板显示"agent 无记录"不等于没跑，切换日照读清单时要知道这一点
- [ ] **Hermes 在途**：T15b2/b3 继续；T52 交付后审其接口分析与语义清单

---

## Batch 12 · M3a 上线收口（主 AI 派发 2026-10-30 · 裁决见 §5.8）

- [ ] **T55（Qoder，P0·切换前必修）R1+R3**：
  - [ ] R1：过户费 overlay 安装点从 `POST_BEFORE_TRADING` 移到 mod `start()`
    （每进程必执行）+ `isinstance` 幂等 + 既有 `TransferFeeNotInstalled`
    门禁保留为兜底；回归：同日续跑跳成交 `other_fees` 非零
  - [ ] R3：跨日第一跳 T+1 解冻取证（两个不同日期的连续会话：昨日买、
    今日首跳解冻在哪步、会不会解两次）→ `report-t55.md`
  - [ ] `m3 verify` 新增两项：overlay 已装 / 续跑跳成交费用非零
- [ ] **T56（Qoder，P0·M3a 门禁实施）日终校核门禁**（§5.8 M3a 形态）：
  - [ ] 把 `rq shadow-day` 升级为**校核门禁**：日终会话重算当日账 →
    与生产账本逐字段对账 → 差异超阈（钱类绝对阈值，沿 T38 的 0.01 元级）
    ⇒ `op_log[parity:diff]` 红 + **停线标志文件**（engine/agent 下一跳
    检测到即拒跑并告警，fail-closed）+ 差异明细留痕
  - [ ] 影子/校核 cron 草稿提请（15:40 三步链：采集→校核→gate；由我装）
  - [ ] 测试 + `report-t56.md`；**注意 R1 未合前先别在生产跑续跑场景**
- [ ] **Hermes 在途**：T15b3；T55/T56 交付后审查（R1 修复的变异验证 +
  停线标志的 fail-closed 语义）

- [x] **T57（Qoder，接管修复）M3 切换三条 P0 + 供给/报警**（用户令「你直接开干，
    由你来接管，回头把做了什么提交就行」；审计报告 `audit-20261030-m3-cutover.md`）：
  - [x] P0-2 成交价按决策时点归因：`_SessionState.decide()` 门 + `submits` 归因表
        （现网 scratch 实测 14:30 的意图成交在 `14:30:00 / 45.04`＝当天 14:30 bar close）
  - [x] P0-3 逐笔认领：删 `mark_by_symbol` ⇒ `queue.resolve_outcomes()`
        （方向+量一对一、一条成交只认领一次、提交未成交⇒rejected+框架原因、
        落账失败⇒一条都不许多标；现网实测 `fills=1 executed=1 rejected=2`）
        —— 现网验收又照出两处残留（同跳去重冒充成交、一条意图多 bar 反复定局），已一并修
  - [x] P1 报警：`rq_tick` 心跳（分层，不进默认表）/ `minute_feed` / `intent_queue`
        （executed 无落账 ⇒ **钱类红**；部署后现网立即把切换日 3 条假账逐条点名，exit 3）
  - [x] P1 供给：`minute.completed_slots()` + `ensure_today_minutes()`（只落已收完的 bar；
        已齐不打接口）；`rq tick` 自证本跳供给；`minute sync --intraday` 可单装 cron
  - [x] P1 防双活：`rq tick` 底座非 rqalpha ⇒ exit 2 拒消费；AI 直接动作在 rqalpha
        底座下改投意图队列（底座读不出来⇒一条都不下）；现网三条路径实测
  - [x] 部署：NEC 工作树 `56ae3b1→b5e4378`（`worker/m3-takeover`，**main 未动**）；
        crontab 装 `40 15 data fetch-raw` + `50 15 minute sync`（备份
        `crontab.pre-m3-takeover-20261030-183811.bak`）；补齐 10-30 raw（38/38 checked）；
        **`paper_account.db` 一字节未动**（md5 前后同值）
  - [x] 测试：`tests/test_m3_takeover.py` 41 例（含假 `rqalpha.api` 桩跑真 `handle_bar`）；
        本地与现网各一遍 **647 passed / 9 skipped**；
        `spikes/mutation_m3_takeover.py` 13 条变异 13 条捕获
  - [x] 文档：`report-t57-m3-takeover.md` + `migration-m3.md §9`（更正"切换完成"三条
        不实说法）+ ROADMAP 新增更正行 + `questions.md` 剩余 5 件待拍板
  - [ ] 待主 AI：review 并合 main（现网正跑未合并分支）；切换日 3 条假账如何处置

---

## Batch 13 · 切换后第一日核验与数据供给（主 AI 派发 2026-11-03 · 用户授权分配）

> 周一（11-02）实况：新引擎 tick 每 5 分钟正常 idle，**零成交 = 账户熔断正确拦截**
> （回撤 35.23%）。核验发现 `peak_total_value=156,005`（10-27 旧账户遗留，
> 账户重置后成孤儿）→ **用户拍板：已重置为当前 100,189.96**（冷备份
> `peak-reset-backup-20261103.db`），熔断解除、engine 恢复（armed=9、quotes 在）。
> 新暴露三个数据供给缺口（引擎本身无故障）：

- [x] **T59（Qoder，P0·分钟供给）盘中采集链闭环**（`report-t59-minute-feed.md`）：
  - [x] 主 AI 已装盘中采集 cron（`6,21,36,51 9-15 * * 1-5` 脚本
    `deploy/m3_minute_intraday.sh`，每 15 分钟补当日分钟线）
  - [x] 验证：**两项都回绿**——11-03 12:49 现网 `m3 verify` = 绿 7 / 警 5 / **红 0**，
        `minute_feed`「今天 960 行 / 40 只；最新 11:30」、`heartbeat` minute=828s（应跳）；
        tick 日志每 5 分钟一跳且 `--json` 带 `minute_feed` 自证字段。
        **粒度结论：15 分钟够用，不加密**——钱类路径不依赖那条 cron（`rq tick` 本跳
        `ensure_today_minutes()` 自采，延迟 ≤ 一根 bar）；改 5 分钟只把新浪请求量翻三倍。
        但这条链上查出 **5 处缺陷**并全修（北交所分段错、午休假红、陈旧度按墙钟算、
        缺口留痕重复刷、bar 时间戳把未收完的一根落进库）
  - [x] `ensure_today_minutes()` 触发路径复核 + `--intraday` 时间戳语义：新增
        `completed_slots(period, now)`（**严格早于当前分钟**才落库，进行中的那根永不入库），
        会话侧 `RawMinuteBarStore` 只吃已收完的 bar；逐标返回 `{written, latest, missing, error}`
- [x] **T60（Qoder，P0·raw 日线）raw_gate 未翻转 5 只归因**（`report-t60-raw-gate.md`）：
  现网 `m3 verify` raw_gate 红：stale/未翻转 = `['000333','000651','000858',
  '600036','600276']`。归因：母库参照滞后？fetch-raw 覆盖缺口？T50 闸语义边界？
  **给结论 + 修复/白名单决策**（母库数据问题→转 stock-dashboard 对话；
  子项目闸语义→修+测试）
  - [x] **结论：三类都不是"5 只"**——派发里的 5 只是我自己 `stale[:5]` 截断造成的误读。
        实况是 ① **11-02 全池缺当日 raw**：15:40 采集早于数据源发布 T 日日线（昨天跑
        时实测 `intraday_no_today_bar=38`，今天 10:40 同源已能取到 11-02）；
        ② 新入池标的（000426/920156）在当天采集**之后**才进池，且 `added_at` 是 UTC
        （16:30 本地）；③ 北交所标的被市场前缀判错，分钟/实时/日线/盘口/涨跌停带
        **同时瞎**。"5 只"里一只 920156 属 ③，其余属 ①
  - [x] 修复（子项目侧，带测试）：`_market_prefix` 等 5 处市场判定收敛成
        `paper_trading/utils/markets.py` 单源（纪律 7）；`RawMinuteBarStore` 的当日带
        prev_close 必须是**上一交易日**，缺了就拒供并点名（旧带 44.50 vs 应 44.97 ⇒
        limit_up 48.95/49.47，每股 0.47 元错价可成交，现网库副本 A/B 实证）；
        cron 加 `40 8` 盘前 + `40 20` 盘后两班 fetch-raw；920156 补 126 行、000426 补 744 行
  - [x] 转母项目（不动母项目代码）：报告 §4 写了三条请求——母库 `tc_encode` 有同一个
        北交所前缀 bug、`close_raw` 发布时点晚于我们 15:40 的采集窗口、北交所标的在
        rqalpha bundle 里 **0 只**（28,063 instruments 无 `.BJ`，意图永不成交 ⇒ 现已
        `INSTRUMENT_UNAVAILABLE` 定局为 rejected 而不是静默挂着）
  - [x] 白名单决策：**不建白名单**——三类都是可修的供给/口径问题，不是标的本身不该交易；
        唯一需要决策的是"北交所进不进池"，那要等 bundle 有 `.BJ` 或由主 AI 决定排除
- [ ] **T61（Hermes，P1·工具语义）m3 verify 红绿语义审查**：今天 3 red 中
  `minute_feed`（盘中 0 行）与 `heartbeat(minute)`（应跳未跳）在**采集 cron
  装上前后**的语义是否合理；`raw_gate` 红时 on_red="回退"的判定在切换期
  是否过严。产出 `report-t61.md`（审查不改码，除非纯工具参数问题）
- [x] **T62（Qoder，P1·流程）状态清理工具**（`report-t62-reset-state.md`）：本次峰值遗留的通用防御——
  账户重置/账本迁移/新开账时必须清理的**状态字段清单**（`risk_state` 的
  peak/daily_baseline、`engine_state` 熔断态等）+ 实现 `cli ledger reset-state`
  （显式命令 + 留痕 op_log[ledger:reset-state]）+ 迁移流程文档补
  "重置后必清" checklist；测试锁定
  - [x] 清单 **14 项**落成代码单源 `paper_trading/ledger_state.py::TARGETS`
        （halt/ghost/throttle/report 四类，每项带 why+action）：peak、daily_baseline、
        未决意图、armed 条件单、`var/rq_state/<日>` 会话目录、fired、budget、cooldown、
        trigger-once、event 节流 + 4 项**只报不清**（逾期 T+1、意图/账本口径冲突、
        心跳、清单没见过的新键）。键的分布是现网副本实测出来的，不是猜的
  - [x] `cli ledger reset-state`：默认 dry-run、`--yes` + `--reason` 必填、
        `--keep/--only/--today/--state-root/--stamp`；留痕 `op_log[ledger:reset-state]`
        带**清理前原值**；钱类 7 表（account/positions/orders/fills/nav_history/
        t1_freeze/op_log）前后快照必须逐字相同，越界即回滚 + 会话目录改名回退；
        无态可清时不写第二份留痕；有红报时退出码 1
  - [x] 文档：`migration-m3.md` §10「重置后必清」表格**由清单渲染**，测试比对两边
        id 序列一致（纪律 7）；`m3-checklist.md` §5 补一条"账本重置不在 verify 射程里"
  - [x] `ledger archive` 现在报出自己清不掉的部分（会话目录不在库里，重开账本碰不到它，
        而 `intraday_resume` 会把它接进新账）⇒ 返回 `reset_state_pending` 并进留痕
  - [x] 测试锁定 26 例（周一孤儿峰值/孤儿基线的复现-解除、钱类越界回滚、幂等无第二份痕、
        只报项不许被 --only 混清、真 `cli.main()` 接线 4 例）；全量 **704 passed / 9 skipped**
  - [x] 现网库副本 5 轮试跑：清 59 项、钱类不动、留痕不多写；生产 md5
        `d3b85438950377dcc8806a0028317a26` 前后同值；**生产工作树未切分支、cron 未动**
  - [x] 顺手照出我自己两处 CLI 失真（dry-run TypeError、no-op 自称 dry-run），
        各补接线测试（`9e3ae70`/`147b396`）
  - [ ] 待主 AI 裁决（工具只报不清的两件）：002192 的 T+1 逾期未解冻（新线上没有
        解冻入口这件事本身要不要立项）、切换日 3 条意图与订单 #13 的口径冲突谁真谁假


## Batch 14 · AI 问答性能（主 AI 派发 2026-11-03 · 用户报"手动发消息超时"）

- [x] **T63（Qoder，P1·性能）llm_context 瘦身**（`report-t63-llm-context.md`）：现网 `llm_context()` = **97,285 字符（≈3 万 token）**——账户+持仓+**全池 38 只 × 5 天日线**（190 条 bar）+ 母库附加段。复杂问题模型要消化 97KB 才回话（修复后 timeout 120s 下仍需 **77 秒**）。目标：**问答典型响应 ≤20 秒**。
  - [x] 分级压缩设计（沿用 agent.context_budget 的分级思路）——**先测了体积在哪**：
        原始装配 253,677 字符里 `value_view.stock_cards` 占大头（股息史 26,850 /
        追踪 11,590 / 财务摘要 7,997 / 基本面 6,591 / AI 分析 4,361），
        `recent_closes` 14,677、`screening` 12,246、`news` 6,466；
        而旧压缩阶梯一级都没碰到它们 ⇒ 预算 8000/4000/2000/1000 输出**全是 97,910 字符**
        （裁到哪与预算无关，超预算 3.5 倍也不报告）。派发里的 97,285 是旧压缩**之后**的数
    - [x] 账户/持仓/**熔断态**=必带：熔断态此前**根本不在上下文里**（11-02 那次
          "为什么没交易"模型只能猜）⇒ 新增 `risk_control` 段（`RiskManager.describe()`
          只读版，不在问答路径锚定日亏基线）；provenance/account/positions/legend
          压到极限也不裁（测试钉住）
    - [x] 日线只带持仓 + 池内参考：`_HELD_CLOSES=5 / _POOL_CLOSES=1`，问答口径装配时就取 1 根
    - [x] 母库附加段截断：池内候选的股息史/追踪/论点**整段丢**、财务摘要收窄到 3 键、
          AI 分析截 160 字；持仓走 HELD 配额（股息 3 / 追踪 3 / 分析 400）；
          筛选 8 行 + 理由 100 字 + 去 score_detail；要闻 10 条 + 摘要 80 字；
          仍超预算再逐级丢 `pool_fundamentals→news→screening→pool_cards`，
          **压到地板就如实报 `within_budget=False`**（地板现网 5,549 估算 token）
  - [x] `llm_ask` 加 `symbols`/精简参数：`llm_ask(..., symbols=None, slim=True, max_tokens=None)`
        + CLI `--symbols/--full/--max-tokens` + 面板 body 同入口
  - [x] 请求体 token 量进日志：`op_log[llm:ask]` 的 params 加 `req_chars/req_tokens/req_max_tokens/slim/symbols`，
        result.context 落压缩元信息（前后体积/裁了什么/是否达标）；另加一行 logger.info。
        `report-t63.md` 体积/延迟对照见其 §4
  - [x] 测试锁定：`tests/test_context.py` 重写 12 例（"预算不同体积必须不同"、
        到地板稳定且不谎报、持仓 vs 池内分级、四段永不裁、压缩失败不许静默）；
        改旧用例的理由写在文件头——旧假数据把体积大头放在会被裁的字段上，与真实分布相反
  - 🔧 **必须回报的一条与派发假设不同的实测**：**响应时间不由上下文主导**。
        同一问题、同端点、同 120s 超时：请求体 99,668 字符/`max_tokens=4096` ⇒ **82.4s**；
        请求体 24,312 字符（3.5× 少）/`max_tokens=8192` ⇒ **74.9s**；
        同样上下文 `max_tokens=1200` ⇒ **28.3s**（真实输出都约 650 token、答案都 1000+ 字、
        `finish=stop` 未截断，`reasoning_content` 0 字排除"思考吃预算"）。
        ⇒ 该端点按 `max_tokens` **上限**预留解码。我把问答预算独立成 `ASK_MAX_TOKENS=1536`
        （实测 28.3~45.5s），**没为了凑 20 秒把上限压到 ~1000**——那会悄悄截断长答案，
        而 `finish_reason=length` 目前不告知用户。要真正到 ≤20s：先加截断信号，再谈更小默认值
        （见 questions.md）
  - 🔧 顺带（同类别，见 T64）：问答路径此前**不带** `llm_retries/llm_max_tokens/llm_thinking`
        （只有 timeout 被 475f994 补上），并把它收敛成 `provider.chat_with_retry` 一个原语
- 背景（主 AI 已修的两层，勿重做）：① `llm/ask` 误列写门禁（已去）
  ② `agent.llm_timeout` 漏传进 LLMConfig（timeout 30→120 已修，77s 验证通过）


## Batch 15 · 接线与门禁的系统性防御（主 AI 派发 2026-11-03 · 用户指令：这类小问题不该再出现，交给员工搞定）

> 背景：本次两个 bug 都是**类别性问题**——① `agent.llm_timeout` 定义了但构造 LLMConfig 时漏传（配置接线断层）② `llm/ask` 只读接口被误列写门禁（门禁清单误伤）。
> **指令：这两类问题由 Qoder/Hermes 系统性消灭，出工具+测试+报告，不再逐个报主 AI。**

- [x] **T64（Qoder，P0·接线完整性）配置接线全量审计**（`report-t64-wiring-audit.md`）：
  - [x] 扩展 `tests/wiring_audit.py`（或新脚本）：覆盖**跨对象传递**断层面
    （本次漏传类别：`AgentConfig.llm_timeout` → `LLMConfig.timeout`；同类还有
    哪些配置字段"定义了、默认值对、但消费点拿不到"？）
    - [x] v2 四类尺子：①零消费（搬运层之外读点=0，AST 按节点语义判定：
          `obj.f`/`obj["f"]`/`.get("f")`/`getattr(o,"f")` 算读，而 `Foo(f=1)` 的 kwarg、
          `{"f": 1}` 的字面量键、`f: int = 1` 的声明**都不算**——v1 把它们算成"用过了"，
          所以那一栏永远绿）；②LLM 请求侧**传递矩阵**（行=每个真发请求的调用点，
          列=4 个 `llm_*`，供给认三路：路径引用／构造点喂目标参数／经 `_ask_llm` 间接层，
          **调用点给字面量单独判"硬编码覆盖"**）；③yaml⇄归一化⇄字段三面白名单；
          ④构造点供给（识别 `RiskManager(**risk_kwargs)` 这类整体摊派）
    - [x] 答案"同类还有哪些"：`llm_ask` 问答路径不带 `llm_retries/llm_max_tokens/llm_thinking`；
          `design_conditions` 把重试次数**硬编码 1**（`改 config 无效`）
  - [x] 全仓扫描 → 清单：6 个面（AgentConfig 27／LLMConfig 7／TradingConfig 9／
        EventConfig 8／RiskManager 6／engine.DEFAULTS 10）。
        **零消费 0 项、三面白名单断层 0 条**（v0.7.6 补的 news_*/require_* 都在位），
        矩阵断层 2 处 = 上面那两条；另记 3 条"不算断层但要看一眼"：
        `LLMConfig.extra_body/max_tokens` 构造点走默认（由调用方按路径覆写，设计如此）、
        `EventConfig.max_symbols` 无配置面（带理由豁免）、`LLMConfig.temperature` 无面板项
  - [x] 修复清单里的全部漏项 + 单测锁定 + `report-t64.md`：**修法不是各补各**——
        把"配置怎么落到请求上"提到 `provider.chat_with_retry`（超时重试 + 1210
        不支持关思考时降 low 档并重给预算），`_ask_llm` 变薄壳（签名不变），
        问答与条件单设计共用一个原语；`design_conditions` 改传 `agent.llm_retries`。
        测试 `tests/test_wiring_t64.py` 15 例：5 例证明**检测器对合成源码真会红**
        （否则门禁是摆设），8 例是行为级"改配置 ⇒ 请求里真的变"（timeout/thinking/retries/
        预算覆盖/只重试超时不重试其他错），1 例门禁本体，1 例"豁免必须带理由"
  - [x] 门禁化：`python tests/wiring_audit.py` 退出码 0=无未豁免断层／1=有断层；
        并随 pytest 跑（`test_audit_gate_passes_on_the_repo`），无需额外 CI 接线。
        误报代价我付过一次（中间版本报 5 处、3 处是假的：嵌套闭包与正则截断实参），
        修完之后矩阵只剩真信号——**门禁哭狼的次数比它漏报的次数更值得防**
- [ ] **T65（Hermes，P0·门禁语义）端点门禁清单审查**：
  - [ ] 逐个核对 `dashboard.py` 所有 `_admin_ok` 保护端点 vs **真实语义**
    （读/写/资金/配置）——输出"应保护/不应保护"对照表；
    读接口误伤（如本次 llm/ask）= P0 项
  - [ ] **测试锁定**：每个写端点"无 token 必 403"、每个读端点"无 token 必 通"
    （防未来再误伤/再放宽）；发现误伤直接修 + 回归
  - [ ] 顺带审：`_takeover_token` 接管路径的边界（哪些端点允许 Key 接管、
    哪些必须原生 token）——与 AGENTS「LLM 接入安全」条款逐条对照
  - [ ] `report-t65.md`
- [ ] **T66（Hermes，P1·验收）T64/T65 交付审查**：接线修复的变异验证 +
  门禁测试的完备性（有无"该保护的没保护"反向漏洞）

> Qoder 交付状态（2026-11-03 晚）：T63/T64 已交并在 `worker/t63-t64-perf-wiring`
> （`4340715` 审计 v2 + 压缩分级 + `80250d4` 问答预算），**未合并**；
> 全量 701 passed / 9 skipped，`tests/wiring_audit.py --quiet` → 断层：无。
> T66 审查 T64 时请特别看：① 矩阵的检测语义（三路供给 + 硬编码判定）会不会漏掉
> 别的间接层；② `chat_with_retry` 收敛是否改变决策路径行为（我判定不变，签名与降级分支都保留）；
> ③ 问答耗时归因（`report-t63.md` §4）是否需要补那一格 2×2（脚本 `/home/nec/tmp/t63_grid.py`，
> 上次跑到一半 SSH 被重置）。
'@; git add -A; git commit -m "chore(worker): Batch 15 systematic defenses per user - T64 config wiring full audit (cross-object passing like llm_timeout->LLMConfig), T65 endpoint auth semantic audit + lock-in tests, T66 review; goal: this class of bug stops recurring" 2>&1 | Select-Object -Last 1; git push origin main 2>&1 | Select-Object -Last 1

## Batch 16 · 拍板落地（主 AI 派发 2026-11-03 晚 · 批复见 questions.md）

- [x] **T67（Qoder，P1·问答体验）截断信号 + 配置面 + 真实 token + CI 门禁**（`report-t67-ask-polish.md`）：
  - [x] "答案被截断"可见信号：`provider.LLMReply` 是 **str 子类**（带
        `finish_reason/truncated/usage`）——决策路径拿它做 JSON 抽取、7 个测试文件
        patch 的是 `chat` 的字符串返回，改成返回 dict 会把 T64 刚收敛的路径再拆散。
        `llm_ask` 把 ⚠️ 标记**追加进答案文本本身**（面板只渲染 answer）＋结构化字段
        ＋`op_log[llm:ask].result`
  - [x] `agent.ask_max_tokens` 配置键：三件套同步（dataclass 字段 + `load_config` 白名单 +
        cli 构造点 + `config.yaml` 键），缺省 1536 不变；按次覆盖（CLI `--max-tokens`、
        面板 body `max_tokens`）优先于配置；测试用真 config 走真读取链断"改了请求里就变"
  - [x] `context_budget` 真实 token：`usage.prompt_tokens` 不再被丢弃，留痕
        `prompt_tokens_est` 与 `prompt_tokens_real` 并列；观测比值（11-03 实测估 8,318 /
        真实 11,440 ⇒ 低 1.37×）喂给 `estimate_tokens` 的校准系数，只接受 [0.6, 2.5]
        （缓存命中一类异常观测宁可不校）
  - [x] `wiring_audit` 进 CI：`.github/workflows/test.yml` 独立一步，跑在 pytest **之前**，
        退出码非 0 直接告警，不再混在测试红里
  - [x] `report-t67.md`：含一条延迟实话——**≤20 秒不是靠上下文达成的**
        （4096→82.4s / 1536→45.5s / 1200→28.3s，真实输出都约 650 token）
- [x] **T68（Qoder，P1·T66 三切入点）**（`report-t68-audit-behavior.md`）：
  - [ ] ⏸ 2×2 归因缺格重跑（`/home/nec/tmp/t63_grid.py`：大上下文×小预算格）
        ——要 2 次云端请求，按"花额度先报备"停在门口，脚本就绪等一句"跑"；
        现有三点已足以分离两个变量，结论不依赖这一格
  - [x] 矩阵三路供给判定复核：**改成 AST 推导而不是人工名单**（`detect_helpers`）——
        名单过期时新层内部调用被报成漏传、它外面的调用点整条跳过，断层反被洗白。
        第 1 层=写 `cfg.timeout/max_tokens/extra_body` 且调 chat；第 2 层起=转传**自己形参**的薄壳
        （第一版我把"任何调用者"都算成层 ⇒ `run/tick/main` 全变层、供给点零检查；已修 + 加牙齿）
        复核还照出我自己三个缺陷：`_ask(` 命中 `llm_ask(` 尾巴的子串碰撞、
        函数表按裸名建键导致同名 `run` 遮蔽（**矩阵静默少一行**）、`fpath` 作用域泄漏标错文件
  - [x] `chat_with_retry` 行为等价性 6 例：只重试超时（次数=retries+1）、非超时错一次即抛、
        1210 降 `level:low` 并把预算抬到 ≥4096、`_ask_llm` 位置参数语义与返回物可 JSON 解析、
        重试期间预算只升不降
  - [x] **engine/strategy 行为级断言 21 例**（真跑 tick 看结果与账本，不是 grep 引用）：
        `enabled=false` 在 cron 的 `--once` 路径下确实不下单**且仍落 blocked_disabled 留痕**、
        `max_orders_per_day/per_symbol_cooldown_sec/min_ticket_cny/max_turnover_per_day/
        rule.enabled/rule.once/universe+universe_max` 各自"改一个键结果就不同"、
        `interval_sec` 明确断它在 `--once` 上不生效、strategy 两窗两手数验到信号
  - [x] 报告如实记下一次**假发现**：我以为 `enabled` 在 `--once` 路径是洞并已在 `run_loop`
        加了守卫，读到 `_execute` 才发现早已有守卫（注释写着同一条洞）；我的冗余守卫还会
        连 `quote_log`、峰值刷新、心跳轮次和"被拦意图"留痕一起吞掉 ⇒ 已回滚，
        并把这条假设改写成**证明它不成立**的测试

---

## Batch 16 跟进 · day-1 P0 复核（Qoder 自主迭代 2026-11-04，无新批次下发）

主 AI 修的两个 P0（`d1a487d`）没有回归测试，且同一条分支上还有第三处同类漏项：

- [x] 补 `tests/test_m3_day1_p0s.py` 7 例：入队即 `mark_order`（防重灌）、
      rq 分支不碰账本、`rq-nav` 让位要看 `nav_history`（RunDaily 不留 op_log 的形状）、
      让位检查 fail-open 且必须只读
- [x] **复核照出第三处并修**：rq 分支只 `mark_order` 不 `bump_budget` ⇒
      `max_orders_per_day/max_turnover_per_day` 在 rqalpha 底座下形同虚设
      （条件单分支一直在加，两路径共用同一个计数器）；补一行 + 两条"限额真的挡单"测试
- [x] T60 可证伪判据验证**成立**：`daily_bars_raw` 11-03 全池 40/40、连续 7 日无逐标的滞后；
      现网 `m3 verify` 绿 9 / 警 3 / 红 0，三个 warn 逐个核对均非失真
- [x] T62 工具第一次真用就点名 11-04 的 18 条重复意图（`intent_ledger_conflict`），
      与 `d1a487d` 成因互相印证；顺便确认 11-03 上报的 `t1_unfreeze_overdue` 已自行解除
- [ ] 待拍板 3 件（questions.md 2026-11-04）：同轮内预算不重读（两路径共有，收紧会改行为）、
      T62 分支要不要合、18+2 条冲突意图留还是清
- 全量 **745 passed / 9 skipped**；门禁 断层：无；生产只读、账本 md5 与 crontab 未变
- [x] T69（Hermes 审查）三条建议全部回执：让位检查 fail-open 补 warn、LIKE 前缀口径复核、
      顺手把同形的"冷却时间戳读失败静默放行"也改成 warn（那道闸正是 11-04 P0 的唯一防线）

