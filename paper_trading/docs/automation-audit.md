# 自动化盘点与决策记录（Automation Audit）

> 本仓的连续性产物。仓内没有 handoff.md / iteration-log.md，本文档记录
> **还有哪些步骤是手工的 + 为什么决定不自动化**，供下一个 agent 接手。
> 不是进度真相源——进度看 `docs/ROADMAP.md`。

- 最后更新：2026-10-23
- 当前里程碑：M1 已验收（2026-10-23）；Pi 本机 `engine.local.yaml` 开启引擎；tag `v0.6.9` 已发布（发布版 `config.yaml` 默认仍 `enabled: false`）

---

## 1. 已机器化的项（本仓现状）

| 项 | 机制 | 位置 |
|----|------|------|
| 撮合口径只许有一份 | 断言代码 dataclass 默认值 == config.yaml，分叉即 CI 红 | `tests/test_caliber_consistency.py` |
| 同标的止损后不当日回补（离场优先） | 单测锁定买入条件作废+拒因 | `tests/test_engine_cond.py` |
| 每日收盘 NAV 一条（无交易也记） | 单测锁定 `nav:close` 幂等 | `tests/test_agent.py` |
| 交易日历未覆盖即 fail-closed | 单测锁定 tick/引擎/事件全部跳过 | `tests/test_runtime.py` |
| 事件复盘不占全局下单锁 | 源码/行为断言（止损不被复盘 LLM 推迟） | `tests/test_lock_isolation.py` |
| 残留运行锁自动接管 | 单测锁定（SIGKILL/PID 复用） | `tests/test_run_lock.py` |
| 第三方抓取硬超时 | 单测锁定（akshare 日线/日历 stall） | `tests/test_data_timeout.py` |
| Pi 日志轮转 | logrotate 配置（daily+5M+14 份+copytruncate） | `deploy/logrotate.paper-trading` |
| 面板写操作鉴权收口 | 改 Key/base_url 需口令；Origin 校验；口令常量时间比较 | `tests/test_dashboard.py` |
| 回测-实盘口径一致 | 对照测试锁定成交/成交日/价/T+1 一致 | `tests/test_m1_correctness.py` |
| Key 不进错误信息 / 掩码回显 | 单测锁定 | `tests/test_llm.py` |
| 对外 JSON 严格合法（无 Infinity/NaN） | 单测锁定 | `tests/test_json_safety.py` |
| 无未来函数 / 分钟线诚实边界 | 单测锁定 | `tests/test_engine_intraday.py` |
| CI 跑全量 pytest | push / PR 触发 | `.github/workflows/test.yml` |

`tests/test_caliber_consistency.py` 锁的是最高危的一处：`Backtester.__init__`
的 `self.fee = fee or FeeModel()`——调用方不传 fee 时（`optimize.py`、直接构造）
会静默吃掉 `FeeModel` 的硬编码默认值，而这套值从不读 config.yaml。断言保证
代码默认值一旦和 config.yaml 分叉，CI 立刻变红，不再靠人的自觉。

---

## 2. 仍是手工的步骤 + 为什么不做

以下步骤当前**靠人**，逐条给出"为什么没自动化"。判断标准来自审计 prompt：
需要人的判断、或自动化会掩盖问题、或给用户设备加常驻任务 → 不该自动化。

### 2.1 发 tag（母项目唯一消费接口）
- **现状**：手工 `git tag v0.6.x && git push --tags`。母项目 `git subtree pull --prefix=paper_trading paper-upstream <tag>` 只跟 tag。
- **为什么不自动化**：审计明令"不自动打 tag"。tag 是发布决策，需人确认
  M1 门禁 / Pi 验收已过；自动打 tag 会把未验收的 commit 变成母项目可消费的接口。
  **2026-10-23**：M1 验收通过后已发 `v0.6.7`（观察日全绿 + Pi 217 项验收；开关走 Pi 本机
  `engine.local.yaml`，发布版 `config.yaml` 保持 `enabled: false`，母项目拉取不会自动开引擎）。
  **2026-10-25/26**：Issue #2 修复 + 审计加固后发 `v0.6.8`（本地/Pi 235 项全绿），母项目需
  `subtree pull v0.6.8` 解锁 gate；剩余审计项收口后发 `v0.6.9`（本地 242 项全绿）。

### 2.2 ROADMAP.md 勾选与进度记录
- **现状**：每次迭代手工勾复选框 + 补一行「进度记录」。
- **为什么不自动化**：勾选代表"验收通过"这一判断，需人对证据负责；
  自动勾选会掩盖未完成项，违反"M1 全部验收通过前不开引擎"的门禁本意。

### 2.3 crontab 安装（Pi）
- **现状**：手工往 Pi 的 crontab 加 5 条（tick / event / engine / calendar），
  行写死在 `AGENTS.md` 的 Cron Integration 段。
- **为什么不自动化**：硬约束明令"不往用户设备加新的 cron / systemd timer"
  （交易系统自身既有 crontab 除外）。写一个"安装脚本"等于往设备塞新的常驻入口。
- **风险**：cron 行与 `utils/market_time.py` 的运行时窗口靠人同步。v0.6.5 改过
  运行时后需手动对齐 crontab（已在 ROADMAP 进度记录留痕）。

### 2.4 Pi 部署
- **现状**：Pi 上 `~/paper-trading` 是 git clone，只允许 `git pull`，
  每次发版后手工 pull + 跑 `pytest` 验收。
- **为什么不自动化**：硬约束明令"禁止 scp/rsync"（会破坏 LF 换行 + 绕过历史）。
  CI 若自动 ssh 到 Pi 部署 = 引入网络攻击面 + 绕过 M1 的人工验收门禁。

### 2.5 合规表（17 条 A 股规则）与代码口径一致性
- **现状**：`AGENTS.md` 的 17 条合规表靠人维护。其中**费率类**（第 7/8/9 条）
  已被 `test_caliber_consistency.py` 机器锁定；涨跌幅档（第 4/5/6 条）由
  `PaperBroker.limit_pct_for` 单点实现 + 单测覆盖。
- **剩余手工部分**：表格里"已建模 / 简化"的状态标注、以及第 12–17 条的
  简化口径说明——这些是**给人看的承诺文档**，不是可执行断言，无法机器校验。
- **为什么不进一步自动化**：把整张自然语言表变成解析断言 = 过度reach，
  且表的措辞是给人读的契约，强行结构化会改变其可读性。

### 2.6 CI 缺 lint / 类型检查 / 覆盖率
- **现状**：CI 只跑 `pytest tests/ -q`。
- **为什么不加**：审计明令"不加 lint、类型检查、覆盖率、多 Python 版本矩阵"。
  （技术注：全量 ruff 有 468 项，多为风格类；bug-finder 规则集 E9/F63/F7/F82
  当前 0 命中。若将来要加，从这套最小规则集起步，勿上全量。）

### 2.7 Key 的自动泄露扫描
- **现状**："Key 三不"靠单测锁定（错误信息、掩码回显），但**没有**扫描
  git 追踪面 / 日志的自动化。
- **为什么不自动化**：本次审计目标已收窄为"口径单源"一件事，不做第二件。
  静态正则扫真实 Key（`sk-[A-Za-z0-9]{20,}`）对本仓现有追踪文件零命中，
  是一项低成本候选——但**尚未实现**，留作下一个候选，不在本次范围。

---

## 3. 母项目接口备注

本次已落地的改动**不涉及**母项目消费接口：

- 未改 `paper_trading/integration/`（只读契约）
- 未改 CLI `--json` 输出结构
- 未改 tag 语义
- `tests/test_caliber_consistency.py` 是纯测试文件，母项目 subtree 不消费 tests/

---

## 4. 下一个候选（未做，供参考）

按「收益/成本」排序，均未实现，需先与用户确认目标再动手：

1. **Key 泄露静态扫描**（§2.7）：低成本，扫追踪文件里的 `sk-...` 模式。
2. **cron 行与 market_time.py 的一致性校验**（§2.3 风险）：只读校验，不装 cron。
3. ~~回测 `trades_detail` rule id 互换（ROADMAP 待办）~~ —— **已结（2026-10-23）**：
   非展示层 bug，是方向型规则语义（`ma_cross` 由信号定方向，`action.side` 只作默认）。
   Pi 旧规则把金叉/死叉拆两条会双向重复触发，已合并为每标的一条并写进文档口径。
